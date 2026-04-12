"""Loaders for every data source feeding the pipeline.

Each loader is a generator that yields :class:`TrainingExample` instances.
The pipeline consumes them lazily so memory stays bounded.

Source policy (2026 production rules):

- **CSV FAQs**  — gold data, treated as first-class QA examples.
- **PDFs**      — *never* pseudo-QA'd by picking the first sentence of a
                  chunk (the old bug). They are either:
                  (a) raw corpus passages for continued pre-training, or
                  (b) fed into ``teacher_qa_generation.py`` for synthetic
                      QA. This loader produces (a).
- **Luganda**   — properly categorised as a *translation* task, not QA.
                  Output language is set to ``lg``. Monolingual Luganda
                  rows become corpus examples.
- **Teacher QA** — loaded as QA examples, stripped of the fragile Gemma
                  chat template (we delegate templating to the trainer).
- **Refusals**  — curated safety examples from ``schema.REFUSAL_TEMPLATES``.
- **Retrieval** — built from PDF chunks; trains the model to answer from
                  a retrieved context and cite the source heading.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterator, Optional

from ml.scripts.data_aug.chunkers import Chunk, chunk_pdf
from ml.scripts.data_aug.schema import (
    REFUSAL_TEMPLATES,
    URA_SYSTEM_PROMPT,
    Message,
    Metadata,
    SourceType,
    TaskType,
    TrainingExample,
    content_hash,
)
from ml.scripts.data_aug.text_utils import clean_text, redact_pii

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_example(
    *,
    user: str,
    assistant: str,
    source: str,
    source_type: SourceType,
    task: TaskType,
    language: str = "en",
    tag: Optional[str] = None,
    chunk_id: Optional[int] = None,
    doc_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    system: Optional[str] = URA_SYSTEM_PROMPT,
    grounding_score: Optional[float] = None,
    is_synthetic: Optional[bool] = None,
) -> Optional[TrainingExample]:
    """Build a validated TrainingExample. Returns None if redaction left the
    example empty or trivially short."""
    user_clean, _ = redact_pii(clean_text(user))
    assistant_clean, _ = redact_pii(clean_text(assistant))

    if len(user_clean) < 4 or len(assistant_clean) < 4:
        return None

    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", content=system))
    messages.append(Message(role="user", content=user_clean))
    messages.append(Message(role="assistant", content=assistant_clean))

    # Phase 2 — default synthetic flag based on source_type. Callers can
    # override via is_synthetic=. Teacher QA / PDF-QA are synthetic by
    # construction; CSV FAQs / refusals / Luganda parallel are real.
    if is_synthetic is None:
        is_synthetic = source_type in {
            SourceType.TEACHER_QA,
            SourceType.PDF_QA,
        }

    try:
        return TrainingExample(
            messages=messages,
            metadata=Metadata(
                source=source,
                source_type=source_type,
                task=task,
                language=language,
                tag=tag,
                content_hash=content_hash(user_clean, assistant_clean),
                chunk_id=chunk_id,
                doc_id=doc_id,
                extra=extra or {},
                grounding_score=grounding_score,
                is_synthetic=is_synthetic,
            ),
        )
    except Exception as exc:  # Pydantic validation
        log.debug("Dropping example from %s: %s", source, exc)
        return None


# ---------------------------------------------------------------------------
# CSV FAQs
# ---------------------------------------------------------------------------


def load_csv_faqs(csv_dir: Path) -> Iterator[TrainingExample]:
    """Load every ``*.csv`` under ``csv_dir`` as QA examples.

    Column detection is loose: any column whose lowercase name contains
    ``question`` / ``answer`` wins. Files that can't be parsed are skipped
    with a warning (never crash the pipeline).
    """
    import pandas as pd

    csv_files = sorted(csv_dir.glob("ura_*_faqs.csv"))
    if not csv_files:
        csv_files = sorted(csv_dir.glob("*.csv"))

    for csv_path in csv_files:
        try:
            df = pd.read_csv(csv_path, encoding="utf-8")
        except UnicodeDecodeError:
            try:
                df = pd.read_csv(csv_path, encoding="utf-8-sig")
            except Exception:
                try:
                    df = pd.read_csv(csv_path, encoding="latin-1")
                except Exception as exc:
                    log.warning("Failed to read %s: %s", csv_path.name, exc)
                    continue
        except Exception as exc:
            log.warning("Failed to read %s: %s", csv_path.name, exc)
            continue

        df.columns = [str(c).strip().lower() for c in df.columns]
        q_col = next((c for c in df.columns if "question" in c), None)
        a_col = next((c for c in df.columns if "answer" in c), None)
        if q_col is None or a_col is None:
            log.warning("Skipping %s: no question/answer columns", csv_path.name)
            continue

        tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")
        count = 0
        for _, row in df.iterrows():
            q = str(row.get(q_col, "") or "")
            a = str(row.get(a_col, "") or "")
            ex = _make_example(
                user=q,
                assistant=a,
                source=csv_path.name,
                source_type=SourceType.CSV_FAQ,
                task=TaskType.QA,
                tag=tag,
            )
            if ex is not None:
                yield ex
                count += 1
        log.info("csv_faq: %s → %d examples", csv_path.name, count)


# ---------------------------------------------------------------------------
# PDF corpus
# ---------------------------------------------------------------------------
#
# The old pipeline emitted a fake "Summarize this" instruction whose answer
# was the first sentence of the chunk — which teaches the model to echo.
# The 2026 replacement has two modes, selected by ``as_corpus``:
#
#  * as_corpus=True  (default) → one assistant-only message with the raw
#    passage and the heading trail. Used for continued pre-training style
#    objectives and for retrieval training.
#  * as_corpus=False           → no examples are produced. Use this mode
#    when the downstream fine-tune only consumes QA data. PDFs should then
#    be pumped through ``teacher_qa_generation.py`` instead.


def _chunk_worker(pdf_path_str: str) -> list[dict[str, Any]]:
    """Run in a subprocess to isolate pymupdf crashes from the main worker."""
    path = Path(pdf_path_str)
    out: list[dict[str, Any]] = []
    for chunk in chunk_pdf(path):
        out.append(
            {
                "text": chunk.text,
                "doc_id": chunk.doc_id,
                "source": chunk.source,
                "chunk_id": chunk.chunk_id,
                "heading_trail": chunk.heading_trail,
            }
        )
    return out


def load_pdf_chunks(
    pdf_dir: Path,
    *,
    max_pdfs: Optional[int] = None,
    workers: int = 1,
    as_corpus: bool = True,
    emit_retrieval: bool = True,
) -> Iterator[TrainingExample]:
    """Yield passages from PDFs.

    If ``emit_retrieval=True`` each chunk is also duplicated as a
    retrieval-format example: the user turn asks a templated question with
    the chunk text as context, and the assistant turn is an extractive
    answer + source citation. This teaches the model to cite — which the
    serving-time RAG stack relies on.
    """
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if max_pdfs:
        pdf_files = pdf_files[:max_pdfs]
    if not pdf_files:
        return

    chunk_iter: Iterator[list[dict[str, Any]]]
    if workers > 1:
        # Bounded parallelism; isolate pymupdf crashes.
        def _run_parallel() -> Iterator[list[dict[str, Any]]]:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_chunk_worker, str(p)): p for p in pdf_files}
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        yield fut.result()
                    except Exception as exc:
                        log.warning("pdf chunk worker failed for %s: %s", p.name, exc)
                        yield []

        chunk_iter = _run_parallel()
    else:
        chunk_iter = (_chunk_worker(str(p)) for p in pdf_files)

    for per_doc in chunk_iter:
        for chunk_dict in per_doc:
            chunk = Chunk(
                text=chunk_dict["text"],
                doc_id=chunk_dict["doc_id"],
                source=chunk_dict["source"],
                chunk_id=chunk_dict["chunk_id"],
                heading_trail=chunk_dict["heading_trail"],
            )
            if as_corpus:
                yield from _chunk_to_corpus_example(chunk)
            if emit_retrieval:
                yield from _chunk_to_retrieval_example(chunk)


def _chunk_to_corpus_example(chunk: Chunk) -> Iterator[TrainingExample]:
    """One example per chunk: user asks a 'tell me about section X' query,
    assistant responds with the literal passage. Teaches grounding."""
    crumb = " > ".join(chunk.heading_trail[-2:]) if chunk.heading_trail else ""
    if crumb:
        user = f"Quote the section '{crumb}' from the {chunk.doc_id} document."
    else:
        user = f"Quote the relevant passage from the {chunk.doc_id} document."
    ex = _make_example(
        user=user,
        assistant=chunk.text,
        source=chunk.source,
        source_type=SourceType.PDF_CORPUS,
        task=TaskType.CORPUS,
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        extra={"heading_trail": chunk.heading_trail},
    )
    if ex is not None:
        yield ex


def _chunk_to_retrieval_example(chunk: Chunk) -> Iterator[TrainingExample]:
    """Retrieval-style example.

    The user turn supplies the chunk as CONTEXT and asks an extractive
    question. The assistant turn cites the heading trail. This is the
    pattern the deployed RAG stack uses, so the model sees it at training
    time.
    """
    crumb = " > ".join(chunk.heading_trail) if chunk.heading_trail else chunk.doc_id
    # Use the chunk text itself as both context and evidence. We keep the
    # question generic — we don't pretend to know the answer. The trainer
    # learns the *format*, not a specific fact. True semantic QA should
    # come from teacher_qa_generation.py.
    user = (
        "Use the CONTEXT below to answer the question. "
        "Cite the source section when you answer.\n\n"
        f"CONTEXT:\n{chunk.text}\n\n"
        "QUESTION: Summarise the key point of this passage and cite the "
        "source."
    )
    first_sentence = re.split(r"(?<=[.!?])\s+", chunk.text, maxsplit=1)[0][:400]
    assistant = f"{first_sentence}\n\nSource: {chunk.doc_id} — {crumb}"
    ex = _make_example(
        user=user,
        assistant=assistant,
        source=chunk.source,
        source_type=SourceType.RETRIEVAL,
        task=TaskType.RETRIEVAL_QA,
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        extra={"heading_trail": chunk.heading_trail},
    )
    if ex is not None:
        yield ex


# ---------------------------------------------------------------------------
# Luganda
# ---------------------------------------------------------------------------


def _load_luganda_jsonl(path: Path) -> Iterator[TrainingExample]:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                en = rec.get("english") or rec.get("en") or rec.get("question", "")
                lg = rec.get("luganda") or rec.get("lg") or rec.get("answer", "")
                if en and lg:
                    ex = _make_example(
                        user=f"Translate to Luganda: {en.strip()}",
                        assistant=str(lg).strip(),
                        source=path.name,
                        source_type=SourceType.LUGANDA_PARALLEL,
                        task=TaskType.TRANSLATION,
                        language="lg",
                    )
                    if ex is not None:
                        yield ex
    except Exception as exc:
        log.warning("Failed to read Luganda JSONL %s: %s", path.name, exc)


def _load_luganda_csv(path: Path) -> Iterator[TrainingExample]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                en = (
                    row.get("english")
                    or row.get("en")
                    or row.get("English")
                    or row.get("English Text", "")
                )
                lg = (
                    row.get("luganda")
                    or row.get("lg")
                    or row.get("Luganda")
                    or row.get("Luganda Text", "")
                )
                if en and lg:
                    ex = _make_example(
                        user=f"Translate to Luganda: {str(en).strip()}",
                        assistant=str(lg).strip(),
                        source=path.name,
                        source_type=SourceType.LUGANDA_PARALLEL,
                        task=TaskType.TRANSLATION,
                        language="lg",
                    )
                    if ex is not None:
                        yield ex
    except Exception as exc:
        log.warning("Failed to read Luganda CSV %s: %s", path.name, exc)


def _load_luganda_tab_txt(path: Path) -> Iterator[TrainingExample]:
    """Handle the tab-separated eng.lug.txt file (needs ftfy for mojibake)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
            # Skip header if present
            if "english" not in first.lower():
                f.seek(0)
            for line in f:
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                en, lg = parts[0].strip(), parts[1].strip()
                if en and lg:
                    ex = _make_example(
                        user=f"Translate to Luganda: {en}",
                        assistant=lg,
                        source=path.name,
                        source_type=SourceType.LUGANDA_PARALLEL,
                        task=TaskType.TRANSLATION,
                        language="lg",
                    )
                    if ex is not None:
                        yield ex
    except Exception as exc:
        log.warning("Failed to read Luganda TXT %s: %s", path.name, exc)


