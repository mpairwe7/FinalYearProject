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
import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

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
ARTIFACTS_DIR = DATA_ROOT / "artifacts"
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

# Training data file names (in priority order)
TRAINING_DATA_FILES = [
    "gemma_training.jsonl",
    "training_data.jsonl",
    "teacher_qa_gemma.jsonl",
    "teacher_qa.jsonl",
]

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
    """Find training data file in artifacts or Data directory."""
    search_dirs = [ARTIFACTS_DIR, DATA_ROOT]
    
    for search_dir in search_dirs:
        for filename in TRAINING_DATA_FILES:
            path = search_dir / filename
            if path.exists():
                return path
    
    return None


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

def setup_model_and_tokenizer(
    model_id: str,
    use_4bit: bool = True,
    use_8bit: bool = False,
    max_seq_length: int = 2048
):
    """Load model with quantization for efficient fine-tuning."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import prepare_model_for_kbit_training
    
    print(f"\n🔧 Loading model: {model_id}")
    
    # Check for T5 models (sequence-to-sequence)
    is_t5 = "t5" in model_id.lower() or "flan" in model_id.lower()
    
    # Determine compute dtype based on GPU capability
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        compute_dtype = torch.bfloat16
        print("   Using bfloat16 compute dtype")
    else:
        compute_dtype = torch.float16
        print("   Using float16 compute dtype")
    
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
            print("   ✓ Using 4-bit quantization (QLoRA)")
        except Exception as e:
            print(f"   ⚠️ 4-bit quantization failed: {e}, falling back to FP16")
    elif use_8bit and not is_t5:
        try:
            bnb_config = BitsAndBytesConfig(load_in_8bit=True)
            print("   ✓ Using 8-bit quantization")
        except Exception as e:
            print(f"   ⚠️ 8-bit quantization failed: {e}, falling back to FP16")
    
    # Load tokenizer
    print("   Loading tokenizer...")
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        
        # Set padding token if not exists
        if tokenizer.pad_token is None:
            if tokenizer.eos_token:
                tokenizer.pad_token = tokenizer.eos_token
            else:
                tokenizer.pad_token = tokenizer.unk_token
        
        tokenizer.padding_side = "right"  # Required for training
        
        # Set model_max_length
        if hasattr(tokenizer, "model_max_length"):
            current_max = tokenizer.model_max_length
            if current_max < max_seq_length:
                print(f"   ⚠️ Tokenizer max length ({current_max}) < requested ({max_seq_length})")
                tokenizer.model_max_length = max_seq_length
                print(f"   ✓ Updated tokenizer max length to {max_seq_length}")
        else:
            tokenizer.model_max_length = max_seq_length
            
    except Exception as e:
        print(f"   ❌ Failed to load tokenizer: {e}")
        raise
    
    # Load model
    print("   Loading model weights...")
    try:
        if is_t5:
            from transformers import T5ForConditionalGeneration
            model_class = T5ForConditionalGeneration
            print("   ✓ Detected T5 model")
        else:
            model_class = AutoModelForCausalLM
        
        model_kwargs = {
            "trust_remote_code": True,
            "device_map": "auto" if torch.cuda.is_available() else None,
        }
        
        if bnb_config:
            model_kwargs["quantization_config"] = bnb_config
        elif compute_dtype and not is_t5:
            model_kwargs["torch_dtype"] = compute_dtype
        
        model = model_class.from_pretrained(model_id, **model_kwargs)

        if use_4bit or use_8bit:
            model = prepare_model_for_kbit_training(model)

        model.gradient_checkpointing_enable()

        total_params = model.num_parameters()
        print(f"   ✓ Model loaded: {total_params:,} parameters")
        
    except Exception as e:
        print(f"   ❌ Failed to load model: {e}")
        raise
    
    return model, tokenizer, is_t5


def apply_lora(model, is_t5: bool = False, config: Optional[Dict[str, Any]] = None):
    """Apply LoRA adapters to the model."""
    from peft import LoraConfig, get_peft_model
    
    lora_config = {**DEFAULT_LORA_CONFIG, **(config or {})}
    
    # Adjust target modules for T5
    if is_t5:
        lora_config["task_type"] = "SEQ_2_SEQ_LM"
        lora_config["target_modules"] = ["q", "k", "v", "o", "wi", "wo"]
    
    peft_config = LoraConfig(
        r=lora_config["r"],
        lora_alpha=lora_config["lora_alpha"],
        lora_dropout=lora_config["lora_dropout"],
        bias=lora_config["bias"],
        task_type=lora_config["task_type"],
        target_modules=lora_config["target_modules"],
    )
    
    model = get_peft_model(model, peft_config)
    
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    
    print(f"\n📊 LoRA Configuration:")
    print(f"   Rank (r):       {lora_config['r']}")
    print(f"   Alpha:          {lora_config['lora_alpha']}")
    print(f"   Dropout:        {lora_config['lora_dropout']}")
    print(f"   Task type:      {lora_config['task_type']}")
    print(f"   Target modules: {', '.join(lora_config['target_modules'][:4])}...")
    print(f"   Trainable:      {trainable:,} / {total:,} ({100 * trainable / total:.2f}%)")
    
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
    dataset,
    output_dir: Path,
    max_seq_length: int = 2048,
    num_epochs: int = 3,
    batch_size: int = 4,
    gradient_accumulation_steps: int = 4,
    learning_rate: float = 2e-4,
    warmup_ratio: float = 0.03,
    model_type: str = "gemma",
    is_t5: bool = False,
):
    """Fine-tune the model using SFTTrainer or Seq2SeqTrainer."""
    import torch
    from transformers import TrainingArguments
    
    effective_batch = batch_size * gradient_accumulation_steps
    
    print(f"\n🚀 Training Configuration:")
    print(f"   Output directory:    {output_dir}")
    print(f"   Epochs:              {num_epochs}")
    print(f"   Batch size:          {batch_size} x {gradient_accumulation_steps} = {effective_batch}")
    print(f"   Learning rate:       {learning_rate}")
    print(f"   Max sequence length: {max_seq_length}")
    print(f"   Warmup ratio:        {warmup_ratio}")
    print(f"   Model type:          {model_type} {'(T5)' if is_t5 else ''}")
    
    # Split dataset
    split = dataset.train_test_split(test_size=0.1, seed=RANDOM_SEED)
    print(f"   Train samples:       {len(split['train'])}")
    print(f"   Eval samples:        {len(split['test'])}")
    
    # Validate dataset before training
    validate_dataset(split["train"], tokenizer, max_seq_length, model_type)
    
    # Determine fp16/bf16 based on GPU capability
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        warmup_ratio=warmup_ratio,
        logging_steps=10,
        save_steps=100,
        eval_steps=100,
        eval_strategy="steps",
        save_strategy="steps",
        save_total_limit=3,
        load_best_model_at_end=True,
        bf16=use_bf16,
        fp16=not use_bf16 and torch.cuda.is_available(),
        optim="paged_adamw_8bit" if torch.cuda.is_available() else "adamw_torch",
        report_to="none",
        lr_scheduler_type="cosine",
        seed=RANDOM_SEED,
        gradient_checkpointing=True,
        max_grad_norm=0.3,
        weight_decay=0.001,
        dataloader_num_workers=0 if torch.cuda.is_available() else 2,
        remove_unused_columns=False,
        logging_first_step=True,
        eval_accumulation_steps=2,
    )
    
    if is_t5:
        # T5 uses different training approach
        from transformers import Seq2SeqTrainer, DataCollatorForSeq2Seq
        
        # Prepare dataset for T5
        dataset = dataset.map(
            format_for_t5,
            remove_columns=[col for col in dataset.column_names if col not in ["input_text", "target_text"]]
        )
        
        # Data collator for T5
        data_collator = DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            model=model,
            padding=True,
            max_length=max_seq_length,
        )
        
        # Trainer for T5
        trainer = Seq2SeqTrainer(
            model=model,
            args=training_args,
            train_dataset=split["train"],
            eval_dataset=split["test"],
            tokenizer=tokenizer,
            data_collator=data_collator,
        )
        
    else:
        # Standard causal LM training
        from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
        
        # Response template for completion-only training (only train on model responses)
        if model_type == "llama":
            response_template = "<|start_header_id|>assistant<|end_header_id|>\n\n"
        else:  # gemma
            response_template = "<start_of_turn>model\n"
        
        collator = DataCollatorForCompletionOnlyLM(
            response_template=response_template,
            tokenizer=tokenizer,
        )
        
        # Trainer
        trainer = SFTTrainer(
            model=model,
            args=training_args,
            train_dataset=split["train"],
            eval_dataset=split["test"],
            tokenizer=tokenizer,
            data_collator=collator,
            dataset_text_field="text",
            max_seq_length=max_seq_length,
            packing=False,
            neftune_noise_alpha=5,  # NEFTune for better generalization
        )
    
    print("\n" + "=" * 60)
    print("TRAINING STARTED")
    print("=" * 60)
    
    # Train the model
    trainer.train()
    
    # Save model
    final_dir = output_dir / "final"
    print(f"\n💾 Saving model to {final_dir}...")
    trainer.save_model(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))
    
    # Save training metrics
    metrics = trainer.state.log_history
    metrics_path = output_dir / "training_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    
    # Save training config for reproducibility
    config_path = output_dir / "training_config.json"
    training_config = {
        "model_id": str(model.config._name_or_path),
        "max_seq_length": max_seq_length,
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "effective_batch_size": effective_batch,
        "learning_rate": learning_rate,
        "warmup_ratio": warmup_ratio,
        "model_type": model_type,
        "is_t5": is_t5,
        "train_samples": len(split["train"]),
        "eval_samples": len(split["test"]),
        "lora_config": {
            "r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "target_modules": DEFAULT_LORA_CONFIG["target_modules"],
            "bias": DEFAULT_LORA_CONFIG["bias"],
            "task_type": DEFAULT_LORA_CONFIG["task_type"],
        },
        "dataset_sources": list(set(dataset["source"])) if "source" in dataset.column_names else []
    }
    with open(config_path, "w") as f:
        json.dump(training_config, f, indent=2)
    
    print(f"\n✓ Model saved to:   {final_dir}")
    print(f"✓ Metrics saved to: {metrics_path}")
    print(f"✓ Config saved to:  {config_path}")
    
    # Evaluate final model
    print(f"\n📊 Final evaluation...")
    try:
        eval_results = trainer.evaluate()
        print(f"   Evaluation loss: {eval_results.get('eval_loss', 'N/A'):.4f}")
        if 'eval_perplexity' in eval_results:
            print(f"   Perplexity: {eval_results['eval_perplexity']:.2f}")
    except Exception as e:
        print(f"   ⚠️ Evaluation failed: {e}")
    
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
    
    # LoRA arguments
    lora_group = parser.add_argument_group("LoRA")
    lora_group.add_argument("--lora-r", type=int, default=None,
                             help="LoRA rank (default: from preset or 16)")
    lora_group.add_argument("--lora-alpha", type=int, default=None,
                             help="LoRA alpha (default: from preset or 32)")
    lora_group.add_argument("--lora-dropout", type=float, default=None,
                             help="LoRA dropout (default: 0.05)")
    
    # Quantization arguments
    quant_group = parser.add_argument_group("Quantization")
    quant_group.add_argument("--no-4bit", action="store_true", help="Disable 4-bit quantization")
    quant_group.add_argument("--use-8bit", action="store_true", help="Use 8-bit quantization instead")
    quant_group.add_argument("--no-quant", action="store_true", help="Disable all quantization (FP16/32)")
    
    # Other arguments
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data without training")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--force-cpu", action="store_true",
                        help="Force CPU training even if GPU is available")
    
    args = parser.parse_args()
    
    print("="*70)
    print("URA TAX ASSISTANT - FINE-TUNING PIPELINE")
    print("="*70)
    print(f"Using pymupdf4llm: {'✓' if PYPDF_AVAILABLE else '✗ (optional)'}")
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
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
    
    # Load data
    print(f"\n📥 Loading data...")
    try:
        dataset = load_training_data(
            args.data,
            args.synthetic,
            use_pdfs=args.use_pdfs,
            max_pdf_chunks=args.max_pdf_chunks
        )
    except Exception as e:
        print(f"❌ Failed to load training data: {e}")
        sys.exit(1)
    
    # Determine model type
    model_type = "llama" if "llama" in args.model.lower() else "gemma"
    if "t5" in args.model.lower():
        model_type = "t5"
    
    # Format for the model
    print(f"\n🔄 Formatting data for {model_type}...")
    try:
        if model_type == "llama":
            dataset = dataset.map(format_for_llama)
        elif model_type == "t5":
            dataset = dataset.map(format_for_t5)
        else:
            dataset = dataset.map(format_for_gemma)
    except Exception as e:
        print(f"❌ Failed to format data: {e}")
        sys.exit(1)
    
    # Filter examples without valid content
    original_len = len(dataset)
    
    if model_type == "t5":
        dataset = dataset.filter(lambda x: (
            "input_text" in x and x["input_text"] and len(x["input_text"]) > 10 and
            "target_text" in x and x["target_text"] and len(x["target_text"]) > 5
        ))
    else:
        dataset = dataset.filter(lambda x: (
            "text" in x and x["text"] and len(x["text"]) > 10
        ))
    
    filtered_count = original_len - len(dataset)
    if filtered_count > 0:
        print(f"   ⚠️ Filtered {filtered_count} invalid examples")
    print(f"   ✓ {len(dataset)} examples ready for training")
    
    if args.dry_run:
        print("\n" + "="*70)
        print("✓ DRY RUN COMPLETE")
        print("="*70)
        print(f"\nData validated successfully!")
        print(f"   Training examples: {len(dataset)}")
        print(f"   Model:            {args.model}")
        print(f"   Model type:       {model_type}")
        print(f"   Output:           {args.output}")
        print(f"   Max seq length:   {args.max_seq_length}")
        print(f"   Epochs:           {args.epochs}")
        
        # Show sample
        if args.verbose and len(dataset) > 0:
            print("\n📝 Sample formatted example:")
            if model_type == "t5":
                sample_input = dataset[0]["input_text"][:300]
                sample_output = dataset[0]["target_text"][:200]
                print(f"   Input:  {sample_input}...")
                print(f"   Output: {sample_output}...")
            else:
                sample = dataset[0]["text"][:500]
                print(f"   {sample}...")
        return
    
    # Setup model
    try:
        model, tokenizer, is_t5 = setup_model_and_tokenizer(
            args.model,
            use_4bit=not args.no_4bit and not args.use_8bit and not args.no_quant,
            use_8bit=args.use_8bit and not args.no_quant,
            max_seq_length=args.max_seq_length
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
    model = apply_lora(model, is_t5=is_t5, config=lora_config)
    
    # Ensure output directory exists
    args.output.mkdir(parents=True, exist_ok=True)
    
    # Train
    try:
        print(f"\n📅 Starting training at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        train(
            model, tokenizer, dataset, args.output,
            max_seq_length=args.max_seq_length,
            num_epochs=args.epochs,
            batch_size=args.batch_size,
            gradient_accumulation_steps=args.gradient_accumulation,
            learning_rate=args.learning_rate,
            warmup_ratio=args.warmup_ratio,
            model_type=model_type,
            is_t5=is_t5,
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
- **Total Examples**: {len(dataset)}
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