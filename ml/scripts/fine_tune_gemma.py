#!/usr/bin/env python3
"""
Fine-tune Gemma-2-2B (or Llama) on URA tax data using LoRA.

This script uses the output from:
  - data_augmentation.py (training_data.jsonl)
  - teacher_qa_generation.py (teacher_qa_gemma.jsonl)

Usage:
    # Step 1: Generate training data
    python ml/scripts/data_augmentation.py --output artifacts/training_data.jsonl
    
    # Step 2: Generate synthetic QA (optional)
    python ml/scripts/teacher_qa_generation.py --output artifacts/teacher_qa
    
    # Step 3: Fine-tune the model
    python ml/scripts/fine_tune_gemma.py \
        --data artifacts/training_data.jsonl \
        --synthetic artifacts/teacher_qa_gemma.jsonl \
        --target web_high_accuracy

Requirements:
    pip install transformers datasets peft accelerate bitsandbytes trl
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"

# Model configurations for different deployment targets
MODEL_CONFIGS = {
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
DEFAULT_LORA_CONFIG = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "bias": "none",
    "task_type": "CAUSAL_LM",
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
}


def check_dependencies():
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
    return True


# ============================================================================
# DATA LOADING
# ============================================================================

def load_training_data(data_path: Path, synthetic_path: Optional[Path] = None):
    """Load and combine training data from multiple sources."""
    from datasets import Dataset, concatenate_datasets
    
    datasets_to_combine = []
    
    # Load main training data
    if data_path.exists():
        print(f"📂 Loading main training data: {data_path}")
        
        data = []
        with open(data_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
        main_dataset = Dataset.from_list(data)
        datasets_to_combine.append(main_dataset)
        print(f"   Loaded {len(main_dataset)} examples")
    else:
        print(f"⚠️  Main training data not found: {data_path}")
    
    # Load synthetic QA data
    if synthetic_path and synthetic_path.exists():
        print(f"📂 Loading synthetic QA data: {synthetic_path}")
        
        synthetic_data = []
        with open(synthetic_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    synthetic_data.append(json.loads(line))
        
        synthetic_dataset = Dataset.from_list(synthetic_data)
        datasets_to_combine.append(synthetic_dataset)
        print(f"   Loaded {len(synthetic_dataset)} synthetic examples")
    
    if not datasets_to_combine:
        raise ValueError("No training data found!")
    
    if len(datasets_to_combine) == 1:
        combined = datasets_to_combine[0]
    else:
        combined = concatenate_datasets(datasets_to_combine)
    
    print(f"\n✓ Total training examples: {len(combined)}")
    return combined


def format_for_gemma(example: dict) -> dict:
    """Format examples for Gemma instruction tuning."""
    
    # If already in Gemma format
    if "text" in example and "<start_of_turn>" in str(example.get("text", "")):
        return example
    
    # If in instruction format
    if "instruction" in example:
        instruction = example["instruction"]
        input_text = example.get("input", "")
        output = example.get("output", "")
        
        user_content = f"{instruction}\n\n{input_text}" if input_text else instruction
        
        text = (
            f"<start_of_turn>user\n{user_content}<end_of_turn>\n"
            f"<start_of_turn>model\n{output}<end_of_turn>"
        )
        return {"text": text}
    
    # If in QA format
    if "question" in example and "answer" in example:
        text = (
            f"<start_of_turn>user\n{example['question']}<end_of_turn>\n"
            f"<start_of_turn>model\n{example['answer']}<end_of_turn>"
        )
        return {"text": text}
    
    return example


def format_for_llama(example: dict) -> dict:
    """Format examples for Llama instruction tuning."""
    
    if "instruction" in example:
        instruction = example["instruction"]
        input_text = example.get("input", "")
        output = example.get("output", "")
        
        user_content = f"{instruction}\n\n{input_text}" if input_text else instruction
        
        text = (
            f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
            f"{user_content}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{output}<|eot_id|>"
        )
        return {"text": text}
    
    if "question" in example and "answer" in example:
        text = (
            f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
            f"{example['question']}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{example['answer']}<|eot_id|>"
        )
        return {"text": text}
    
    return example


# ============================================================================
# MODEL SETUP
# ============================================================================

def setup_model_and_tokenizer(model_id: str, use_4bit: bool = True):
    """Load model with quantization for efficient fine-tuning."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import prepare_model_for_kbit_training
    
    print(f"\n🔧 Loading model: {model_id}")
    
    # Quantization config
    if use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        print("   Using 4-bit quantization (QLoRA)")
    else:
        bnb_config = None
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    # Load model
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    
    if use_4bit:
        model = prepare_model_for_kbit_training(model)
    
    model.gradient_checkpointing_enable()
    
    print(f"   Model loaded: {model.num_parameters():,} parameters")
    
    return model, tokenizer