def load_luganda_data(luganda_dir: Path) -> Iterator[TrainingExample]:
    """Load every Luganda file in the directory as translation examples."""
    count = 0
    for path in sorted(luganda_dir.glob("*.jsonl")):
        for ex in _load_luganda_jsonl(path):
            yield ex
            count += 1
    for path in sorted(luganda_dir.glob("*.csv")):
        for ex in _load_luganda_csv(path):
            yield ex
            count += 1
    for path in sorted(luganda_dir.glob("*.txt")):
        for ex in _load_luganda_tab_txt(path):
            yield ex
            count += 1
    log.info("luganda: %d translation examples", count)


# ---------------------------------------------------------------------------
# Teacher QA (synthetic pairs already generated by teacher_qa_generation.py)
# ---------------------------------------------------------------------------


def load_teacher_qa(search_dirs: list[Path]) -> Iterator[TrainingExample]:
    """Load teacher-generated QA pairs from whatever location exists.

    Accepts the three formats emitted by ``teacher_qa_generation.py``:
      - ``teacher_qa.jsonl`` (``{question, answer, chunk_text, ...}``)
      - ``teacher_qa_instruction.jsonl`` (Alpaca-style)
      - ``teacher_qa_gemma.jsonl`` (raw chat-template text — re-parsed back
        to messages to avoid locking in the Gemma template)
    """
    names = (
        "teacher_qa.jsonl",
        "teacher_qa_instruction.jsonl",
        "teacher_qa_gemma.jsonl",
    )
    seen_paths: set[Path] = set()
    found_any = False
    for root in search_dirs:
        for name in names:
            path = root / name
            if not path.exists() or path in seen_paths:
                continue
            seen_paths.add(path)
            found_any = True
            count = 0
            for ex in _parse_teacher_file(path):
                yield ex
                count += 1
            log.info("teacher_qa: %s → %d examples", path.name, count)
    if not found_any:
        log.info("teacher_qa: no files found in %s", [str(d) for d in search_dirs])


