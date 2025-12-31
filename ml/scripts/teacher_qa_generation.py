#!/usr/bin/env python3
"""
Teacher Model QA Generation Script
Uses Llama-3.2-3B as a "Teacher" to generate high-quality questions from PDF chunks.

This is a synthetic data generation technique where a capable model creates
training data for fine-tuning smaller/faster models.

Usage:
    python ml/scripts/teacher_qa_generation.py --output artifacts/teacher_qa.jsonl
    python ml/scripts/teacher_qa_generation.py --chunks 100 --questions 5 --batch-size 4
"""

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, asdict

import torch

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None
    print("Warning: pymupdf4llm not installed. Run: pip install pymupdf4llm")

try:
    from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
except ImportError:
    print("Error: transformers not installed. Run: pip install transformers accelerate")
    exit(1)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_ROOT = PROJECT_ROOT / "Data"
PDF_DIR = DATA_ROOT / "pdfs"
OUTPUT_DIR = DATA_ROOT / "artifacts"

# Teacher model configuration
TEACHER_MODEL = "google/flan-t5-small"
FALLBACK_MODEL = "distilgpt2"  # Compatible causal LM fallback

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_NEW_TOKENS = 512
TEMPERATURE = 0.7
TOP_P = 0.9

# Question generation settings
DEFAULT_QUESTIONS_PER_CHUNK = 5
DEFAULT_CHUNK_SIZE = 400
DEFAULT_CHUNK_OVERLAP = 50


@dataclass
class GeneratedQA:
    """Generated question-answer pair."""
    question: str
    answer: str
    chunk_text: str
    source_pdf: str
    chunk_id: int
    question_type: str
    confidence: float = 1.0


# =============================================================================
# Text Processing
# =============================================================================

def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s.,;:!?\'\"()\-–—]', '', text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    start = 0
    
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = ' '.join(words[start:end])
        if len(chunk) > 50:  # Minimum chunk size
            chunks.append(chunk)
        start = end - overlap if end < len(words) else len(words)
    
    return chunks


def load_pdf_chunks(
    pdf_dir: Path,
    chunk_size: int = 400,
    chunk_overlap: int = 50,
    max_pdfs: Optional[int] = None
) -> list[dict]:
    """Load and chunk all PDFs."""
    if pymupdf4llm is None:
        print("❌ pymupdf4llm not installed")
        return []
    
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if max_pdfs:
        pdf_files = pdf_files[:max_pdfs]
    
    print(f"📄 Loading {len(pdf_files)} PDF files...")
    
    all_chunks = []
    for pdf_path in pdf_files:
        try:
            md_result = pymupdf4llm.to_markdown(pdf_path)
            # Handle both string and list return types
            if isinstance(md_result, list):
                text = clean_text("\n\n".join(
                    chunk.get('text', str(chunk)) if isinstance(chunk, dict) else str(chunk)
                    for chunk in md_result
                ))
            else:
                text = clean_text(str(md_result))
            
            if len(text) > 100:
                chunks = chunk_text(text, chunk_size, chunk_overlap)
                for i, chunk in enumerate(chunks):
                    all_chunks.append({
                        'text': chunk,
                        'source': pdf_path.name,
                        'chunk_id': i,
                        'total_chunks': len(chunks)
                    })
                print(f"  ✓ {pdf_path.name}: {len(chunks)} chunks")
        except Exception as e:
            print(f"  ✗ {pdf_path.name}: {e}")
    
    print(f"\n📊 Total chunks: {len(all_chunks)}")
    return all_chunks


