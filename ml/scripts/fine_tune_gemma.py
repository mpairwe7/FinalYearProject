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
    pip install transformers datasets peft accelerate bitsandbytes trl
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Standard directory structure
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "models"

RANDOM_SEED = 42

# Model configurations for different deployment targets
MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "web_high_accuracy": {
        "model_id": "google/gemma-2-2b-it",
        "max_seq_length": 2048,
        "lora_r": 16,
        "lora_alpha": 32,
    },
    "mobile_offline": {
        "model_id": "meta-llama/Llama-3.2-1B-Instruct",
        "max_seq_length": 1024,
        "lora_r": 8,
        "lora_alpha": 16,
    },
    "background_t5": {
        "model_id": "google/flan-t5-small",
        "max_seq_length": 512,
        "lora_r": 8,
        "lora_alpha": 16,
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
    
    # Check GPU availability
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"✓ GPU available: {gpu_name} ({gpu_mem:.1f} GB)")
        else:
            print("⚠️  No GPU detected - training will be slow")
    except Exception:
        pass
    
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
# Data Loading
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


def load_training_data(data_path: Path, synthetic_path: Optional[Path] = None):
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

    # Truncate to max_seq_length tokens if possible
    max_seq_length = 2048  # Default; can be overridden by config
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained("google/gemma-2-2b-it")
        tokens = tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) > max_seq_length:
            tokens = tokens[:max_seq_length]
            text = tokenizer.decode(tokens, skip_special_tokens=True)
    except Exception:
        pass
    return {"text": text}

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

    # Truncate to max_seq_length tokens if possible
    max_seq_length = 2048  # Default; can be overridden by config
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-3.2-1B-Instruct")
        tokens = tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) > max_seq_length:
            tokens = tokens[:max_seq_length]
            text = tokenizer.decode(tokens, skip_special_tokens=True)
    except Exception:
        pass
    return {"text": text}

# =============================================================================
# Model Setup
# =============================================================================