_GEMMA_USER_RE = re.compile(
    r"<start_of_turn>user\s*(.*?)<end_of_turn>", re.DOTALL
)
_GEMMA_MODEL_RE = re.compile(
    r"<start_of_turn>model\s*(.*?)<end_of_turn>", re.DOTALL
)


def _parse_teacher_file(path: Path) -> Iterator[TrainingExample]:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_checkpoint_meta"):
                    continue

                source = rec.get("source_pdf") or rec.get("source") or path.name

                # Phase 2 — teacher grounding score is propagated from
                # teacher_qa_generation.filter_by_grounding so downstream
                # quality filtering and per-row audit can see it.
                grounding = rec.get("grounding_score")

                if "question" in rec and "answer" in rec:
                    ex = _make_example(
                        user=str(rec["question"]),
                        assistant=str(rec["answer"]),
                        source=source,
                        source_type=SourceType.TEACHER_QA,
                        task=TaskType.QA,
                        tag=rec.get("type"),
                        grounding_score=grounding,
                        is_synthetic=True,
                    )
                    if ex is not None:
                        yield ex
                    continue

                if "instruction" in rec and "output" in rec:
                    instr = str(rec["instruction"])
                    ctx = str(rec.get("input", "") or "").strip()
                    user = f"{instr}\n\nContext:\n{ctx}" if ctx else instr
                    ex = _make_example(
                        user=user,
                        assistant=str(rec["output"]),
                        source=source,
                        source_type=SourceType.TEACHER_QA,
                        task=TaskType.QA,
                        tag=rec.get("type"),
                        grounding_score=grounding,
                        is_synthetic=True,
                    )
                    if ex is not None:
                        yield ex
                    continue

                if "text" in rec:
                    text = rec["text"]
                    u = _GEMMA_USER_RE.search(text)
                    m = _GEMMA_MODEL_RE.search(text)
                    if u and m:
                        ex = _make_example(
                            user=u.group(1).strip(),
                            assistant=m.group(1).strip(),
                            source=source,
                            source_type=SourceType.TEACHER_QA,
                            task=TaskType.QA,
                            tag=rec.get("type"),
                            grounding_score=grounding,
                            is_synthetic=True,
                        )
                        if ex is not None:
                            yield ex
    except Exception as exc:
        log.warning("Failed to parse %s: %s", path.name, exc)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def load_refusal_examples() -> Iterator[TrainingExample]:
    """Yield curated refusal / safety examples from the schema module."""
    for question, answer in REFUSAL_TEMPLATES:
        ex = _make_example(
            user=question,
            assistant=answer,
            source="curated:refusals",
            source_type=SourceType.REFUSAL,
            task=TaskType.REFUSAL,
            tag="safety",
        )
        if ex is not None:
            yield ex