# =============================================================================
# Teacher Model
# =============================================================================
class TeacherModel:
    """Teacher model for generating synthetic QA pairs."""
    
    def __init__(self, model_name: str = TEACHER_MODEL, device: str = DEVICE):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = None
        self.pipeline = None
        
    def load(self) -> bool:
        """Load the teacher model and tokenizer."""
        try:
            from huggingface_hub import login
            import os
            
            # Authenticate with Hugging Face if token is available
            hf_token = os.getenv("HF_TOKEN")
            if hf_token:
                login(hf_token)
            
            models_to_try = [
                #"meta-llama/Llama-3.2-3B-Instruct",  # Primary (gated)
                "google/flan-t5-small",              # Public instruction-tuned
                "distilgpt2",                        # Last resort causal LM
            ]
            
            for model_name in models_to_try:
                try:
                    print(f"Trying to load model: {model_name}")
                    self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                    
                    # Use correct model class based on type
                    if "t5" in model_name.lower():
                        from transformers import T5ForConditionalGeneration
                        self.model = T5ForConditionalGeneration.from_pretrained(
                            model_name,
                            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                            device_map="auto" if self.device == "cuda" else None,
                        )
                    else:
                        self.model = AutoModelForCausalLM.from_pretrained(
                            model_name,
                            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                            device_map="auto" if self.device == "cuda" else None,
                            low_cpu_mem_usage=True,
                        )
                    
                    # Note: If device_map="auto", model is already on GPU, don't call .to()
                    if self.device == "cuda" and not hasattr(self.model, 'hf_device_map'):
                        self.model.to(self.device)
                    
                    # Create pipeline based on model type
                    if "t5" in model_name.lower():
                        from transformers import pipeline as t5_pipeline
                        self.pipeline = t5_pipeline(
                            "text2text-generation",
                            model=self.model,
                            tokenizer=self.tokenizer,
                            device=0 if self.device == "cuda" else -1,
                            max_length=512,  # Limit output length
                            temperature=0.7,
                            do_sample=True,
                        )
                    else:
                        self.pipeline = pipeline(
                            "text-generation",
                            model=self.model,
                            tokenizer=self.tokenizer,
                            device=0 if self.device == "cuda" else -1,
                            max_new_tokens=512,
                            temperature=0.7,
                            do_sample=True,
                            pad_token_id=self.tokenizer.eos_token_id,
                        )
                    print("✅ Teacher model loaded successfully")
                    return True
                except Exception as e:
                    print(f"❌ Failed to load {model_name}: {e}")
                    continue
            
            raise RuntimeError("All teacher models failed to load.")
        except Exception as e:
            print(f"❌ Failed to load teacher model: {e}")
            return False
        
    def generate_questions(
        self,
        chunk_text: str,
        num_questions: int = 5,
        context: str = "Uganda Revenue Authority (URA) tax documentation"
    ) -> list[dict]:
        """Generate questions from a text chunk."""
        
        # Truncate chunk if too long to avoid tokenization errors
        max_chunk_length = 1000  # Adjust based on model limits
        if len(chunk_text) > max_chunk_length:
            chunk_text = chunk_text[:max_chunk_length] + "..."
        
        if "t5" in self.model_name.lower():
            # T5-style prompt
            prompt = f"""Generate {num_questions} questions and answers from this text about {context}. Output as JSON array: [{{"question": "...", "answer": "...", "type": "factual"}}]

Text: {chunk_text}

JSON:"""
        else:
            # Causal LM prompt (simplified for distilgpt2)
            prompt = f"""Context: {context}

Text: {chunk_text}

Generate {num_questions} questions and answers in JSON format:
[
  {{"question": "...", "answer": "...", "type": "factual"}},
  ...
]

Output:"""

        try:
            if self.pipeline is None:
                print("      ✗ Pipeline not initialized")
                return []
            
            # Generate response
            if "t5" in self.model_name.lower():
                result = self.pipeline(prompt)[0]['generated_text']
            else:
                result = self.pipeline(prompt)[0]['generated_text']
                # Extract generated part
                if isinstance(result, str) and len(result) > len(prompt):
                    result = result[len(prompt):].strip()
            
            # Parse JSON
            questions = self._parse_questions(result, num_questions)
            return questions
            
        except Exception as e:
            print(f"      ✗ Generation error: {e}")
            return []

# =============================================================================
# QA Generation Pipeline
# =============================================================================

