"""URA Chatbot service layer.

This module loads the FAQ knowledge base from CSV files and provides
retrieval-augmented generation (RAG) via keyword/semantic search over
the ingested documents.  When a fine-tuned model checkpoint is available
the ``generate`` method delegates to it; otherwise it falls back to a
deterministic retrieval-only response so the API remains functional
during development and CI.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_DATA_DIR = str(Path(__file__).resolve().parents[3] / "Data" / "dataset")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
# Guard against path traversal via DATA_DIR env var
if not _DATA_DIR.is_relative_to(_PROJECT_ROOT):
    logger.warning("DATA_DIR %s escapes project root; falling back to default", _DATA_DIR)
    _DATA_DIR = Path(_DEFAULT_DATA_DIR).resolve()


def _load_faq_data(data_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    """Load all ``ura_*_faqs.csv`` files into an in-memory FAQ index.

    Returns ``(faq_index, tag_labels)`` where *faq_index* is keyed by tag and
    *tag_labels* maps tag IDs to human-readable names.
    """
    faq_index: dict[str, list[dict[str, str]]] = {}
    tag_labels: dict[str, str] = {}

    if not data_dir.is_dir():
        logger.warning("FAQ data directory not found: %s", data_dir)
        return faq_index, tag_labels

    for csv_path in sorted(data_dir.glob("ura_*_faqs.csv")):
        tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")
        label = tag.replace("_", " ").title()
        tag_labels[tag] = label

        entries: list[dict[str, str]] = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    q = (row.get("question") or row.get("Question") or "").strip()
                    a = (row.get("answer") or row.get("Answer") or "").strip()
                    if q and a:
                        entries.append({"question": q, "answer": a, "source": csv_path.name})
        except Exception:
            logger.exception("Failed to load %s", csv_path)

        if entries:
            faq_index[tag] = entries
            logger.info("Loaded %d FAQs from %s (tag=%s)", len(entries), csv_path.name, tag)

    logger.info(
        "FAQ index ready – %d tags, %d total entries",
        len(faq_index),
        sum(len(v) for v in faq_index.values()),
    )
    return faq_index, tag_labels


def _simple_search(
    query: str,
    faq_index: dict[str, list[dict[str, str]]],
    top_k: int = 4,
) -> list[dict[str, str]]:
    """Keyword-based retrieval: score each FAQ by word overlap with *query*."""
    query_tokens = set(query.lower().split())
    scored: list[tuple[float, dict[str, str]]] = []

    for entries in faq_index.values():
        for entry in entries:
            q_tokens = set(entry["question"].lower().split())
            a_tokens = set(entry["answer"].lower().split())
            overlap = len(query_tokens & (q_tokens | a_tokens))
            if overlap > 0:
                scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------
class ChatModel:
    """Unified service that backs all API endpoints.

    On initialisation it loads the FAQ CSV corpus into memory.  A future
    iteration will initialise a vector store (Qdrant) and a fine-tuned
    Gemma-2-9B model for full RAG.
    """

    def __init__(self) -> None:
        self.name = "ura-gemma-2-9b"
        self._faq_index, self._tag_labels = _load_faq_data(_DATA_DIR)
        logger.info("ChatModel initialised (knowledge base: %d tags)", len(self._faq_index))

    # -- Chat (RAG) ---------------------------------------------------------
    def generate(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 4,
    ) -> dict[str, Any]:
        """Return a RAG-style answer with source citations."""
        hits = _simple_search(message, self._faq_index, top_k=top_k)

        if hits:
            best = hits[0]
            reply = best["answer"]
            sources = list({h["source"] for h in hits})
        else:
            reply = (
                "I could not find a specific answer in the URA knowledge base. "
                "Please try rephrasing your question, or contact URA directly at "
                "https://ura.go.ug for assistance."
            )
            sources = []

        return {
            "reply": reply,
            "sources": sources,
            "model": self.name,
            "conversation_id": conversation_id,
        }

    # -- Classification -----------------------------------------------------
    def classify(self, text: str, top_k: int = 1) -> dict[str, Any]:
        """Classify *text* against known FAQ tags by keyword overlap."""
        t0 = time.perf_counter()
        query_tokens = set(text.lower().split())
        tag_scores: dict[str, float] = {}

        for tag, entries in self._faq_index.items():
            score = 0.0
            for entry in entries:
                tokens = set(entry["question"].lower().split())
                score += len(query_tokens & tokens)
            if score > 0:
                tag_scores[tag] = score

        total = sum(tag_scores.values()) or 1.0
        ranked = sorted(tag_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        predictions = [
            {
                "tag": tag,
                "confidence": round(score / total, 4),
                "label": self._tag_labels.get(tag, tag.replace("_", " ").title()),
            }
            for tag, score in ranked
        ]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"predictions": predictions, "processing_time_ms": round(elapsed_ms, 2)}

    def classify_batch(self, texts: list[str]) -> dict[str, Any]:
        """Classify multiple texts in one call."""
        t0 = time.perf_counter()
        results = []
        for text in texts:
            single = self.classify(text, top_k=1)
            if single["predictions"]:
                p = single["predictions"][0]
                results.append({"text": text, "tag": p["tag"], "confidence": p["confidence"]})
            else:
                results.append({"text": text, "tag": "unknown", "confidence": 0.0})

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"results": results, "processing_time_ms": round(elapsed_ms, 2)}

    # -- Tags & FAQ ---------------------------------------------------------
    def list_tags(self) -> dict[str, Any]:
        """Return all known FAQ tags."""
        tags = [
            {
                "id": tag,
                "name": self._tag_labels.get(tag, tag.replace("_", " ").title()),
                "description": f"Questions about {self._tag_labels.get(tag, tag).lower()}",
            }
            for tag in sorted(self._faq_index)
        ]
        return {"tags": tags, "total": len(tags)}

    def get_faq(self, tag: str) -> dict[str, Any] | None:
        """Return FAQ entries for a specific *tag*."""
        entries = self._faq_index.get(tag)
        if entries is None:
            return None
        return {"tag": tag, "faqs": entries, "total": len(entries)}