def apply_lora(model, config: dict = None):
    """Apply LoRA adapters to the model."""
    from peft import LoraConfig, get_peft_model
    
    lora_config = config or DEFAULT_LORA_CONFIG
    
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
    print(f"   Rank (r): {lora_config['r']}")
    print(f"   Alpha: {lora_config['lora_alpha']}")
    print(f"   Trainable: {trainable:,} ({100 * trainable / total:.2f}%)")
    
    return model


# ============================================================================
# TRAINING
# ============================================================================

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
):
    """Fine-tune the model using SFTTrainer."""
    from transformers import TrainingArguments
    from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
    
    print(f"\n🚀 Starting training...")
    print(f"   Output: {output_dir}")
    print(f"   Epochs: {num_epochs}")
    print(f"   Batch size: {batch_size} x {gradient_accumulation_steps} = {batch_size * gradient_accumulation_steps}")
    
    # Split dataset
    split = dataset.train_test_split(test_size=0.1, seed=42)
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=num_epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        warmup_ratio=0.03,
        logging_steps=10,
        save_steps=100,
        eval_steps=100,
        evaluation_strategy="steps",
        save_strategy="steps",
        save_total_limit=3,
        load_best_model_at_end=True,
        fp16=True,
        optim="paged_adamw_8bit",
        report_to="none",
        lr_scheduler_type="cosine",
        seed=42,
    )
    
    # Response template for completion-only training
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
    )
    
    print("\n" + "=" * 60)
    print("TRAINING STARTED")
    print("=" * 60)
    
    trainer.train()
    
    # Save
    print("\n💾 Saving model...")
    trainer.save_model(str(output_dir / "final"))
    tokenizer.save_pretrained(str(output_dir / "final"))
    
    # Save metrics
    metrics = trainer.state.log_history
    metrics_path = output_dir / "training_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    
    print(f"✓ Model saved to: {output_dir / 'final'}")
    print(f"✓ Metrics saved to: {metrics_path}")
    
    return trainer


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Fine-tune Gemma on URA tax data")
    
    parser.add_argument("--data", type=Path, default=ARTIFACTS_DIR / "training_data.jsonl")
    parser.add_argument("--synthetic", type=Path, default=None)
    parser.add_argument("--model", type=str, default="google/gemma-2-2b-it")
    parser.add_argument("--target", type=str, choices=list(MODEL_CONFIGS.keys()), default=None)
    parser.add_argument("--output", type=Path, default=ARTIFACTS_DIR / "ura-gemma-finetuned")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate data without training")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("URA TAX ASSISTANT - GEMMA FINE-TUNING")
    print("=" * 70)
    
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
    
    # Load data
    dataset = load_training_data(args.data, args.synthetic)
    
    # Format for the model
    if "gemma" in args.model.lower():
        dataset = dataset.map(format_for_gemma)
    elif "llama" in args.model.lower():
        dataset = dataset.map(format_for_llama)
    
    # Filter examples without 'text' field
    dataset = dataset.filter(lambda x: "text" in x and x["text"])
    print(f"   After formatting: {len(dataset)} examples")
    
    if args.dry_run:
        print("\n✓ Dry run complete - data validated")
        print(f"   Ready to train on {len(dataset)} examples")
        return
    
    # Setup model
    model, tokenizer = setup_model_and_tokenizer(
        args.model,
        use_4bit=not args.no_4bit,
    )
    
    # Apply LoRA
    lora_config = {
        **DEFAULT_LORA_CONFIG,
        "r": args.lora_r,
        "lora_alpha": args.lora_alpha,
    }
    model = apply_lora(model, lora_config)
    
    # Train
    args.output.mkdir(parents=True, exist_ok=True)
    train(
        model, tokenizer, dataset, args.output,
        max_seq_length=args.max_seq_length,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    
    print("\n" + "=" * 70)
    print("✓ FINE-TUNING COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