def generate_qa_dataset(
    chunks: list[dict],
    teacher: TeacherModel,
    questions_per_chunk: int = 5,
    max_chunks: Optional[int] = None,
    save_interval: int = 10,
    output_path: Optional[Path] = None,
) -> list[GeneratedQA]:
    """Generate QA pairs from all chunks using teacher model."""
    
    if max_chunks:
        chunks = chunks[:max_chunks]
    
    print(f"\n🎓 Generating questions for {len(chunks)} chunks")
    print(f"   Questions per chunk: {questions_per_chunk}")
    print(f"   Expected total: ~{len(chunks) * questions_per_chunk} QA pairs")
    
    all_qa_pairs = []
    start_time = time.time()
    
    for i, chunk in enumerate(chunks):
        chunk_start = time.time()
        
        print(f"\n[{i+1}/{len(chunks)}] Processing {chunk['source']} (chunk {chunk['chunk_id']+1}/{chunk['total_chunks']})")
        
        # Generate questions
        questions = teacher.generate_questions(
            chunk['text'],
            num_questions=questions_per_chunk
        )
        
        # Create QA pairs
        for q in questions:
            qa = GeneratedQA(
                question=q['question'],
                answer=q['answer'],
                chunk_text=chunk['text'],
                source_pdf=chunk['source'],
                chunk_id=chunk['chunk_id'],
                question_type=q.get('type', 'factual'),
            )
            all_qa_pairs.append(qa)
        
        chunk_time = time.time() - chunk_start
        print(f"   ✓ Generated {len(questions)} questions ({chunk_time:.1f}s)")
        
        # Periodic save
        if output_path and (i + 1) % save_interval == 0:
            _save_checkpoint(all_qa_pairs, output_path)
    
    total_time = time.time() - start_time
    print(f"\n✅ Generation complete!")
    print(f"   Total QA pairs: {len(all_qa_pairs)}")
    print(f"   Total time: {total_time/60:.1f} minutes")
    print(f"   Avg time per chunk: {total_time/len(chunks):.1f}s")
    
    return all_qa_pairs


def _save_checkpoint(qa_pairs: list[GeneratedQA], output_path: Path):
    """Save checkpoint during generation."""
    checkpoint_path = output_path.with_suffix('.checkpoint.jsonl')
    with open(checkpoint_path, 'w', encoding='utf-8') as f:
        for qa in qa_pairs:
            f.write(json.dumps(asdict(qa), ensure_ascii=False) + '\n')
    print(f"   💾 Checkpoint saved: {len(qa_pairs)} pairs")


# =============================================================================
# Export Functions
# =============================================================================