def setup_model_and_tokenizer(model_id: str, use_4bit: bool = True, use_8bit: bool = False):
    """Load model with quantization for efficient fine-tuning."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import prepare_model_for_kbit_training
    
    print(f"\n🔧 Loading model: {model_id}")
    
    # Determine compute dtype based on GPU capability
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        compute_dtype = torch.bfloat16
        print("   Using bfloat16 compute dtype")
    else:
        compute_dtype = torch.float16
        print("   Using float16 compute dtype")
    
    # Quantization config
    bnb_config = None
    if use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )
        print("   ✓ Using 4-bit quantization (QLoRA)")
    elif use_8bit:
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)
        print("   ✓ Using 8-bit quantization")
    
    # Load tokenizer
    print("   Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"  # Required for training
    
    # Load model
    print("   Loading model weights...")
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=compute_dtype if not bnb_config else None,
    )
    
    if use_4bit or use_8bit:
        model = prepare_model_for_kbit_training(model)
    
    model.gradient_checkpointing_enable()
    
    total_params = model.num_parameters()
    print(f"   ✓ Model loaded: {total_params:,} parameters")
    
    return model, tokenizer


def apply_lora(model, config: Optional[Dict[str, Any]] = None):
    """Apply LoRA adapters to the model."""
    from peft import LoraConfig, get_peft_model
    
    lora_config = {**DEFAULT_LORA_CONFIG, **(config or {})}
    
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
    print(f"   Target modules: {', '.join(lora_config['target_modules'][:4])}...")
    print(f"   Trainable:      {trainable:,} / {total:,} ({100 * trainable / total:.2f}%)")
    
    return model


# =============================================================================
# Training
# =============================================================================

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
):
    """Fine-tune the model using SFTTrainer."""
    import torch
    from transformers import TrainingArguments
    from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
    
    effective_batch = batch_size * gradient_accumulation_steps
    
    print(f"\n🚀 Training Configuration:")
    print(f"   Output directory:    {output_dir}")
    print(f"   Epochs:              {num_epochs}")
    print(f"   Batch size:          {batch_size} x {gradient_accumulation_steps} = {effective_batch}")
    print(f"   Learning rate:       {learning_rate}")
    print(f"   Max sequence length: {max_seq_length}")
    print(f"   Warmup ratio:        {warmup_ratio}")
    
    # Split dataset
    split = dataset.train_test_split(test_size=0.1, seed=RANDOM_SEED)
    print(f"   Train samples:       {len(split['train'])}")
    print(f"   Eval samples:        {len(split['test'])}")
    
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
        evaluation_strategy="steps",
        save_strategy="steps",
        save_total_limit=3,
        load_best_model_at_end=True,
        bf16=use_bf16,
        fp16=not use_bf16,
        optim="paged_adamw_8bit",
        report_to="none",
        lr_scheduler_type="cosine",
        seed=RANDOM_SEED,
        gradient_checkpointing=True,
        max_grad_norm=0.3,
        weight_decay=0.001,
    )
    
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
        "max_seq_length": max_seq_length,
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "effective_batch_size": effective_batch,
        "learning_rate": learning_rate,
        "warmup_ratio": warmup_ratio,
        "model_type": model_type,
        "train_samples": len(split["train"]),
        "eval_samples": len(split["test"]),
    }
    with open(config_path, "w") as f:
        json.dump(training_config, f, indent=2)
    
    print(f"\n✓ Model saved to:   {final_dir}")
    print(f"✓ Metrics saved to: {metrics_path}")
    print(f"✓ Config saved to:  {config_path}")
    
    return trainer


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fine-tune Gemma/Llama on URA tax data using LoRA/QLoRA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run                    # Validate data only
  %(prog)s --target web_high_accuracy   # Use preset config
  %(prog)s --epochs 5 --lora-r 32       # Custom training
"""
    )
    
    # Data arguments
    data_group = parser.add_argument_group("Data")
    data_group.add_argument("--data", type=Path, default=None,
                            help="Training data JSONL file (auto-detected if not specified)")
    data_group.add_argument("--synthetic", type=Path, default=None,
                            help="Additional synthetic QA data")
    
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
    train_group.add_argument("--epochs", type=int, default=3)
    train_group.add_argument("--batch-size", type=int, default=4)
    train_group.add_argument("--learning-rate", type=float, default=2e-4)
    train_group.add_argument("--max-seq-length", type=int, default=2048)
    train_group.add_argument("--warmup-ratio", type=float, default=0.03)
    
    # LoRA arguments
    lora_group = parser.add_argument_group("LoRA")
    lora_group.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    lora_group.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha")
    lora_group.add_argument("--lora-dropout", type=float, default=0.05)
    
    # Quantization arguments
    quant_group = parser.add_argument_group("Quantization")
    quant_group.add_argument("--no-4bit", action="store_true", help="Disable 4-bit quantization")
    quant_group.add_argument("--use-8bit", action="store_true", help="Use 8-bit quantization instead")
    
    # Other arguments
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data without training")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")
    
    args = parser.parse_args()
    
    print("="*70)
    print("URA TAX ASSISTANT - FINE-TUNING PIPELINE")
    print("="*70)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Use preset config if specified
    if args.target:
        config = MODEL_CONFIGS[args.target]
        args.model = config["model_id"]
        args.max_seq_length = config["max_seq_length"]
        args.lora_r = config["lora_r"]
        args.lora_alpha = config["lora_alpha"]
        print(f"\n📋 Using preset: {args.target}")
        print(f"   Model:      {args.model}")
        print(f"   Seq length: {args.max_seq_length}")
        print(f"   LoRA r:     {args.lora_r}")
    
    # Find training data if not specified
    if args.data is None:
        args.data = find_training_data()
        if args.data is None:
            print("\n❌ No training data found!")
            print("   Expected files in artifacts/ or Data/:")
            for f in TRAINING_DATA_FILES:
                print(f"   - {f}")
            print("\n   Run data_augmentation.py first to generate training data.")
            sys.exit(1)
        print(f"\n📂 Auto-detected training data: {args.data}")
    
    # Set default output directory
    if args.output is None:
        model_name = args.model.split("/")[-1].lower()
        args.output = OUTPUT_DIR / f"ura-{model_name}-finetuned"
    
    # Load data
    dataset = load_training_data(args.data, args.synthetic)
    
    # Determine model type
    model_type = "llama" if "llama" in args.model.lower() else "gemma"
    
    # Format for the model
    print(f"\n🔄 Formatting data for {model_type}...")
    if model_type == "llama":
        dataset = dataset.map(format_for_llama)
    else:
        dataset = dataset.map(format_for_gemma)
    
    # Filter examples without valid 'text' field
    original_len = len(dataset)
    dataset = dataset.filter(lambda x: "text" in x and x["text"] and len(x["text"]) > 10)
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
        print(f"   Output:           {args.output}")
        
        # Show sample
        if args.verbose and len(dataset) > 0:
            print("\n📝 Sample formatted example:")
            sample = dataset[0]["text"][:500]
            print(f"   {sample}...")
        return
    
    # Setup model
    model, tokenizer = setup_model_and_tokenizer(
        args.model,
        use_4bit=not args.no_4bit and not args.use_8bit,
        use_8bit=args.use_8bit,
    )
    
    # Apply LoRA
    lora_config = {
        "r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
    }
    model = apply_lora(model, lora_config)
    
    # Ensure output directory exists
    args.output.mkdir(parents=True, exist_ok=True)
    
    # Train
    train(
        model, tokenizer, dataset, args.output,
        max_seq_length=args.max_seq_length,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        warmup_ratio=args.warmup_ratio,
        model_type=model_type,
    )
    
    print("\n" + "="*70)
    print("✓ FINE-TUNING COMPLETE")
    print("="*70)
    print(f"\nModel saved to: {args.output / 'final'}")
    print(f"\nNext steps:")
    print(f"  1. Test the model: python ml/scripts/inference.py --model {args.output / 'final'}")
    print(f"  2. Push to HuggingFace: huggingface-cli upload <your-username>/ura-tax-assistant {args.output / 'final'}")


if __name__ == "__main__":
    main()
