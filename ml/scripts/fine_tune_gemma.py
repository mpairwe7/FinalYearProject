#!/usr/bin/env python3
"""
Fine-tune Gemma-2-2B (or Llama) on URA tax data using LoRA/QLoRA.

This script uses the output from:
  - data_augmentation.py (training_data.jsonl, gemma_training.jsonl)
  - teacher_qa_generation.py (teacher_qa_gemma.jsonl) [optional]

Usage:
    # Fine-tune with default settings
    python ml/scripts/fine_tune_gemma.py
    
    # Fine-tune with specific target
    python ml/scripts/fine_tune_gemma.py --target web_high_accuracy
    
    # Validate data without training
    python ml/scripts/fine_tune_gemma.py --dry-run

Requirements:
    pip install transformers datasets peft accelerate bitsandbytes trl pymupdf4llm
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

log = logging.getLogger("fine_tune_gemma")

# Import pymupdf.layout and pymupdf4llm for PDF processing if needed
try:
    import pymupdf.layout
    import pymupdf4llm
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False
    print("Warning: pymupdf4llm not installed. PDF loading features may be limited.")

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Standard directory structure
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "models"

# PDF directories for potential additional data extraction
PDF_DIR = DATA_ROOT / "pdfs"

RANDOM_SEED = 42

# Model configurations for different deployment targets
MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "web_high_accuracy": {
        "model_id": "google/gemma-2-2b-it",
        "max_seq_length": 2048,
        "lora_r": 16,
        "lora_alpha": 32,
        "epochs": 3,
        "learning_rate": 2e-4,
    },
    "mobile_gemma_2b": {
        "model_id": "google/gemma-2-2b-it",
        "max_seq_length": 1024,
        "lora_r": 8,
        "lora_alpha": 16,
        "epochs": 5,
        "learning_rate": 1e-4,
        "description": "Gemma-2-2B optimised for on-device mobile inference (GGUF INT4)",
    },
    "mobile_offline": {
        "model_id": "meta-llama/Llama-3.2-1B-Instruct",
        "max_seq_length": 1024,
        "lora_r": 8,
        "lora_alpha": 16,
        "epochs": 5,
        "learning_rate": 1e-4,
    },
    "background_t5": {
        "model_id": "google/flan-t5-small",
        "max_seq_length": 512,
        "lora_r": 8,
        "lora_alpha": 16,
        "epochs": 10,
        "learning_rate": 3e-4,
    },
}

# Default LoRA configuration
DEFAULT_LORA_CONFIG: Dict[str, Any] = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "bias": "none",
    "task_type": "CAUSAL_LM",
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
}

# Training data file names (in priority order).
# The 2026 data_augmentation.py pipeline emits messages-format JSONL first;
# legacy Alpaca JSONL and teacher_qa files are kept as fallbacks so historical
# datasets still work.
TRAINING_DATA_FILES = [
    "train.messages.jsonl",
    "training_data.messages.jsonl",
    "gemma_training.jsonl",
    "training_data.jsonl",
    "train.instruction.jsonl",
    "teacher_qa_gemma.jsonl",
    "teacher_qa.jsonl",
]


# =============================================================================
# Messages → text conversion (delegated chat templating)
# =============================================================================
#
# The 2026 data_augmentation pipeline emits each row as a ``messages`` list
# (OpenAI / HF chat format). This is the industry-standard SFT input format
# and is what TRL >= 0.12 consumes natively. We convert messages to raw
# ``text`` here so the existing SFTTrainer ``dataset_text_field="text"`` path
# keeps working without upgrading TRL, while still honouring the canonical
# schema upstream.

def _messages_to_gemma_text(messages: List[Dict[str, str]]) -> str:
    """Render a messages list into the Gemma-2 chat template.

    Gemma-2's instruction-tuned template has no distinct ``system`` role, so
    we fold any leading system content into the first user turn (this is
    exactly what ``tokenizer.apply_chat_template`` does for gemma-2-it)."""
    pending_system: Optional[str] = None
    rendered: List[str] = []
    for msg in messages:
        role = str(msg.get("role", "user")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            pending_system = (
                f"{pending_system}\n\n{content}" if pending_system else content
            )
            continue
        if role == "user":
            body = f"{pending_system}\n\n{content}" if pending_system else content
            pending_system = None
            rendered.append(f"<start_of_turn>user\n{body.strip()}<end_of_turn>")
        elif role in {"assistant", "model"}:
            rendered.append(f"<start_of_turn>model\n{content}<end_of_turn>")
    return "\n".join(rendered)


def _messages_to_llama_text(messages: List[Dict[str, str]]) -> str:
    """Render a messages list into the Llama-3 chat template.

    Llama-3 has a proper ``system`` role — we emit it verbatim."""
    parts: List[str] = ["<|begin_of_text|>"]
    for msg in messages:
        role = str(msg.get("role", "user")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if role not in {"system", "user", "assistant"}:
            role = "user"
        parts.append(
            f"<|start_header_id|>{role}<|end_header_id|>\n\n{content}<|eot_id|>"
        )
    return "".join(parts)

# =============================================================================
# Dependency Checks
# =============================================================================

def check_dependencies() -> bool:
    """Check if required packages are installed."""
    required = ['torch', 'transformers', 'datasets', 'peft', 'trl']
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"❌ Missing required packages: {', '.join(missing)}")
        print(f"   Install with: pip install {' '.join(missing)}")
        return False
    
    # Check for optional PDF processing
    if not PYPDF_AVAILABLE:
        print("⚠️  Optional: pymupdf4llm not installed. Install with: pip install pymupdf4llm")
        print("   This enables additional PDF-based data extraction features.")
    
    # Check GPU availability
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"✓ GPU available: {gpu_name} ({gpu_mem:.1f} GB)")
            # Check CUDA version compatibility
            cuda_version = torch.version.cuda
            print(f"   CUDA Version: {cuda_version}")
        else:
            print("⚠️  No GPU detected - training will be slow")
            # Auto-continue in CI/non-interactive environments
            if os.environ.get("CI") or not os.isatty(0):
                print("   Non-interactive environment detected — continuing with CPU")
            else:
                response = input("Continue with CPU training? (y/n): ")
                if response.lower() != 'y':
                    return False
    except Exception as e:
        print(f"⚠️  GPU check failed: {e}")
    
    return True


def find_training_data() -> Optional[Path]:
    """Find training data file in artifacts or Data directory.

    The 2026 data_augmentation pipeline writes to
    ``artifacts/training_data/`` by default, so we look there first. Older
    flat ``artifacts/*.jsonl`` layouts and the legacy ``Data/*`` locations
    are still honoured for back-compat.
    """
    search_dirs = [
        ARTIFACTS_DIR / "training_data",
        ARTIFACTS_DIR,
        DATA_ROOT,
        DATA_ROOT / "artifacts",
        DATA_ROOT / "teacher_qa",
    ]

    for search_dir in search_dirs:
        for filename in TRAINING_DATA_FILES:
            path = search_dir / filename
            if path.exists():
                return path

    return None


def find_sibling_splits(
    train_path: Path,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Locate sibling val + test JSONL files next to the train file.

    If ``train_path`` is ``.../train.messages.jsonl``, returns
    ``(.../val.messages.jsonl, .../test.messages.jsonl)`` when they exist
    (either or both may be None). This lets the fine-tuner consume the
    stratified + contamination-scanned splits from the 2026 pipeline
    instead of re-splitting the training set — which would cause train /
    eval leakage and waste the careful stratification.
    """
    name = train_path.name
    if "train" not in name:
        return None, None

    def _sibling(new_split: str) -> Optional[Path]:
        candidate = train_path.with_name(name.replace("train", new_split, 1))
        return candidate if candidate.exists() else None

    return _sibling("val"), _sibling("test")


def find_dataset_manifest(train_path: Path) -> Optional[Path]:
    """Return the ``manifest.json`` next to the training data, if any."""
    manifest = train_path.parent / "manifest.json"
    return manifest if manifest.exists() else None


def _digest_file(path: Path) -> str:
    """Return a short SHA-256 digest (12 hex chars) of the file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# =============================================================================
# Enhanced Data Loading with PDF Extraction
# =============================================================================

def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load data from a JSONL file."""
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  ⚠️ Skipping invalid JSON at line {line_num}: {e}")
    return data


def extract_pdf_to_training_data(pdf_path: Path, max_chunks: int = 50) -> List[Dict[str, Any]]:
    """Extract content from PDFs and convert to training format."""
    if not PYPDF_AVAILABLE:
        print("  ⚠️ pymupdf4llm not available for PDF extraction")
        return []
    
    try:
        print(f"  Extracting text from {pdf_path.name}...")
        
        # Extract text using pymupdf4llm with layout preservation
        md_text = pymupdf4llm.to_markdown(
            str(pdf_path),
            pages=None,
            show_progress=False
        )
        
        # Clean and chunk text
        import re
        text = str(md_text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        if len(text) < 100:
            print(f"  ⚠️ PDF {pdf_path.name} has insufficient text ({len(text)} chars)")
            return []
        
        # Simple chunking
        words = text.split()
        chunks = []
        chunk_size = 200
        for i in range(0, min(len(words), max_chunks * chunk_size), chunk_size):
            chunk_text = ' '.join(words[i:i+chunk_size])
            if len(chunk_text) > 50:
                chunks.append(chunk_text)
        
        # Convert chunks to QA format
        training_data = []
        for i, chunk in enumerate(chunks[:max_chunks]):
            # Create simple instructional data from chunks
            training_data.append({
                "instruction": f"Summarize the following text about tax regulations:",
                "input": chunk,
                "output": f"This text discusses tax-related information including {chunk[:100]}...",
                "source": pdf_path.name,
                "chunk_id": i
            })
        
        print(f"  ✓ Extracted {len(training_data)} training examples from {pdf_path.name}")
        return training_data
        
    except Exception as e:
        print(f"  ✗ Error extracting from {pdf_path.name}: {e}")
        return []


def load_training_data(
    data_path: Path,
    synthetic_path: Optional[Path] = None,
    use_pdfs: bool = False,
    max_pdf_chunks: int = 50
):
    """Load and combine training data from multiple sources."""
    from datasets import Dataset, concatenate_datasets
    
    datasets_to_combine = []
    
    # Load main training data
    if data_path.exists():
        print(f"📂 Loading main training data: {data_path}")
        data = load_jsonl(data_path)
        if data:
            main_dataset = Dataset.from_list(data)
            datasets_to_combine.append(main_dataset)
            print(f"   ✓ Loaded {len(main_dataset)} examples")
    else:
        print(f"⚠️  Main training data not found: {data_path}")
    
    # Load synthetic QA data
    if synthetic_path and synthetic_path.exists():
        print(f"📂 Loading synthetic QA data: {synthetic_path}")
        synthetic_data = load_jsonl(synthetic_path)
        if synthetic_data:
            synthetic_dataset = Dataset.from_list(synthetic_data)
            datasets_to_combine.append(synthetic_dataset)
            print(f"   ✓ Loaded {len(synthetic_dataset)} synthetic examples")
    
    # Extract additional data from PDFs if requested
    if use_pdfs and PYPDF_AVAILABLE and PDF_DIR.exists():
        print(f"📂 Extracting additional data from PDFs in {PDF_DIR}")
        pdf_data = []
        pdf_files = list(PDF_DIR.glob("*.pdf"))[:5]  # Limit to first 5 PDFs
        for pdf_file in pdf_files:
            pdf_data.extend(extract_pdf_to_training_data(pdf_file, max_pdf_chunks))
        
        if pdf_data:
            pdf_dataset = Dataset.from_list(pdf_data)
            datasets_to_combine.append(pdf_dataset)
            print(f"   ✓ Extracted {len(pdf_dataset)} examples from PDFs")
    
    if not datasets_to_combine:
        raise ValueError("No training data found!")
    
    if len(datasets_to_combine) == 1:
        combined = datasets_to_combine[0]
    else:
        combined = concatenate_datasets(datasets_to_combine)
    
    print(f"\n✓ Total training examples: {len(combined)}")
    return combined


def format_for_gemma(example: Dict[str, Any]) -> Dict[str, str]:
    """Format examples for Gemma instruction tuning."""

    # 2026 messages format (canonical from data_augmentation.py)
    msgs = example.get("messages")
    if isinstance(msgs, list) and msgs:
        return {"text": _messages_to_gemma_text(msgs)}

    # If already in Gemma format
    if "text" in example and "<start_of_turn>" in str(example.get("text", "")):
        return {"text": example["text"]}

    # If in instruction format (Alpaca-style)
    if "instruction" in example:
        instruction = example["instruction"]
        input_text = example.get("input", "").strip()
        output = example.get("output", "")
        
        user_content = f"{instruction}\n\n{input_text}" if input_text else instruction
        
        text = (
            f"<start_of_turn>user\n{user_content.strip()}<end_of_turn>\n"
            f"<start_of_turn>model\n{output.strip()}<end_of_turn>"
        )
        return {"text": text}
    
    # If in QA format
    if "question" in example and "answer" in example:
        question = str(example["question"]).strip()
        answer = str(example["answer"]).strip()
        text = (
            f"<start_of_turn>user\n{question}<end_of_turn>\n"
            f"<start_of_turn>model\n{answer}<end_of_turn>"
        )
        return {"text": text}
    
    # If in prompt/completion format
    if "prompt" in example and "completion" in example:
        prompt = str(example["prompt"]).strip()
        completion = str(example["completion"]).strip()
        text = (
            f"<start_of_turn>user\n{prompt}<end_of_turn>\n"
            f"<start_of_turn>model\n{completion}<end_of_turn>"
        )
        return {"text": text}
    
    return {"text": ""}


def format_for_llama(example: Dict[str, Any]) -> Dict[str, str]:
    """Format examples for Llama 3.x instruction tuning."""

    # 2026 messages format (canonical from data_augmentation.py)
    msgs = example.get("messages")
    if isinstance(msgs, list) and msgs:
        return {"text": _messages_to_llama_text(msgs)}

    if "instruction" in example:
        instruction = example["instruction"]
        input_text = example.get("input", "").strip()
        output = example.get("output", "")
        
        user_content = f"{instruction}\n\n{input_text}" if input_text else instruction
        
        text = (
            f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
            f"{user_content.strip()}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{output.strip()}<|eot_id|>"
        )
        return {"text": text}
    
    if "question" in example and "answer" in example:
        question = str(example["question"]).strip()
        answer = str(example["answer"]).strip()
        text = (
            f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
            f"{question}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{answer}<|eot_id|>"
        )
        return {"text": text}
    
    if "prompt" in example and "completion" in example:
        prompt = str(example["prompt"]).strip()
        completion = str(example["completion"]).strip()
        text = (
            f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
            f"{prompt}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{completion}<|eot_id|>"
        )
        return {"text": text}
    
    return {"text": ""}


def format_for_t5(example: Dict[str, Any]) -> Dict[str, str]:
    """Format examples for T5 models."""
    # 2026 messages format: join user turns as input, assistant text as target.
    msgs = example.get("messages")
    if isinstance(msgs, list) and msgs:
        user_parts: List[str] = []
        assistant_parts: List[str] = []
        for m in msgs:
            role = str(m.get("role", "")).lower()
            content = str(m.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                user_parts.append(content)
            elif role in {"assistant", "model"}:
                assistant_parts.append(content)
            # system turns dropped for T5 (no chat role concept)
        if user_parts and assistant_parts:
            return {
                "input_text": f"question: {' '.join(user_parts)}",
                "target_text": " ".join(assistant_parts),
            }

    if "instruction" in example and "output" in example:
        instruction = example["instruction"]
        input_text = example.get("input", "")
        
        if input_text:
            text = f"question: {instruction} context: {input_text}"
        else:
            text = f"question: {instruction}"
        
        return {
            "input_text": text,
            "target_text": example["output"]
        }
    
    if "question" in example and "answer" in example:
        return {
            "input_text": f"question: {example['question']}",
            "target_text": example["answer"]
        }
    
    return {"input_text": "", "target_text": ""}


# =============================================================================
# Enhanced Model Setup with Better Error Handling
# =============================================================================

def _try_flash_attention_2() -> Optional[str]:
    """Return ``"flash_attention_2"`` if flash-attn is installed and the
    GPU supports it (SM 8.0+), else ``None`` to fall back to SDPA.

    Flash Attention 2 gives 2-4× throughput and ~20% VRAM savings on A100
    / H100 / A6000 class GPUs. We don't hard-require it because older
    GPUs (T4, V100) don't support it and the wheel install can fail on
    mismatched CUDA. SDPA (PyTorch native, no extra install) is the
    universal fallback.
    """
    try:
        import flash_attn  # noqa: F401
    except ImportError:
        return None
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        major = torch.cuda.get_device_capability(0)[0]
        return "flash_attention_2" if major >= 8 else None
    except Exception:
        return None


def setup_model_and_tokenizer(
    model_id: str,
    use_4bit: bool = True,
    use_8bit: bool = False,
    max_seq_length: int = 2048,
    distributed: bool = False,
):
    """Load model + tokenizer for fine-tuning.

    2026 production notes:
    - **Flash Attention 2** auto-detected; falls back to SDPA on unsupported
      GPUs or missing flash-attn wheel.
    - **Device map** is ``None`` in distributed mode so ``accelerate
      launch`` / DDP can place the model on each process's local GPU.
      ``device_map="auto"`` would break multi-GPU training by
      tensor-parallelising the model instead of data-parallel.
    - **bfloat16** compute dtype on capable GPUs (A6000, A100, H100).
    - **Pad token**: we set ``pad_token = eos_token`` only when the
      tokenizer has no distinct pad, but we immediately configure
      ``tokenizer.pad_token_id`` so the data collator can mask padding
      without corrupting the EOS signal during loss computation.
    - **Gradient checkpointing with ``use_reentrant=False``** — PyTorch 2+
      best practice. The default ``use_reentrant=True`` is slower and
      incompatible with DDP + find_unused_parameters.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import prepare_model_for_kbit_training

    log.info("Loading model: %s", model_id)

    # Check for T5 models (sequence-to-sequence)
    is_t5 = "t5" in model_id.lower() or "flan" in model_id.lower()

    # Determine compute dtype based on GPU capability
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        compute_dtype = torch.bfloat16
        log.info("  Using bfloat16 compute dtype")
    else:
        compute_dtype = torch.float16
        log.info("  Using float16 compute dtype")

    # Quantization config
    bnb_config = None
    if use_4bit and not is_t5:  # T5 models may not support 4-bit
        try:
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=compute_dtype,
                bnb_4bit_use_double_quant=True,
            )
            log.info("  Using 4-bit quantization (QLoRA)")
        except Exception as e:
            log.warning("  4-bit quantization failed: %s, falling back to FP16", e)
    elif use_8bit and not is_t5:
        try:
            bnb_config = BitsAndBytesConfig(load_in_8bit=True)
            log.info("  Using 8-bit quantization")
        except Exception as e:
            log.warning("  8-bit quantization failed: %s, falling back to FP16", e)

    # Load tokenizer
    log.info("  Loading tokenizer...")
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)

        # Pad token handling. If the tokenizer doesn't have a distinct pad
        # token, reuse EOS but keep track — the data collator will mask
        # pad positions out of the loss via attention_mask, so reusing EOS
        # is safe *as long as* attention_mask is passed (which all HF
        # collators do).
        if tokenizer.pad_token_id is None:
            if tokenizer.eos_token_id is not None:
                tokenizer.pad_token = tokenizer.eos_token
                log.info("  Pad token set to EOS (id=%d)", tokenizer.eos_token_id)
            elif tokenizer.unk_token is not None:
                tokenizer.pad_token = tokenizer.unk_token
                log.info("  Pad token set to UNK (no EOS available)")

        # Right-padding is required for causal LM training (left-padding
        # is only for batched generation).
        tokenizer.padding_side = "right"

        # Set model_max_length for tokenisation
        if hasattr(tokenizer, "model_max_length"):
            current_max = tokenizer.model_max_length
            if current_max < max_seq_length:
                log.warning(
                    "  Tokenizer max length (%d) < requested (%d) — updating",
                    current_max, max_seq_length,
                )
                tokenizer.model_max_length = max_seq_length
        else:
            tokenizer.model_max_length = max_seq_length

    except Exception as e:
        log.error("  Failed to load tokenizer: %s", e)
        raise

    # Flash Attention 2 auto-detect
    attn_impl = _try_flash_attention_2()
    if attn_impl:
        log.info("  Attention backend: flash_attention_2 (auto-detected)")
    else:
        attn_impl = "sdpa"  # PyTorch native, universally available
        log.info("  Attention backend: sdpa (flash_attn not available)")

    # Load model
    log.info("  Loading model weights...")
    try:
        if is_t5:
            from transformers import T5ForConditionalGeneration

            model_class = T5ForConditionalGeneration
            log.info("  Detected T5 model")
        else:
            model_class = AutoModelForCausalLM

        model_kwargs: Dict[str, Any] = {
            "trust_remote_code": False,
        }

        # In distributed mode, do NOT set device_map — accelerate/DDP
        # places the model on each process's local GPU itself. Setting
        # device_map="auto" in DDP silently tensor-parallels instead.
        if not distributed and torch.cuda.is_available():
            model_kwargs["device_map"] = "auto"

        # Flash Attention 2 only applies to causal LM, not T5
        if not is_t5:
            model_kwargs["attn_implementation"] = attn_impl

        if bnb_config:
            model_kwargs["quantization_config"] = bnb_config
        elif compute_dtype and not is_t5:
            model_kwargs["torch_dtype"] = compute_dtype

        model = model_class.from_pretrained(model_id, **model_kwargs)

        if use_4bit or use_8bit:
            # ``use_reentrant=False`` is PyTorch 2+ standard; required for
            # DDP + torch.compile compatibility.
            model = prepare_model_for_kbit_training(
                model,
                use_gradient_checkpointing=True,
                gradient_checkpointing_kwargs={"use_reentrant": False},
            )
        else:
            model.gradient_checkpointing_enable(
                gradient_checkpointing_kwargs={"use_reentrant": False},
            )

        total_params = model.num_parameters()
        log.info("  Model loaded: %s parameters (%s)", f"{total_params:,}", attn_impl)

    except Exception as e:
        log.error("  Failed to load model: %s", e)
        raise

    return model, tokenizer, is_t5


def apply_lora(
    model,
    is_t5: bool = False,
    config: Optional[Dict[str, Any]] = None,
    use_rslora: bool = False,
    use_dora: bool = False,
):
    """Apply LoRA adapters to the model.

    2026 notes:
    - **RSLoRA** (Rank-Stabilised LoRA, 2024) rescales the LoRA alpha by
      ``sqrt(r)`` which dramatically improves higher-rank training. Enabled
      via ``--use-rslora``. Effectively free (no extra params, no extra
      compute), and PEFT ≥ 0.9 supports it natively.
    - **DoRA** (Weight-Decomposed LoRA, 2024) splits the LoRA update into
      a magnitude + direction, closing ~half the gap to full fine-tuning
      at the cost of ~10% extra compute. Optional via ``--use-dora``.
    - **Target modules** follow the QLoRA paper: all attention projections
      + all MLP projections, which is more robust than the "q_proj/v_proj"
      LoRA original.
    """
    from peft import LoraConfig, get_peft_model

    lora_config = {**DEFAULT_LORA_CONFIG, **(config or {})}

    # Adjust target modules for T5
    if is_t5:
        lora_config["task_type"] = "SEQ_2_SEQ_LM"
        lora_config["target_modules"] = ["q", "k", "v", "o", "wi", "wo"]

    peft_kwargs = dict(
        r=lora_config["r"],
        lora_alpha=lora_config["lora_alpha"],
        lora_dropout=lora_config["lora_dropout"],
        bias=lora_config["bias"],
        task_type=lora_config["task_type"],
        target_modules=lora_config["target_modules"],
    )
    if use_rslora:
        peft_kwargs["use_rslora"] = True
    if use_dora:
        peft_kwargs["use_dora"] = True

    peft_config = LoraConfig(**peft_kwargs)

    model = get_peft_model(model, peft_config)

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())

    log.info("LoRA Configuration:")
    log.info("  Rank (r):       %d", lora_config["r"])
    log.info("  Alpha:          %d", lora_config["lora_alpha"])
    log.info("  Dropout:        %s", lora_config["lora_dropout"])
    log.info("  RSLoRA:         %s", use_rslora)
    log.info("  DoRA:           %s", use_dora)
    log.info("  Task type:      %s", lora_config["task_type"])
    log.info("  Target modules: %s...", ", ".join(lora_config["target_modules"][:4]))
    log.info(
        "  Trainable:      %s / %s (%.2f%%)",
        f"{trainable:,}", f"{total:,}", 100 * trainable / total,
    )

    return model


# =============================================================================
# Enhanced Training with Validation
# =============================================================================

def validate_dataset(dataset, tokenizer, max_seq_length: int, model_type: str = "gemma"):
    """Validate dataset tokenization and identify potential issues."""
    print(f"\n🔍 Validating dataset for {model_type}...")
    
    sample_texts = dataset[:min(10, len(dataset))]["text"]
    
    total_tokens = 0
    max_tokens = 0
    min_tokens = float('inf')
    too_long = 0
    
    for i, text in enumerate(sample_texts):
        tokens = tokenizer.encode(text, truncation=False)
        token_count = len(tokens)
        total_tokens += token_count
        max_tokens = max(max_tokens, token_count)
        min_tokens = min(min_tokens, token_count)
        
        if token_count > max_seq_length:
            too_long += 1
            if i < 3:  # Show first 3 examples that are too long
                print(f"  ⚠️ Example {i}: {token_count} tokens (truncation needed)")
    
    avg_tokens = total_tokens / len(sample_texts)
    
    print(f"  Token statistics (sample of {len(sample_texts)}):")
    print(f"    Average: {avg_tokens:.1f}")
    print(f"    Min:     {min_tokens}")
    print(f"    Max:     {max_tokens}")
    print(f"    >{max_seq_length}: {too_long} examples")
    
    if too_long > len(sample_texts) * 0.5:
        print(f"  ⚠️ Warning: More than 50% of samples exceed max sequence length")
        print(f"    Consider increasing --max-seq-length or reducing chunk size")
    
    return True


def train(
    model,
    tokenizer,
    train_dataset,
    eval_dataset,
    output_dir: Path,
    *,
    max_seq_length: int = 2048,
    num_epochs: int = 3,
    batch_size: int = 4,
    gradient_accumulation_steps: int = 4,
    learning_rate: float = 2e-4,
    warmup_ratio: float = 0.03,
    weight_decay: float = 0.01,
    model_type: str = "gemma",
    is_t5: bool = False,
    lora_r: int = 16,
    lora_alpha: int = 32,
    lora_dropout: float = 0.05,
    use_rslora: bool = False,
    use_dora: bool = False,
    neftune_alpha: Optional[float] = 5.0,
    report_to: str = "none",
    resume_from_checkpoint: Optional[str] = None,
    push_to_hub: Optional[str] = None,
    save_merged: bool = False,
    group_by_length: bool = True,
    seed: int = 42,
    dataset_metadata: Optional[Dict[str, Any]] = None,
):
    """Fine-tune the model using TRL 1.0 ``SFTTrainer`` / HF ``Seq2SeqTrainer``.

    2026 production design:

    - **TRL 1.0 API** — uses ``SFTConfig`` + ``processing_class`` (the old
      ``tokenizer=`` / ``dataset_text_field=`` / ``packing=`` kwargs on
      ``SFTTrainer.__init__`` were removed).
    - **Native completion-only loss** via ``SFTConfig(completion_only_loss=True)``.
      This is the 2025 correct way — ``DataCollatorForCompletionOnlyLM``
      silently fails to mask across packed sequences so we removed it.
    - **Pre-computed splits** — callers now pass ``train_dataset`` and
      ``eval_dataset`` directly. No more fragile ``train_test_split``
      that would discard the pipeline's stratified + contamination-scanned
      val set.
    - **Reproducible seeding** via ``transformers.set_seed`` at main entry.
    - **Flash Attention 2** auto-detected in ``setup_model_and_tokenizer``.
    - **use_rslora / use_dora** flags for PEFT ≥ 0.9 improvements.
    - **Gradient checkpointing with ``use_reentrant=False``** (required for
      DDP + torch.compile).
    - **group_by_length** enabled for 10-20% training speedup.
    - **weight_decay=0.01** (LoRA papers consistently show 0.0 underfits
      and 0.1 overfits on small datasets).
    - **report_to** routes to TensorBoard/W&B/MLflow when requested.
    - **Dataset manifest digest** captured in ``training_config.json`` so
      every checkpoint is traceable to its exact training data.
    """
    import torch
    from transformers import EarlyStoppingCallback

    effective_batch = batch_size * gradient_accumulation_steps

    log.info("Training configuration:")
    log.info("  Output directory:    %s", output_dir)
    log.info("  Epochs:              %d", num_epochs)
    log.info(
        "  Batch size:          %d x %d = %d",
        batch_size, gradient_accumulation_steps, effective_batch,
    )
    log.info("  Learning rate:       %s", learning_rate)
    log.info("  Weight decay:        %s", weight_decay)
    log.info("  Max sequence length: %d", max_seq_length)
    log.info("  Warmup ratio:        %s", warmup_ratio)
    log.info(
        "  Model type:          %s%s",
        model_type, " (T5)" if is_t5 else "",
    )
    log.info("  Train samples:       %d", len(train_dataset))
    log.info("  Eval samples:        %d", len(eval_dataset) if eval_dataset else 0)

    # Validate dataset before training
    if not is_t5:
        validate_dataset(train_dataset, tokenizer, max_seq_length, model_type)

    # Determine fp16/bf16 based on GPU capability
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()

    # Adaptive step intervals based on dataset size
    steps_per_epoch = max(1, len(train_dataset) // effective_batch)
    eval_save_steps = max(50, steps_per_epoch // 3)

    # Optimizer choice — paged_adamw_8bit needs bitsandbytes which may not
    # be importable on hosts without python3-dev (Triton JIT compilation
    # fails). Detect and fall back to adamw_torch_fused on CUDA or
    # adamw_torch on CPU.
    def _pick_optimizer() -> str:
        if not torch.cuda.is_available():
            return "adamw_torch"
        try:
            import bitsandbytes  # noqa: F401
            return "paged_adamw_8bit"
        except Exception:
            log.info("bitsandbytes unavailable — using adamw_torch_fused (no 8-bit optimizer)")
            return "adamw_torch_fused"

    chosen_optim = _pick_optimizer()

    # Shared TrainingArguments-equivalent fields.
    common_args: Dict[str, Any] = dict(
        output_dir=str(output_dir),
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        warmup_ratio=warmup_ratio,
        logging_steps=10,
        save_steps=eval_save_steps,
        eval_steps=eval_save_steps,
        eval_strategy="steps" if eval_dataset is not None else "no",
        save_strategy="steps",
        save_total_limit=3,
        load_best_model_at_end=eval_dataset is not None,
        metric_for_best_model="eval_loss" if eval_dataset is not None else None,
        greater_is_better=False,
        bf16=use_bf16,
        fp16=not use_bf16 and torch.cuda.is_available(),
        optim=chosen_optim,
        report_to=report_to,
        lr_scheduler_type="cosine",
        seed=seed,
        data_seed=seed,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_grad_norm=1.0,
        weight_decay=weight_decay,
        # Worker procs use AF_UNIX sockets to share GPU tensors which can
        # be blocked by sandboxed environments (PermissionError: socket.bind).
        # 0 workers is slower but safe everywhere; override with the env var
        # ``DATALOADER_NUM_WORKERS`` when you know your env supports it.
        dataloader_num_workers=int(os.environ.get("DATALOADER_NUM_WORKERS", "0")),
        dataloader_pin_memory=torch.cuda.is_available(),
        logging_first_step=True,
        eval_accumulation_steps=16,
        group_by_length=group_by_length and not is_t5,  # causal LM only
        # Training throughput
        save_safetensors=True,
    )
    if push_to_hub:
        common_args.update(
            push_to_hub=True,
            hub_model_id=push_to_hub,
            hub_strategy="every_save",
        )

    callbacks = []
    if eval_dataset is not None:
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=3))

    if is_t5:
        # T5: use Seq2SeqTrainer directly — not SFT.
        from transformers import (
            DataCollatorForSeq2Seq,
            Seq2SeqTrainer,
            Seq2SeqTrainingArguments,
        )

        # Map messages/instruction format to (input_text, target_text).
        def _map_t5(ex):
            return format_for_t5(ex)

        train_t5 = train_dataset.map(
            _map_t5,
            remove_columns=[c for c in train_dataset.column_names],
        )
        eval_t5 = (
            eval_dataset.map(
                _map_t5,
                remove_columns=[c for c in eval_dataset.column_names],
            ) if eval_dataset is not None else None
        )

        training_args = Seq2SeqTrainingArguments(**common_args)

        data_collator = DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            model=model,
            padding=True,
            max_length=max_seq_length,
        )

        trainer = Seq2SeqTrainer(
            model=model,
            args=training_args,
            train_dataset=train_t5,
            eval_dataset=eval_t5,
            processing_class=tokenizer,
            data_collator=data_collator,
            callbacks=callbacks,
        )
    else:
        # Causal LM via TRL SFTTrainer + SFTConfig (TRL >= 0.12 API).
        from trl import SFTConfig, SFTTrainer

        # IMPORTANT — TRL 1.0 SFTTrainer prefers a ``messages`` column
        # over the explicit ``dataset_text_field``. If our dataset has
        # both, TRL re-renders the chat template via
        # ``tokenizer.apply_chat_template`` — which fails on Gemma-2
        # because the upstream template raises ``"System role not
        # supported"`` (Gemma has no system role).
        #
        # We've already pre-rendered every row to a ``text`` column via
        # ``format_for_gemma`` / ``format_for_llama`` (which correctly
        # fold system into the first user turn for Gemma). Strip every
        # other column so TRL uses the pre-rendered text directly.
        def _strip_to_text(ds):
            keep = {"text"}
            drop = [c for c in ds.column_names if c not in keep]
            return ds.remove_columns(drop) if drop else ds

        train_dataset = _strip_to_text(train_dataset)
        if eval_dataset is not None:
            eval_dataset = _strip_to_text(eval_dataset)

        sft_only = dict(
            max_length=max_seq_length,
            # Packing requires a pre-tokenisation dataset.map() pass which
            # invokes ``multiprocess.Manager`` and crashes with EOFError
            # when forked from a CUDA-initialised parent (datasets +
            # multiprocess >= 0.70.16 known issue). We disable packing
            # by default and rely on TRL's per-example completion-only
            # masking, which is correct but slightly less throughput-efficient.
            # Re-enable explicitly via SFTConfig override if your env
            # supports it (e.g. spawn-mode multiprocessing).
            packing=False,
            # TRL 1.0: masks user turns automatically based on chat template.
            completion_only_loss=True,
            dataset_text_field="text",
            neftune_noise_alpha=neftune_alpha,
            dataset_num_proc=None,
            remove_unused_columns=True,
        )
        # SFTConfig accepts every TrainingArguments field plus the SFT-only
        # fields above.
        try:
            sft_config = SFTConfig(**common_args, **sft_only)
        except TypeError:
            # Fallback: some SFT-only kwargs may rename across TRL versions.
            # Try a conservative subset and log what was dropped.
            safe_sft_only = {
                k: v for k, v in sft_only.items()
                if k in {"max_length", "packing", "completion_only_loss",
                         "dataset_text_field", "neftune_noise_alpha"}
            }
            log.warning(
                "SFTConfig did not accept some kwargs; retrying with %s",
                list(safe_sft_only.keys()),
            )
            sft_config = SFTConfig(**common_args, **safe_sft_only)

        trainer = SFTTrainer(
            model=model,
            args=sft_config,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            processing_class=tokenizer,
            callbacks=callbacks,
        )

    log.info("=" * 60)
    log.info("TRAINING STARTED")
    log.info("=" * 60)

    # Support resume-from-checkpoint for failed runs.
    trainer.train(resume_from_checkpoint=resume_from_checkpoint)

    # Save model (LoRA adapter by default)
    final_dir = output_dir / "final"
    log.info("Saving adapter to %s", final_dir)
    trainer.save_model(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))

    # Optional: save the fully merged FP16 model for direct deployment
    if save_merged:
        merged_dir = output_dir / "final-merged"
        log.info("Merging LoRA into base model and saving to %s", merged_dir)
        try:
            merged = trainer.model.merge_and_unload()
            merged.save_pretrained(
                str(merged_dir), safe_serialization=True, max_shard_size="4GB",
            )
            tokenizer.save_pretrained(str(merged_dir))
            log.info("  Merged model saved")
        except Exception as exc:
            log.warning("  merge_and_unload failed: %s", exc)

    # Save training metrics
    metrics = trainer.state.log_history
    metrics_path = output_dir / "training_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Save training config for reproducibility
    config_path = output_dir / "training_config.json"
    training_config = {
        "pipeline_version": "2026.1.0",
        "model_id": str(model.config._name_or_path),
        "model_type": model_type,
        "is_t5": is_t5,
        "max_seq_length": max_seq_length,
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "effective_batch_size": effective_batch,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "warmup_ratio": warmup_ratio,
        "seed": seed,
        "train_samples": len(train_dataset),
        "eval_samples": len(eval_dataset) if eval_dataset is not None else 0,
        "lora_config": {
            "r": lora_r,
            "lora_alpha": lora_alpha,
            "lora_dropout": lora_dropout,
            "use_rslora": use_rslora,
            "use_dora": use_dora,
            "target_modules": DEFAULT_LORA_CONFIG["target_modules"],
            "bias": DEFAULT_LORA_CONFIG["bias"],
            "task_type": DEFAULT_LORA_CONFIG["task_type"],
        },
        "neftune_alpha": neftune_alpha,
        "report_to": report_to,
        "dataset_metadata": dataset_metadata or {},
        "dataset_sources": (
            sorted(set(train_dataset["source"])) if "source" in train_dataset.column_names else []
        ),
    }
    with open(config_path, "w") as f:
        json.dump(training_config, f, indent=2, default=str)

    log.info("Model saved to:   %s", final_dir)
    log.info("Metrics saved to: %s", metrics_path)
    log.info("Config saved to:  %s", config_path)

    # Evaluate final model
    if eval_dataset is not None:
        log.info("Final evaluation...")
        try:
            eval_results = trainer.evaluate()
            loss = eval_results.get("eval_loss")
            if loss is not None:
                log.info("  eval_loss: %.4f", loss)
            if "eval_perplexity" in eval_results:
                log.info("  perplexity: %.2f", eval_results["eval_perplexity"])
        except Exception as e:
            log.warning("  Evaluation failed: %s", e)

    return trainer


# =============================================================================
# Enhanced Main Function
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fine-tune Gemma/Llama/T5 on URA tax data using LoRA/QLoRA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run                    # Validate data only
  %(prog)s --target web_high_accuracy   # Use preset config
  %(prog)s --epochs 5 --lora-r 32       # Custom training
  %(prog)s --use-pdfs                   # Extract additional data from PDFs
"""
    )
    
    # Data arguments
    data_group = parser.add_argument_group("Data")
    data_group.add_argument("--data", type=Path, default=None,
                            help="Training data JSONL file (auto-detected if not specified)")
    data_group.add_argument("--synthetic", type=Path, default=None,
                            help="Additional synthetic QA data")
    data_group.add_argument("--use-pdfs", action="store_true",
                            help="Extract additional training data from PDFs")
    data_group.add_argument("--max-pdf-chunks", type=int, default=50,
                            help="Maximum chunks to extract per PDF")
    
    # Model arguments
    model_group = parser.add_argument_group("Model")
    model_group.add_argument("--model", type=str, default="google/gemma-2-2b-it",
                             help="HuggingFace model ID")
    model_group.add_argument("--target", type=str, choices=list(MODEL_CONFIGS.keys()),
                             default=None, help="Use preset model configuration")
    model_group.add_argument("--output", type=Path, default=None,
                             help="Output directory for fine-tuned model")
    
    # Training arguments
    train_group = parser.add_argument_group("Training")
    train_group.add_argument("--epochs", type=int, default=None,
                             help="Number of training epochs (overrides preset)")
    train_group.add_argument("--batch-size", type=int, default=4)
    train_group.add_argument("--learning-rate", type=float, default=None,
                             help="Learning rate (overrides preset)")
    train_group.add_argument("--max-seq-length", type=int, default=None,
                             help="Maximum sequence length (overrides preset)")
    train_group.add_argument("--warmup-ratio", type=float, default=0.03)
    train_group.add_argument("--gradient-accumulation", type=int, default=4,
                             help="Gradient accumulation steps")
    
    train_group.add_argument("--weight-decay", type=float, default=0.01,
                             help="AdamW weight decay (default: 0.01, LoRA paper)")
    train_group.add_argument("--neftune-alpha", type=float, default=5.0,
                             help="NEFTune noise alpha (2023, improves generalisation). Set 0 to disable.")
    train_group.add_argument("--no-group-by-length", action="store_true",
                             help="Disable length-based batch grouping (10-20%% slower but simpler)")
    train_group.add_argument("--seed", type=int, default=RANDOM_SEED,
                             help="Random seed for reproducibility (default: 42)")
    train_group.add_argument("--resume-from-checkpoint", type=str, default=None,
                             help="Resume training from a checkpoint path")
    train_group.add_argument("--report-to", type=str, default="tensorboard",
                             choices=["none", "tensorboard", "wandb", "mlflow", "all"],
                             help="Observability backend (default: tensorboard)")

    # LoRA arguments
    lora_group = parser.add_argument_group("LoRA")
    lora_group.add_argument("--lora-r", type=int, default=None,
                             help="LoRA rank (default: from preset or 16)")
    lora_group.add_argument("--lora-alpha", type=int, default=None,
                             help="LoRA alpha (default: from preset or 32)")
    lora_group.add_argument("--lora-dropout", type=float, default=None,
                             help="LoRA dropout (default: 0.05)")
    lora_group.add_argument("--use-rslora", action="store_true",
                             help="Use Rank-Stabilised LoRA (2024, PEFT >= 0.9). "
                                  "Rescales alpha by sqrt(r); free accuracy gain.")
    lora_group.add_argument("--use-dora", action="store_true",
                             help="Use Weight-Decomposed LoRA (2024, PEFT >= 0.9). "
                                  "Closes ~half the gap to full FT, ~10%% slower.")

    # Quantization arguments
    quant_group = parser.add_argument_group("Quantization")
    quant_group.add_argument("--no-4bit", action="store_true", help="Disable 4-bit quantization")
    quant_group.add_argument("--use-8bit", action="store_true", help="Use 8-bit quantization instead")
    quant_group.add_argument("--no-quant", action="store_true", help="Disable all quantization (FP16/32)")

    # GPU arguments
    gpu_group = parser.add_argument_group("GPU")
    gpu_group.add_argument("--gpu-ids", type=str, default=None,
                           help="Comma-separated GPU IDs to use (e.g., '1,2'). Sets CUDA_VISIBLE_DEVICES.")
    gpu_group.add_argument("--num-gpus", type=int, default=None,
                           help="Number of GPUs for multi-GPU training (used with accelerate launch)")

    # Deployment / persistence
    deploy_group = parser.add_argument_group("Deployment")
    deploy_group.add_argument("--push-to-hub", type=str, default=None,
                              help="HuggingFace Hub repo id to push the model to (e.g. user/ura-gemma)")
    deploy_group.add_argument("--save-merged", action="store_true",
                              help="Also save the fully-merged FP16 model alongside the LoRA adapter")

    # Other arguments
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data without training")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--force-cpu", action="store_true",
                        help="Force CPU training even if GPU is available")
    
    args = parser.parse_args()

    # Structured logging — also goes to the trainer via HF Trainer's logger.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    print("=" * 70)
    print("URA TAX ASSISTANT - FINE-TUNING PIPELINE (2026)")
    print("=" * 70)
    print(f"Using pymupdf4llm: {'✓' if PYPDF_AVAILABLE else '✗ (optional)'}")

    # Deterministic reproducibility — must be set BEFORE any CUDA context
    # is created, which is why we do it right after arg parsing.
    try:
        from transformers import set_seed

        set_seed(args.seed)
        log.info("Seed set to %d (transformers.set_seed)", args.seed)
    except Exception:
        import random
        random.seed(args.seed)

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # GPU selection
    if args.gpu_ids:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_ids
        gpu_list = args.gpu_ids.split(",")
        print(f"\n🎯 Using GPUs: {args.gpu_ids} ({len(gpu_list)} device(s))")

    # Force CPU if requested
    if args.force_cpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        print("\n⚠️  CPU training forced (--force-cpu)")
    
    # Use preset config if specified
    if args.target:
        config = MODEL_CONFIGS[args.target]
        args.model = config["model_id"]
        if args.max_seq_length is None:
            args.max_seq_length = config["max_seq_length"]
        if args.lora_r is None:
            args.lora_r = config["lora_r"]
        if args.lora_alpha is None:
            args.lora_alpha = config["lora_alpha"]
        if args.epochs is None:
            args.epochs = config["epochs"]
        if args.learning_rate is None:
            args.learning_rate = config["learning_rate"]
        print(f"\n📋 Using preset: {args.target}")
        print(f"   Model:      {args.model}")
        print(f"   Seq length: {args.max_seq_length}")
        print(f"   LoRA r:     {args.lora_r}")
        print(f"   Epochs:     {args.epochs}")
        print(f"   LR:         {args.learning_rate}")
    
    # Set defaults for unspecified args (after preset overrides)
    if args.max_seq_length is None:
        args.max_seq_length = 2048 if "gemma" in args.model else 1024
    if args.epochs is None:
        args.epochs = 3
    if args.learning_rate is None:
        args.learning_rate = 2e-4
    if args.lora_r is None:
        args.lora_r = DEFAULT_LORA_CONFIG["r"]
    if args.lora_alpha is None:
        args.lora_alpha = DEFAULT_LORA_CONFIG["lora_alpha"]
    if args.lora_dropout is None:
        args.lora_dropout = DEFAULT_LORA_CONFIG["lora_dropout"]
    
    # Find training data if not specified
    if args.data is None:
        args.data = find_training_data()
        if args.data is None:
            print("\n❌ No training data found!")
            print("   Expected files in artifacts/ or Data/:")
            for f in TRAINING_DATA_FILES:
                print(f"   - {f}")
            print("\n   Run data_augmentation.py or teacher_qa_generation.py first.")
            print("   Or use --use-pdfs to extract from PDFs directly.")
            sys.exit(1)
        print(f"\n📂 Auto-detected training data: {args.data}")
    
    # Set default output directory
    if args.output is None:
        model_name = args.model.split("/")[-1].lower()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = OUTPUT_DIR / f"ura-{model_name}-{timestamp}"
    
    # Load data — prefer pre-computed splits from the 2026 pipeline.
    print(f"\n📥 Loading data...")
    val_path, test_path = find_sibling_splits(args.data)
    manifest_path = find_dataset_manifest(args.data)

    dataset_metadata: Dict[str, Any] = {
        "train_path": str(args.data),
        "train_sha256": _digest_file(args.data),
    }
    if val_path:
        dataset_metadata["val_path"] = str(val_path)
        dataset_metadata["val_sha256"] = _digest_file(val_path)
    if test_path:
        dataset_metadata["test_path"] = str(test_path)
        dataset_metadata["test_sha256"] = _digest_file(test_path)
    if manifest_path:
        dataset_metadata["manifest_path"] = str(manifest_path)
        dataset_metadata["manifest_sha256"] = _digest_file(manifest_path)
        try:
            m = json.loads(manifest_path.read_text())
            dataset_metadata["pipeline_version"] = m.get("pipeline_version")
            dataset_metadata["schema_version"] = m.get("schema_version")
            dataset_metadata["git_commit"] = m.get("git", {}).get("commit")
        except Exception:
            pass

    try:
        train_dataset = load_training_data(
            args.data,
            args.synthetic,
            use_pdfs=args.use_pdfs,
            max_pdf_chunks=args.max_pdf_chunks,
        )
    except Exception as e:
        print(f"❌ Failed to load training data: {e}")
        sys.exit(1)

    eval_dataset = None
    if val_path is not None:
        from datasets import Dataset
        val_rows = load_jsonl(val_path)
        if val_rows:
            eval_dataset = Dataset.from_list(val_rows)
            log.info("Loaded pre-computed eval split: %s (%d rows)", val_path, len(eval_dataset))
    else:
        log.warning(
            "No sibling val.messages.jsonl found — will split train internally. "
            "For a production run, use the 2026 data_augmentation.py output "
            "which emits pre-computed train/val/test splits."
        )

    # Determine model type
    model_type = "llama" if "llama" in args.model.lower() else "gemma"
    if "t5" in args.model.lower():
        model_type = "t5"

    # Format for the model (on both train and eval if present)
    print(f"\n🔄 Formatting data for {model_type}...")
    try:
        format_fn = {
            "llama": format_for_llama,
            "t5": format_for_t5,
        }.get(model_type, format_for_gemma)
        train_dataset = train_dataset.map(format_fn)
        if eval_dataset is not None:
            eval_dataset = eval_dataset.map(format_fn)
    except Exception as e:
        print(f"❌ Failed to format data: {e}")
        sys.exit(1)

    # Filter examples without valid content
    original_len = len(train_dataset)

    if model_type == "t5":
        def _t5_ok(x):
            return bool(
                x.get("input_text") and len(x["input_text"]) > 10
                and x.get("target_text") and len(x["target_text"]) > 5
            )
        train_dataset = train_dataset.filter(_t5_ok)
        if eval_dataset is not None:
            eval_dataset = eval_dataset.filter(_t5_ok)
    else:
        def _text_ok(x):
            return bool(x.get("text") and len(x["text"]) > 10)
        train_dataset = train_dataset.filter(_text_ok)
        if eval_dataset is not None:
            eval_dataset = eval_dataset.filter(_text_ok)

    filtered_count = original_len - len(train_dataset)
    if filtered_count > 0:
        print(f"   ⚠️ Filtered {filtered_count} invalid examples")
    print(f"   ✓ {len(train_dataset)} training examples ready")
    if eval_dataset is not None:
        print(f"   ✓ {len(eval_dataset)} evaluation examples ready")

    # If no sibling val set was found, fall back to a deterministic
    # in-memory split. This preserves backward compatibility with legacy
    # flat JSONL inputs while the pre-split path is the canonical 2026 one.
    if eval_dataset is None and len(train_dataset) > 20:
        log.warning("Falling back to 90/10 train_test_split (seed=%d)", args.seed)
        split = train_dataset.train_test_split(test_size=0.1, seed=args.seed)
        train_dataset, eval_dataset = split["train"], split["test"]

    if args.dry_run:
        print("\n" + "=" * 70)
        print("✓ DRY RUN COMPLETE")
        print("=" * 70)
        print("\nData validated successfully!")
        print(f"   Train examples:   {len(train_dataset)}")
        print(f"   Eval examples:    {len(eval_dataset) if eval_dataset else 0}")
        print(f"   Model:            {args.model}")
        print(f"   Model type:       {model_type}")
        print(f"   Output:           {args.output}")
        print(f"   Max seq length:   {args.max_seq_length}")
        print(f"   Epochs:           {args.epochs}")
        print(f"   LoRA r / α:       {args.lora_r} / {args.lora_alpha}")
        print(f"   RSLoRA / DoRA:    {args.use_rslora} / {args.use_dora}")
        print(f"   Neftune α:        {args.neftune_alpha}")
        print(f"   Weight decay:     {args.weight_decay}")
        print(f"   Group by length:  {not args.no_group_by_length}")
        print(f"   Report to:        {args.report_to}")
        print(f"   Dataset metadata: {json.dumps(dataset_metadata, indent=2)}")

        # Show sample
        if args.verbose and len(train_dataset) > 0:
            print("\n📝 Sample formatted example:")
            if model_type == "t5":
                sample_input = train_dataset[0]["input_text"][:300]
                sample_output = train_dataset[0]["target_text"][:200]
                print(f"   Input:  {sample_input}...")
                print(f"   Output: {sample_output}...")
            else:
                sample = train_dataset[0]["text"][:500]
                print(f"   {sample}...")
        return

    # Setup model
    distributed = bool(args.num_gpus and args.num_gpus > 1)
    try:
        model, tokenizer, is_t5 = setup_model_and_tokenizer(
            args.model,
            use_4bit=not args.no_4bit and not args.use_8bit and not args.no_quant,
            use_8bit=args.use_8bit and not args.no_quant,
            max_seq_length=args.max_seq_length,
            distributed=distributed,
        )
    except Exception as e:
        print(f"❌ Failed to setup model: {e}")
        sys.exit(1)

    # Apply LoRA
    lora_config = {
        "r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
    }
    model = apply_lora(
        model,
        is_t5=is_t5,
        config=lora_config,
        use_rslora=args.use_rslora,
        use_dora=args.use_dora,
    )

    # Ensure output directory exists
    args.output.mkdir(parents=True, exist_ok=True)

    # Train
    try:
        print(f"\n📅 Starting training at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        train(
            model, tokenizer, train_dataset, eval_dataset, args.output,
            max_seq_length=args.max_seq_length,
            num_epochs=args.epochs,
            batch_size=args.batch_size,
            gradient_accumulation_steps=args.gradient_accumulation,
            learning_rate=args.learning_rate,
            warmup_ratio=args.warmup_ratio,
            weight_decay=args.weight_decay,
            model_type=model_type,
            is_t5=is_t5,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            use_rslora=args.use_rslora,
            use_dora=args.use_dora,
            neftune_alpha=args.neftune_alpha if args.neftune_alpha > 0 else None,
            report_to=args.report_to,
            resume_from_checkpoint=args.resume_from_checkpoint,
            push_to_hub=args.push_to_hub,
            save_merged=args.save_merged,
            group_by_length=not args.no_group_by_length,
            seed=args.seed,
            dataset_metadata=dataset_metadata,
        )
        
    except KeyboardInterrupt:
        print(f"\n⚠️ Training interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "="*70)
    print("✓ FINE-TUNING COMPLETE")
    print("="*70)
    print(f"\nModel saved to: {args.output / 'final'}")
    print(f"Training completed at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Create README file for the model
    readme_path = args.output / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(f"""# URA Tax Assistant - Fine-tuned Model

## Model Information
- **Base Model**: {args.model}
- **Fine-tuning Method**: LoRA/QLoRA
- **Training Date**: {datetime.datetime.now().strftime('%Y-%m-%d')}
- **Sequence Length**: {args.max_seq_length}
- **Training Epochs**: {args.epochs}

## Training Data
- **Total Examples**: {len(train_dataset)}
- **Data Sources**: {'Multiple sources' if args.use_pdfs else 'JSONL files'}
- **Model Type**: {model_type}

## Training Configuration
- **Learning Rate**: {args.learning_rate}
- **Batch Size**: {args.batch_size}
- **LoRA Rank (r)**: {args.lora_r}
- **LoRA Alpha**: {args.lora_alpha}

## Usage
```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

model_name = "{args.output / 'final'}"
model = AutoModelForCausalLM.from_pretrained(model_name)
tokenizer = AutoTokenizer.from_pretrained(model_name)

# For inference
prompt = "<start_of_turn>user\\nYour tax question here<end_of_turn>\\n<start_of_turn>model\\n"
inputs = tokenizer(prompt, return_tensors="pt")
outputs = model.generate(**inputs, max_new_tokens=200)
response = tokenizer.decode(outputs[0], skip_special_tokens=True)""")


if __name__ == "__main__":
    main()