def export_qa_pairs(
    qa_pairs: list[GeneratedQA],
    output_path: Path,
    formats: list[str] = ['jsonl', 'gemma', 'instruction'],
    also_save_to_data: bool = True,
):
    """Export QA pairs in multiple formats.
    
    Args:
        qa_pairs: List of generated QA pairs
        output_path: Primary output path (in artifacts/)
        formats: Export formats to generate
        also_save_to_data: If True, also save to Data/ folder for Kaggle zip inclusion
    """
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Also save to Data folder if requested (for Kaggle dataset inclusion)
    data_output_path = DATA_ROOT / output_path.name if also_save_to_data else None
    if data_output_path:
        data_output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # JSONL format (raw)
    if 'jsonl' in formats:
        jsonl_path = output_path.with_suffix('.jsonl')
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for qa in qa_pairs:
                f.write(json.dumps(asdict(qa), ensure_ascii=False) + '\n')
        print(f"✓ Exported JSONL: {jsonl_path}")
        
        # Also save to Data folder for Kaggle
        if data_output_path:
            data_jsonl = data_output_path.with_suffix('.jsonl')
            shutil.copy(jsonl_path, data_jsonl)
            print(f"  → Also saved to: {data_jsonl}")
    
    # Gemma format
    if 'gemma' in formats:
        gemma_path = output_path.with_name(output_path.stem + '_gemma.jsonl')
        with open(gemma_path, 'w', encoding='utf-8') as f:
            for qa in qa_pairs:
                formatted = {
                    'text': f"<start_of_turn>user\n{qa.question}<end_of_turn>\n<start_of_turn>model\n{qa.answer}<end_of_turn>",
                    'source': qa.source_pdf,
                    'type': qa.question_type,
                }
                f.write(json.dumps(formatted, ensure_ascii=False) + '\n')
        print(f"✓ Exported Gemma format: {gemma_path}")
        
        # Also save to Data folder for Kaggle
        if data_output_path:
            data_gemma = data_output_path.with_name(data_output_path.stem + '_gemma.jsonl')
            shutil.copy(gemma_path, data_gemma)
            print(f"  → Also saved to: {data_gemma}")
    
    # Instruction format
    if 'instruction' in formats:
        inst_path = output_path.with_name(output_path.stem + '_instruction.jsonl')
        with open(inst_path, 'w', encoding='utf-8') as f:
            for qa in qa_pairs:
                formatted = {
                    'instruction': qa.question,
                    'input': '',
                    'output': qa.answer,
                    'context': qa.chunk_text[:500],  # Truncate context
                    'source': qa.source_pdf,
                    'type': qa.question_type,
                }
                f.write(json.dumps(formatted, ensure_ascii=False) + '\n')
        print(f"✓ Exported Instruction format: {inst_path}")
    
    # Statistics
    print(f"\n📊 Export Statistics:")
    print(f"   Total QA pairs: {len(qa_pairs)}")
    
    # By source
    sources = {}
    for qa in qa_pairs:
        sources[qa.source_pdf] = sources.get(qa.source_pdf, 0) + 1
    print(f"   Sources: {len(sources)} PDFs")
    
    # By type
    types = {}
    for qa in qa_pairs:
        types[qa.question_type] = types.get(qa.question_type, 0) + 1
    print(f"   Question types: {types}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate QA pairs from PDFs using teacher model (Llama-3.2-3B)"
    )
    parser.add_argument(
        "--output", type=str,
        default=str(OUTPUT_DIR / "teacher_qa"),
        help="Output file path (without extension)"
    )
    parser.add_argument(
        "--questions", type=int,
        default=DEFAULT_QUESTIONS_PER_CHUNK,
        help=f"Questions per chunk (default: {DEFAULT_QUESTIONS_PER_CHUNK})"
    )
    parser.add_argument(
        "--chunks", type=int,
        default=None,
        help="Max chunks to process (default: all)"
    )
    parser.add_argument(
        "--chunk-size", type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Words per chunk (default: {DEFAULT_CHUNK_SIZE})"
    )
    parser.add_argument(
        "--max-pdfs", type=int,
        default=None,
        help="Max PDFs to process (default: all)"
    )
    parser.add_argument(
        "--model", type=str,
        default=TEACHER_MODEL,
        help=f"Teacher model (default: {TEACHER_MODEL})"
    )
    parser.add_argument(
        "--formats", type=str,
        default="jsonl,gemma,instruction",
        help="Export formats (comma-separated)"
    )
    parser.add_argument(
        "--no-data-copy", action="store_true",
        help="Don't copy output to Data/ folder (for Kaggle zip inclusion)"
    )
    
    args = parser.parse_args()
    
    print("="*70)
    print("🎓 TEACHER MODEL QA GENERATION")
    print("="*70)
    print(f"\nTeacher Model: {args.model}")
    print(f"Questions per chunk: {args.questions}")
    print(f"Device: {DEVICE}")
    
    # Load PDF chunks
    chunks = load_pdf_chunks(
        PDF_DIR,
        chunk_size=args.chunk_size,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        max_pdfs=args.max_pdfs
    )
    
    if not chunks:
        print("❌ No PDF chunks found. Check Data/pdfs/ folder.")
        return
    
    # Initialize teacher model
    teacher = TeacherModel(model_name=args.model)
    if not teacher.load():
        print("❌ Failed to load teacher model")
        return
    
    # Generate QA pairs
    output_path = Path(args.output)
    qa_pairs = generate_qa_dataset(
        chunks=chunks,
        teacher=teacher,
        questions_per_chunk=args.questions,
        max_chunks=args.chunks,
        output_path=output_path,
    )
    
    if not qa_pairs:
        print("❌ No QA pairs generated")
        return
    
    # Export
    export_formats = [f.strip() for f in args.formats.split(',')]
    export_qa_pairs(
        qa_pairs, 
        output_path, 
        formats=export_formats,
        also_save_to_data=not args.no_data_copy
    )
    
    print("\n✅ Teacher QA generation complete!")


if __name__ == "__main__":
    main()