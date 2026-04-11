#!/usr/bin/env python3
"""
Teacher Model QA Generation Script
Uses Qwen2.5-7B-Instruct as a "Teacher" to generate high-quality QA pairs from PDF chunks
for fine-tuning smaller student models (Gemma-2B, Llama-1B).

Synthetic data generation pipeline:
  1. Extract & sentence-aware chunk PDFs (pymupdf4llm)
  2. Generate QA pairs via teacher LLM with structured chat templates
  3. Quality-filter and deduplicate
  4. Export as JSONL (additional formats: gemma, instruction, qwen via --formats)

Usage:
    # Full run on 2 GPUs
    CUDA_VISIBLE_DEVICES=1,2 python ml/scripts/teacher_qa_generation.py

    # Quick test (3 PDFs, 2 chunks)
    python ml/scripts/teacher_qa_generation.py --max-pdfs 3 --chunks 2

    # Resume interrupted run
    python ml/scripts/teacher_qa_generation.py --resume
"""

import argparse
import gc
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import torch
from tqdm import tqdm

try:
    import pymupdf.layout
    import pymupdf4llm
    from pymupdf4llm import to_markdown
except ImportError:
    pymupdf4llm = None
    to_markdown = None
    print("Warning: pymupdf4llm not installed. Run: pip install pymupdf4llm")

try:
    from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
except ImportError:
    print("Error: transformers not installed. Run: pip install transformers accelerate")
    raise SystemExit(1)


# =============================================================================
# Platform detection — pin GPU visibility BEFORE any CUDA context is created
# =============================================================================

ON_KAGGLE = os.path.exists("/kaggle")

# Set CUDA_VISIBLE_DEVICES before torch creates a CUDA context so that
# every operation in this script — PDF loading, chunking, and inference —
# runs on the specified GPUs.  Local default: GPUs 1 and 2.
# Kaggle: leave unset so all T4s are available.
# A CLI --gpu-ids arg (parsed later in main()) can still override this.
if not ON_KAGGLE and not os.environ.get("CUDA_VISIBLE_DEVICES"):
    os.environ["CUDA_VISIBLE_DEVICES"] = "1,2"

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

print("Platform:", "Kaggle" if ON_KAGGLE else "Local")
print("Device:", DEVICE)
print("GPUs visible:", os.environ.get("CUDA_VISIBLE_DEVICES", "all"))


def setup_kaggle_paths() -> dict:
    """Download dataset from Kaggle Hub and return resolved path dict."""
    try:
        import kagglehub
    except ImportError:
        raise SystemExit(
            "kagglehub not installed. Run: pip install kagglehub"
        )

    base_path = kagglehub.dataset_download("mpairwelauben/ura-tax-data-v2")
    dataset_root = os.path.join(base_path, "dataset")

    paths = {
        "pdfs":      os.path.join(dataset_root, "pdfs"),
        "luganda":   os.path.join(dataset_root, "TTT"),
        "artifacts": "/kaggle/working/artifacts",
        "cache":     "/kaggle/working/models",
    }

    os.makedirs(paths["artifacts"], exist_ok=True)
    os.makedirs(paths["cache"], exist_ok=True)

    for k, v in paths.items():
        print(f"  {k}: {v} -> {'OK' if os.path.exists(v) else 'MISSING'}")

    return paths


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_ROOT = PROJECT_ROOT / "Data"

if ON_KAGGLE:
    _PATHS = setup_kaggle_paths()
    PDF_DIR      = Path(_PATHS["pdfs"])
    LUGANDA_DIR  = Path(_PATHS["luganda"])
    ARTIFACTS_DIR = Path(_PATHS["artifacts"])
    CACHE_DIR    = _PATHS["cache"]
else:
    PDF_DIR       = DATA_ROOT / "pdfs"
    LUGANDA_DIR   = DATA_ROOT / "TTT"
    ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
    # Use the existing HF hub cache so already-downloaded models are reused
    CACHE_DIR     = os.path.join(
        os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface")), "hub"
    )
os.makedirs(str(ARTIFACTS_DIR), exist_ok=True)

TEACHER_MODEL = "Qwen/Qwen2.5-7B-Instruct"
FALLBACK_MODEL = "Qwen/Qwen2.5-3B-Instruct"

# GPU selection — reflects what was pinned above at module init
DEFAULT_GPU_IDS = os.environ.get("CUDA_VISIBLE_DEVICES") or None

MAX_NEW_TOKENS = 150  # Reduced for QA generation
TEMPERATURE = 0.7
TOP_P = 0.9
SEED = 42

DEFAULT_QUESTIONS_PER_CHUNK = 3
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50

# GPU OPTIMIZED BATCH SIZES
# T4 GPUs: 15GB each. Qwen2.5-7B 4-bit: ~4GB, Qwen2.5-3B: ~2GB
BATCH_SIZE_PER_GPU = 8  # Texts per GPU
TOTAL_BATCH_SIZE = BATCH_SIZE_PER_GPU * 2  # For 2 GPUs

# Disable tokenizer parallelism warning
os.environ["TOKENIZERS_PARALLELISM"] = "false"


@dataclass
class GeneratedQA:
    question: str
    answer: str
    chunk_text: str
    source_pdf: str
    chunk_id: int
    question_type: str
    confidence: float = 1.0


# Quality thresholds
MIN_QUESTION_LENGTH = 15
MIN_ANSWER_LENGTH = 20
MAX_ANSWER_LENGTH = 2000


# =============================================================================
# Text Processing — sentence-aware chunking
# =============================================================================

def clean_text(text: str) -> str:
    """Clean and normalize extracted text."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s.,;:!?\'\"()\-–—/&%@#$+]', '', text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using regex-based boundary detection."""
    return re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks that respect sentence boundaries."""
    sentences = _split_sentences(text)
    chunks = []
    current_words: list[str] = []

    for sentence in sentences:
        words = sentence.split()
        current_words.extend(words)

        if len(current_words) >= chunk_size:
            chunk = ' '.join(current_words)
            if len(chunk) > 50:
                chunks.append(chunk)

            # Backtrack by overlap words for the next chunk, aligning to sentence start
            overlap_words = current_words[-overlap:] if overlap < len(current_words) else current_words
            current_words = list(overlap_words)

    # Final chunk
    if current_words:
        chunk = ' '.join(current_words)
        if len(chunk) > 50:
            chunks.append(chunk)

    return chunks


def load_pdfs(
    pdf_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_pdfs: Optional[int] = None,
) -> list[dict]:
    """Load and chunk all PDFs with GPU memory management and fallback extraction.

    Uses optimized pymupdf4llm settings for clean text extraction, filters
    short chunks, and reports per-file statistics.
    """
    if to_markdown is None:
        print("pymupdf4llm not installed — cannot extract PDFs")
        return []

    # Clear GPU memory on the pinned devices before heavy I/O
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gpus = os.environ.get("CUDA_VISIBLE_DEVICES", "all")
        print(f"  GPU memory cleared (CUDA_VISIBLE_DEVICES={gpus}, "
              f"{torch.cuda.device_count()} device(s))")

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return []
    if max_pdfs:
        pdf_files = pdf_files[:max_pdfs]

    print(f"Found {len(pdf_files)} PDF files")
    all_chunks: list[dict] = []

    for pdf_idx, pdf_path in enumerate(pdf_files, 1):
        try:
            print(f"[{pdf_idx}/{len(pdf_files)}] Processing: {pdf_path.name}")

            # Primary extraction: full layout-aware settings
            try:
                md = to_markdown(
                    pdf_path,
                    ignore_images=True,
                    ignore_graphics=True,
                    table_strategy="lines",
                    write_images=False,
                    page_separators="",
                    hdr_info=False,
                )
            except Exception:
                # Fallback to minimal settings on failure
                print(f"  Warning: Using fallback extraction for {pdf_path.name}")
                md = to_markdown(
                    pdf_path,
                    ignore_images=True,
                    ignore_graphics=True,
                    write_images=False,
                )

            # Handle both str and list[dict] return types across pymupdf4llm versions
            text_parts: list[str] = []
            if isinstance(md, list):
                for item in md:
                    if isinstance(item, dict):
                        t = item.get('text', '')
                    else:
                        t = str(item)
                    if t and t.strip():
                        text_parts.append(t.strip())
            elif isinstance(md, str) and md.strip():
                text_parts.append(md.strip())

            if not text_parts:
                print(f"  No text extracted from {pdf_path.name}")
                continue

            full_text = "\n\n".join(text_parts)
            cleaned = clean_text(full_text)
            chunks = chunk_text(cleaned, chunk_size, chunk_overlap)

            pdf_chunks: list[dict] = []
            for i, chunk in enumerate(chunks):
                word_count = len(chunk.split())
                if word_count >= 20:  # Skip fragments too short to be useful
                    pdf_chunks.append({
                        'text': chunk,
                        'source': pdf_path.name,
                        'chunk_id': i,
                        'total_chunks': len(chunks),
                        'word_count': word_count,
                        'language': 'en',
                    })

            all_chunks.extend(pdf_chunks)
            print(f"  ✓ Extracted {len(pdf_chunks)} chunks")

            # Release memory after each PDF
            gc.collect()

        except Exception as e:
            print(f"  ✗ Error processing {pdf_path.name}: {str(e)[:100]}")
            continue

    print(f"\n✓ Total chunks extracted: {len(all_chunks)}")
    if all_chunks:
        avg_words = sum(c['word_count'] for c in all_chunks) / len(all_chunks)
        print(f"✓ Average chunk size: {avg_words:.1f} words")

    return all_chunks


def batch_chunks(
    chunks: list[dict],
    batch_size: int = TOTAL_BATCH_SIZE,
) -> list[list[dict]]:
    """Group chunks into fixed-size batches for efficient GPU processing."""
    if not chunks:
        return []
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    print(f"Created {len(batches)} batches of size {batch_size}")
    return batches


def load_luganda_dataset(
    luganda_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """Load Luganda text from all sources in Data/TTT/ into chunks.

    Sources:
      - Luganda.csv                                      (English, Luganda)
      - makerere_luganda_monolingual_corpus.csv          (Sentences)
      - Luganda_Agriculture-specific_dataset-1.csv       (Sentences)
      - Makerere_Sentiment_corpus_Luganda_*.csv          (Luganda - Translation)
      - Multilingual Parallel Corpus.xlsx                (Luganda Translation)
      - luganda_wiki_corpus.txt                          (free text)
      - eng.lug.txt                                      (tab-separated en\\tlg)
      - *.pdf                                            (via pymupdf4llm)

    Every chunk dict carries language='lg' for prompt routing downstream.
    """
    try:
        import pandas as pd
    except ImportError:
        print("Warning: pandas not installed — CSV/XLSX Luganda sources skipped. "
              "Run: pip install pandas openpyxl")
        pd = None

    if not luganda_dir.exists():
        print(f"Luganda directory not found: {luganda_dir}")
        return []

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gpus = os.environ.get("CUDA_VISIBLE_DEVICES", "all")
        print(f"  GPU memory cleared (CUDA_VISIBLE_DEVICES={gpus}, "
              f"{torch.cuda.device_count()} device(s))")

    all_texts: list[tuple[str, str]] = []  # (text, source_filename)

    # ------------------------------------------------------------------
    # 1. Luganda.csv — bilingual, take Luganda column
    # ------------------------------------------------------------------
    p = luganda_dir / "Luganda.csv"
    if p.exists() and pd is not None:
        try:
            df = pd.read_csv(p, usecols=["Luganda"], encoding="latin-1").dropna()
            texts = df["Luganda"].astype(str).tolist()
            combined = " ".join(texts)
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(texts)} rows")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 2. makerere_luganda_monolingual_corpus.csv — Sentences column
    # ------------------------------------------------------------------
    p = luganda_dir / "makerere_luganda_monolingual_corpus.csv"
    if p.exists() and pd is not None:
        try:
            df = pd.read_csv(p, header=None, names=["Sentences"]).dropna()
            combined = " ".join(df["Sentences"].astype(str).tolist())
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(df)} rows")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 3. Luganda_Agriculture-specific_dataset-1.csv — Sentences column
    # ------------------------------------------------------------------
    p = luganda_dir / "Luganda_Agriculture-specific_dataset-1.csv"
    if p.exists() and pd is not None:
        try:
            df = pd.read_csv(p, usecols=["Sentences"]).dropna()
            combined = " ".join(df["Sentences"].astype(str).tolist())
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(df)} rows")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 4. Makerere Sentiment corpus — "Luganda - Translation" column
    # ------------------------------------------------------------------
    p = luganda_dir / "Makerere_Sentiment_corpus_Luganda_and_Kiswahil_translations.csv"
    if p.exists() and pd is not None:
        try:
            df = pd.read_csv(p, usecols=[" Luganda - Translation"]).dropna()
            combined = " ".join(df[" Luganda - Translation"].astype(str).tolist())
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(df)} rows")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 5. Multilingual Parallel Corpus.xlsx — "Luganda Translation" column
    # ------------------------------------------------------------------
    p = luganda_dir / "Multilingual Parallel Corpus.xlsx"
    if p.exists() and pd is not None:
        try:
            df = pd.read_excel(p, usecols=["Luganda Translation"]).dropna()
            combined = " ".join(df["Luganda Translation"].astype(str).tolist())
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(df)} rows")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 6. luganda_wiki_corpus.txt — free-text paragraphs
    # ------------------------------------------------------------------
    p = luganda_dir / "luganda_wiki_corpus.txt"
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            all_texts.append((text, p.name))
            print(f"  ✓ {p.name}: {len(text)} chars")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 7. eng.lug.txt — tab-separated "english\tluganda", take col 1
    # ------------------------------------------------------------------
    p = luganda_dir / "eng.lug.txt"
    if p.exists():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            luganda_lines = []
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1].strip():
                    luganda_lines.append(parts[1].strip())
            combined = " ".join(luganda_lines)
            all_texts.append((combined, p.name))
            print(f"  ✓ {p.name}: {len(luganda_lines)} sentence pairs")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")

    # ------------------------------------------------------------------
    # 8. PDFs in TTT/ (Luganda_Good_Practices_Manual.pdf, etc.)
    # ------------------------------------------------------------------
    if to_markdown is not None:
        for pdf_path in sorted(luganda_dir.glob("*.pdf")):
            try:
                try:
                    md = to_markdown(
                        pdf_path,
                        ignore_images=True,
                        ignore_graphics=True,
                        table_strategy="lines",
                        write_images=False,
                        page_separators="",
                        hdr_info=False,
                    )
                except Exception:
                    md = to_markdown(pdf_path, ignore_images=True, write_images=False)

                text_parts: list[str] = []
                if isinstance(md, list):
                    for item in md:
                        t = item.get('text', '') if isinstance(item, dict) else str(item)
                        if t.strip():
                            text_parts.append(t.strip())
                elif isinstance(md, str) and md.strip():
                    text_parts.append(md.strip())

                if text_parts:
                    all_texts.append(("\n\n".join(text_parts), pdf_path.name))
                    print(f"  ✓ {pdf_path.name}: extracted")
                gc.collect()
            except Exception as e:
                print(f"  ✗ {pdf_path.name}: {e}")

    # ------------------------------------------------------------------
    # Build chunks from all collected texts
    # ------------------------------------------------------------------
    all_chunks: list[dict] = []
    for raw_text, source_name in all_texts:
        cleaned = clean_text(raw_text)
        if len(cleaned) < 50:
            continue
        chunks = chunk_text(cleaned, chunk_size, chunk_overlap)
        for i, chunk in enumerate(chunks):
            word_count = len(chunk.split())
            if word_count >= 20:
                all_chunks.append({
                    'text': chunk,
                    'source': source_name,
                    'chunk_id': i,
                    'total_chunks': len(chunks),
                    'word_count': word_count,
                    'language': 'lg',
                })

    print(f"\n✓ Luganda chunks: {len(all_chunks)}")
    if all_chunks:
        avg = sum(c['word_count'] for c in all_chunks) / len(all_chunks)
        print(f"✓ Average chunk size: {avg:.1f} words")

    return all_chunks


# =============================================================================
# Teacher Model — multi-GPU, chat-template, structured generation
# =============================================================================

class TeacherModel:
    """Teacher model for generating synthetic QA pairs with proper chat templates."""

    def __init__(self, model_name: str = TEACHER_MODEL, device: str = DEVICE):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = None
        self.pipe = None

    def load(self) -> bool:
        """Load teacher model with HF auth, multi-GPU device_map, and bf16."""
        try:
            from huggingface_hub import login
            hf_token = os.getenv("HF_TOKEN")
            if hf_token:
                login(hf_token)
                print("Authenticated with HuggingFace Hub")
        except Exception:
            pass

        models_to_try = [self.model_name, FALLBACK_MODEL]

        for model_id in models_to_try:
            try:
                print(f"Loading model: {model_id}")
                self._print_gpu_status()

                tokenizer = AutoTokenizer.from_pretrained(
                    model_id, trust_remote_code=True, cache_dir=CACHE_DIR
                )
                if tokenizer.pad_token is None:
                    tokenizer.pad_token = tokenizer.eos_token

                # device_map="auto" distributes across all CUDA_VISIBLE_DEVICES
                device_map = "auto" if self.device == "cuda" else None
                gpu_ids = os.getenv("CUDA_VISIBLE_DEVICES", "")
                if gpu_ids:
                    print(f"  Target GPUs: {gpu_ids}")

                model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
                    device_map=device_map,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                    cache_dir=CACHE_DIR,
                )

                # Pipeline — do NOT pass device= when using device_map
                self.pipe = pipeline(
                    "text-generation",
                    model=model,
                    tokenizer=tokenizer,
                )

                self.model = model
                self.tokenizer = tokenizer
                self.model_name = model_id

                param_count = sum(p.numel() for p in model.parameters()) / 1e9
                print(f"Loaded {model_id} ({param_count:.1f}B params)")
                self._print_gpu_status()
                return True

            except Exception as e:
                print(f"Failed to load {model_id}: {e}")
                # Clean up partial load
                if hasattr(self, 'model') and self.model is not None:
                    del self.model
                    self.model = None
                torch.cuda.empty_cache()
                gc.collect()
                continue

        print("All teacher models failed to load")
        return False

    def _print_gpu_status(self):
        """Log current GPU VRAM usage with physical GPU IDs."""
        if not torch.cuda.is_available():
            return
        visible = os.getenv("CUDA_VISIBLE_DEVICES", "")
        phys_ids = visible.split(",") if visible else [str(i) for i in range(torch.cuda.device_count())]
        for i in range(torch.cuda.device_count()):
            alloc = torch.cuda.memory_allocated(i) / 1e9
            total = torch.cuda.get_device_properties(i).total_memory / 1e9
            phys = phys_ids[i] if i < len(phys_ids) else str(i)
            print(f"  GPU {i} (physical #{phys}): {alloc:.1f}/{total:.1f} GB")

    def generate_questions(
        self,
        chunk_text: str,
        num_questions: int = DEFAULT_QUESTIONS_PER_CHUNK,
        context: str = "Uganda Revenue Authority (URA) tax documentation",
        language: str = "en",
    ) -> list[dict]:
        """Generate QA pairs using the model's native chat template.

        Args:
            language: ISO 639-1 code — 'en' for English, 'lg' for Luganda.
                      Luganda chunks receive a bilingual prompt so the teacher
                      model produces questions and answers in Luganda.
        """
        if self.pipe is None:
            return []

        # Truncate chunk to stay within context budget
        max_chunk_chars = 3000
        if len(chunk_text) > max_chunk_chars:
            chunk_text = chunk_text[:max_chunk_chars]

        if language == "lg":
            system_msg = (
                "Oli omuyigiriza wa misingi gy'ebitabo by'empoola. Ofuna emibuuzo n'okuddamu "
                "okuva mu bitabo by'empoola. Siikiriza buzibu JSONL array yokka."
                " (You are a tax education expert generating QA pairs in Luganda. "
                "Output ONLY a valid JSON array.)"
            )
            user_msg = (
                f"Okuva mu kintu kino ekyava mu {context}, kola ddala {num_questions} "
                f"emibuuzo n'okuddamu mu Luganda.\n\n"
                f"EKINTU:\n{chunk_text}\n\n"
                f"AMATEEKA:\n"
                f"1. Emibuuzo gibeere egyirizibwa mu kintu\n"
                f"2. Okuddamu kube kwa wanvu (ennyiriri 2-4), era kibeere kya mazima\n"
                f"3. Yabula emika gy'emibuuzo: \"factual\", \"procedural\", \"conceptual\", \"application\"\n"
                f"4. Toleke emibuuzo egitaliiko nkola\n"
                f"5. Siikiriza JSONL array yokka, toleke kintu kyonna ekindi\n\n"
                f"FORMAT:\n"
                f"[\n"
                f'  {{"question": "...", "answer": "...", "type": "factual"}},\n'
                f'  {{"question": "...", "answer": "...", "type": "procedural"}}\n'
                f"]"
            )
        else:
            system_msg = (
                "You are a tax education expert. You generate high-quality question-answer "
                "pairs from official tax documentation. Output ONLY a valid JSON array."
            )
            user_msg = (
                f"Given this passage from {context}, generate exactly {num_questions} diverse QA pairs.\n\n"
                f"PASSAGE:\n{chunk_text}\n\n"
                f"RULES:\n"
                f"1. Questions must be answerable directly from the passage\n"
                f"2. Answers must be detailed (2-4 sentences), accurate, and cite specifics from the text\n"
                f'3. Mix question types: "factual", "procedural", "conceptual", "application"\n'
                f"4. Do NOT generate generic or vague questions\n"
                f"5. Output ONLY a JSON array, no other text\n\n"
                f"FORMAT:\n"
                f"[\n"
                f'  {{"question": "...", "answer": "...", "type": "factual"}},\n'
                f'  {{"question": "...", "answer": "...", "type": "procedural"}}\n'
                f"]"
            )

        # Build messages using the model's chat template
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        try:
            result = self.pipe(
                messages,
                max_new_tokens=MAX_NEW_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
                num_return_sequences=1,
                return_full_text=False,
            )

            generated = result[0]['generated_text']
            # Handle both string and list[dict] returns from chat pipelines
            if isinstance(generated, list) and len(generated) > 0:
                generated = generated[-1].get('content', str(generated[-1]))
            generated = str(generated).strip()

            questions = self._parse_questions(generated, num_questions)
            return self._quality_filter(questions)

        except Exception as e:
            tqdm.write(f"    Generation error: {e}")
            return []

    def _parse_questions(self, text: str, expected_count: int) -> list[dict]:
        """Parse JSON QA pairs from generated text with multi-strategy fallback."""
        text = text.strip()

        # Strategy 1: Direct JSON array parse
        json_match = re.search(r'\[[\s\S]*\]', text)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, list):
                    return self._validate_parsed(parsed, expected_count)
            except json.JSONDecodeError:
                pass

        # Strategy 2: Extract individual JSON objects
        obj_matches = re.finditer(r'\{[^{}]*"question"[^{}]*"answer"[^{}]*\}', text, re.DOTALL)
        objects = []
        for m in obj_matches:
            try:
                obj = json.loads(m.group(0))
                objects.append(obj)
            except json.JSONDecodeError:
                continue
        if objects:
            return self._validate_parsed(objects, expected_count)

        # Strategy 3: Line-based Q/A parsing
        questions = []
        current_q: dict = {}
        for line in text.split('\n'):
            line = line.strip()
            q_match = re.search(r'(?:question|Q)\s*\d*[:.]\s*(.+)', line, re.IGNORECASE)
            a_match = re.search(r'(?:answer|A)\s*\d*[:.]\s*(.+)', line, re.IGNORECASE)

            if q_match:
                if current_q.get('question') and current_q.get('answer'):
                    questions.append(current_q)
                current_q = {'question': q_match.group(1).strip(), 'type': 'factual'}
            elif a_match and current_q.get('question'):
                current_q['answer'] = a_match.group(1).strip()

        if current_q.get('question') and current_q.get('answer'):
            questions.append(current_q)

        return questions[:expected_count]

    def _validate_parsed(self, items: list, limit: int) -> list[dict]:
        """Validate and normalize parsed QA objects."""
        valid = []
        for item in items:
            if not isinstance(item, dict):
                continue
            q = str(item.get('question', '')).strip()
            a = str(item.get('answer', '')).strip()
            if q and a:
                valid.append({
                    'question': q,
                    'answer': a,
                    'type': str(item.get('type', 'factual')).lower(),
                })
        return valid[:limit]

    def _quality_filter(self, questions: list[dict]) -> list[dict]:
        """Filter QA pairs by minimum quality criteria."""
        filtered = []
        for q in questions:
            if len(q['question']) < MIN_QUESTION_LENGTH:
                continue
            if len(q['answer']) < MIN_ANSWER_LENGTH:
                continue
            if len(q['answer']) > MAX_ANSWER_LENGTH:
                q['answer'] = q['answer'][:MAX_ANSWER_LENGTH]
            # Reject obviously bad generations
            if q['question'].lower() == q['answer'].lower():
                continue
            if '...' in q['answer'] and len(q['answer']) < 30:
                continue
            filtered.append(q)
        return filtered

    def unload(self):
        """Free GPU memory after generation is complete."""
        if self.model is not None:
            del self.model
            self.model = None
        if self.pipe is not None:
            del self.pipe
            self.pipe = None
        self.tokenizer = None
        torch.cuda.empty_cache()
        gc.collect()
        print("Teacher model unloaded, VRAM freed")


# =============================================================================
# Deduplication
# =============================================================================

def deduplicate_qa_pairs(qa_pairs: list[dict]) -> list[dict]:
    """Remove near-duplicate QA pairs using content hashing."""
    seen_hashes: set[str] = set()
    unique = []

    for qa in qa_pairs:
        # Normalize for comparison
        q_norm = re.sub(r'\s+', ' ', qa['question'].lower().strip())
        h = hashlib.md5(q_norm.encode()).hexdigest()

        if h not in seen_hashes:
            seen_hashes.add(h)
            unique.append(qa)

    removed = len(qa_pairs) - len(unique)
    if removed > 0:
        print(f"Deduplication: removed {removed} duplicates, {len(unique)} unique pairs remain")
    return unique


# =============================================================================
# Checkpoint / Resume
# =============================================================================

def _checkpoint_path(output_path: Path) -> Path:
    return output_path.with_suffix('.checkpoint.jsonl')


def _save_checkpoint(qa_pairs: list[dict], output_path: Path, processed_chunks: int):
    """Save progress checkpoint with metadata."""
    cp_path = _checkpoint_path(output_path)
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cp_path, 'w', encoding='utf-8') as f:
        # First line: metadata
        meta = {'_checkpoint_meta': True, 'processed_chunks': processed_chunks,
                'total_pairs': len(qa_pairs), 'timestamp': time.time()}
        f.write(json.dumps(meta, ensure_ascii=False) + '\n')
        for qa in qa_pairs:
            f.write(json.dumps(qa, ensure_ascii=False) + '\n')


def _load_checkpoint(output_path: Path) -> tuple[list[dict], int]:
    """Load checkpoint if it exists. Returns (qa_pairs, start_chunk_index)."""
    cp_path = _checkpoint_path(output_path)
    if not cp_path.exists():
        return [], 0

    qa_pairs = []
    start_chunk = 0

    with open(cp_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get('_checkpoint_meta'):
                start_chunk = data['processed_chunks']
            else:
                qa_pairs.append(data)

    print(f"Resumed from checkpoint: {len(qa_pairs)} pairs, starting at chunk {start_chunk}")
    return qa_pairs, start_chunk


# =============================================================================
# Generation Pipeline
# =============================================================================

def generate_qa_dataset(
    chunks: list[dict],
    teacher: TeacherModel,
    questions_per_chunk: int = DEFAULT_QUESTIONS_PER_CHUNK,
    max_chunks: Optional[int] = None,
    save_interval: int = 10,
    output_path: Optional[Path] = None,
    resume: bool = False,
) -> list[dict]:
    """Generate QA pairs from chunks with progress tracking, checkpointing, and resume."""
    if max_chunks:
        chunks = chunks[:max_chunks]

    # Resume from checkpoint
    all_qa_pairs: list[dict] = []
    start_idx = 0
    if resume and output_path:
        all_qa_pairs, start_idx = _load_checkpoint(output_path)

    remaining = chunks[start_idx:]
    expected = len(remaining) * questions_per_chunk
    print(f"\nGenerating QA for {len(remaining)} chunks ({expected} expected pairs)")
    if start_idx > 0:
        print(f"  Resuming from chunk {start_idx} (already have {len(all_qa_pairs)} pairs)")

    start_time = time.time()
    failed_chunks = 0

    pbar = tqdm(remaining, desc="Generating QA", unit="chunk",
                initial=start_idx, total=len(chunks))

    for i, chunk in enumerate(pbar):
        abs_idx = start_idx + i

        try:
            lang = chunk.get('language', 'en')
            questions = teacher.generate_questions(
                chunk['text'], num_questions=questions_per_chunk, language=lang
            )

            for q in questions:
                record = GeneratedQA(
                    question=q['question'],
                    answer=q['answer'],
                    chunk_text=chunk['text'],
                    source_pdf=chunk['source'],
                    chunk_id=chunk['chunk_id'],
                    question_type=q.get('type', 'factual'),
                )
                all_qa_pairs.append(asdict(record))

            pbar.set_postfix({
                'pairs': len(all_qa_pairs),
                'yield': f"{len(questions)}/{questions_per_chunk}",
            })

        except Exception as e:
            failed_chunks += 1
            tqdm.write(f"  Chunk {abs_idx} failed: {e}")

        # Periodic checkpoint
        if output_path and (abs_idx + 1) % save_interval == 0:
            _save_checkpoint(all_qa_pairs, output_path, abs_idx + 1)

    elapsed = time.time() - start_time
    total_chunks = len(remaining)
    print(f"\nGeneration complete:")
    print(f"  Total pairs: {len(all_qa_pairs)}")
    print(f"  Failed chunks: {failed_chunks}/{total_chunks}")
    print(f"  Time: {elapsed/60:.1f} min ({elapsed/max(total_chunks,1):.1f}s/chunk)")

    # Final checkpoint
    if output_path:
        _save_checkpoint(all_qa_pairs, output_path, len(chunks))

    return all_qa_pairs


# =============================================================================
# Export — multi-format
# =============================================================================

def export_qa_pairs(
    qa_pairs: list[dict],
    output_path: Path,
    formats: list[str],
    also_save_to_data: bool = True,
):
    """Export QA pairs as JSONL (default). Pass additional formats via the formats list."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy to Data/teacher_qa/ directory for downstream consumers
    # (data_augmentation.py, kaggle-training.yml both look here)
    data_copy_dir = DATA_ROOT / "teacher_qa" if also_save_to_data else None
    if data_copy_dir:
        data_copy_dir.mkdir(parents=True, exist_ok=True)

    def _write_and_copy(path: Path, lines: list[str], label: str):
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"  Exported {label}: {path} ({len(lines)} lines)")
        if data_copy_dir:
            dest = data_copy_dir / path.name
            shutil.copy(path, dest)
            print(f"    -> copied to {dest}")

    # Raw JSONL
    if 'jsonl' in formats:
        lines = [json.dumps(qa, ensure_ascii=False) + '\n' for qa in qa_pairs]
        _write_and_copy(output_path.with_suffix('.jsonl'), lines, 'JSONL')

    # Gemma chat format
    if 'gemma' in formats:
        lines = []
        for qa in qa_pairs:
            rec = {
                'text': (
                    f"<start_of_turn>user\n{qa['question']}<end_of_turn>\n"
                    f"<start_of_turn>model\n{qa['answer']}<end_of_turn>"
                ),
                'source': qa['source_pdf'],
                'type': qa['question_type'],
            }
            lines.append(json.dumps(rec, ensure_ascii=False) + '\n')
        _write_and_copy(
            output_path.with_name(output_path.stem + '_gemma.jsonl'), lines, 'Gemma'
        )

    # Alpaca instruction format
    if 'instruction' in formats:
        lines = []
        for qa in qa_pairs:
            rec = {
                'instruction': qa['question'],
                'input': '',
                'output': qa['answer'],
                'context': qa['chunk_text'][:500],
                'source': qa['source_pdf'],
                'type': qa['question_type'],
            }
            lines.append(json.dumps(rec, ensure_ascii=False) + '\n')
        _write_and_copy(
            output_path.with_name(output_path.stem + '_instruction.jsonl'), lines, 'Instruction'
        )

    # Qwen/ChatML format
    if 'qwen' in formats:
        lines = []
        for qa in qa_pairs:
            rec = {
                'messages': [
                    {'role': 'system', 'content': 'You are a helpful Uganda Revenue Authority tax assistant.'},
                    {'role': 'user', 'content': qa['question']},
                    {'role': 'assistant', 'content': qa['answer']},
                ],
                'source': qa['source_pdf'],
                'type': qa['question_type'],
            }
            lines.append(json.dumps(rec, ensure_ascii=False) + '\n')
        _write_and_copy(
            output_path.with_name(output_path.stem + '_qwen.jsonl'), lines, 'Qwen/ChatML'
        )

    # Summary statistics
    sources = {}
    types = {}
    for qa in qa_pairs:
        sources[qa['source_pdf']] = sources.get(qa['source_pdf'], 0) + 1
        types[qa['question_type']] = types.get(qa['question_type'], 0) + 1

    print(f"\nExport statistics:")
    print(f"  Total: {len(qa_pairs)} pairs from {len(sources)} PDFs")
    print(f"  Types: {types}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate QA pairs from URA tax PDFs using a teacher LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                          # Full run, default settings
  %(prog)s --gpu-ids 1,2                            # Use GPUs 1 and 2
  %(prog)s --max-pdfs 3 --chunks 10 --questions 3   # Quick test
  %(prog)s --resume                                 # Resume interrupted run
  %(prog)s --model Qwen/Qwen2.5-3B-Instruct        # Use smaller teacher
""",
    )

    parser.add_argument("--output", type=str, default=str(ARTIFACTS_DIR / "teacher_qa"),
                        help="Output path (without extension)")
    parser.add_argument("--pdf-dir", type=str, default=str(PDF_DIR),
                        help=f"PDF directory (default: {PDF_DIR})")
    parser.add_argument("--luganda-dir", type=str, default=str(LUGANDA_DIR),
                        help=f"Luganda dataset directory (default: {LUGANDA_DIR})")
    parser.add_argument("--skip-luganda", action="store_true",
                        help="Skip loading Luganda dataset")
    parser.add_argument("--model", type=str, default=TEACHER_MODEL,
                        help=f"Teacher model (default: {TEACHER_MODEL})")
    parser.add_argument("--questions", type=int, default=DEFAULT_QUESTIONS_PER_CHUNK,
                        help=f"Questions per chunk (default: {DEFAULT_QUESTIONS_PER_CHUNK})")
    parser.add_argument("--chunks", type=int, default=None,
                        help="Max chunks to process (default: all)")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help=f"Words per chunk (default: {DEFAULT_CHUNK_SIZE})")
    parser.add_argument("--max-pdfs", type=int, default=None,
                        help="Max PDFs to process (default: all)")
    parser.add_argument("--gpu-ids", type=str, default=DEFAULT_GPU_IDS,
                        help=f"Comma-separated GPU IDs (default: {DEFAULT_GPU_IDS or 'all'})")
    parser.add_argument("--formats", type=str, default="jsonl",
                        help="Export formats, comma-separated (default: jsonl). "
                             "Options: jsonl,gemma,instruction,qwen")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last checkpoint")
    parser.add_argument("--no-data-copy", action="store_true",
                        help="Don't copy output to Data/ folder")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Skip deduplication")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"Random seed (default: {SEED})")
    parser.add_argument("--save-interval", type=int, default=10,
                        help="Checkpoint every N chunks (default: 10)")

    args = parser.parse_args()

    # Allow --gpu-ids to override the module-level default.
    # Because no CUDA context has been created yet, this override is still effective.
    if args.gpu_ids and args.gpu_ids != os.environ.get("CUDA_VISIBLE_DEVICES"):
        os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_ids
        print(f"GPU override: CUDA_VISIBLE_DEVICES={args.gpu_ids}")

    active_gpus = os.environ.get("CUDA_VISIBLE_DEVICES", "all")
    n_gpus = torch.cuda.device_count()
    print(f"GPUs active for entire run: {active_gpus} ({n_gpus} visible device(s))")

    # Reproducibility
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    print("=" * 70)
    print("TEACHER MODEL QA GENERATION")
    print("=" * 70)
    print(f"  Model:       {args.model}")
    print(f"  PDF dir:     {args.pdf_dir}")
    print(f"  Questions:   {args.questions}/chunk")
    print(f"  Chunk size:  {args.chunk_size} words")
    print(f"  Device:      {DEVICE} (visible: {os.getenv('CUDA_VISIBLE_DEVICES', 'all')})")
    print(f"  Resume:      {args.resume}")
    print(f"  Seed:        {args.seed}")
    print(f"  Luganda:     {'disabled' if args.skip_luganda else args.luganda_dir}")
    print()

    # 1. Load PDF chunks
    pdf_dir = Path(args.pdf_dir)
    print("=" * 70)
    print("Loading PDF chunks...")
    chunks = load_pdfs(
        pdf_dir, chunk_size=args.chunk_size,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP, max_pdfs=args.max_pdfs,
    )

    # 2. Load Luganda dataset and merge
    if not args.skip_luganda:
        print("=" * 70)
        print("Loading Luganda dataset...")
        luganda_chunks = load_luganda_dataset(
            Path(args.luganda_dir),
            chunk_size=args.chunk_size,
            chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        )
        chunks = chunks + luganda_chunks
        print(f"Combined: {len(chunks)} total chunks "
              f"({len(chunks) - len(luganda_chunks)} English + {len(luganda_chunks)} Luganda)")

    if not chunks:
        print("No chunks found — nothing to process")
        return

    batch_chunks(chunks, TOTAL_BATCH_SIZE)

    # 3. Load teacher model
    teacher = TeacherModel(model_name=args.model)
    if not teacher.load():
        print("Failed to load teacher model")
        return

    # 3. Generate QA pairs
    output_path = Path(args.output)
    qa_pairs = generate_qa_dataset(
        chunks=chunks,
        teacher=teacher,
        questions_per_chunk=args.questions,
        max_chunks=args.chunks,
        save_interval=args.save_interval,
        output_path=output_path,
        resume=args.resume,
    )

    # 4. Unload model to free VRAM
    teacher.unload()

    if not qa_pairs:
        print("No QA pairs generated")
        return

    # 5. Deduplicate
    if not args.no_dedup:
        qa_pairs = deduplicate_qa_pairs(qa_pairs)

    # 6. Export
    export_formats = [f.strip() for f in args.formats.split(',')]
    export_qa_pairs(
        qa_pairs, output_path,
        formats=export_formats,
        also_save_to_data=not args.no_data_copy,
    )

    # Cleanup checkpoint on successful completion
    cp = _checkpoint_path(output_path)
    if cp.exists():
        cp.unlink()
        print("Checkpoint cleaned up (run completed successfully)")

    print(f"\nTeacher QA generation complete: {len(qa_pairs)} pairs")


if __name__ == "__main__":
    main()
