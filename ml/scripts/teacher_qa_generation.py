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

# Import pymupdf.layout and pymupdf4llm
try:
    import pymupdf.layout
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None
    pymupdf.layout = None
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
TEACHER_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
FALLBACK_MODEL = "microsoft/Phi-3-mini-4k-instruct"  # Compatible causal LM fallback

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
    """Load and chunk all PDFs using pymupdf4llm with layout support."""
    if pymupdf4llm is None or pymupdf.layout is None:
        print("❌ pymupdf4llm not installed or missing pymupdf.layout")
        return []
    
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if max_pdfs:
        pdf_files = pdf_files[:max_pdfs]
    
    print(f"📄 Loading {len(pdf_files)} PDF files...")
    
    all_chunks = []
    for pdf_path in pdf_files:
        try:
            # Use pymupdf4llm with layout support for better text extraction
            print(f"  Processing {pdf_path.name}...")
            
            # Extract markdown with layout preservation
            md_text = pymupdf4llm.to_markdown(
                str(pdf_path),
                pages=None,  # All pages
                show_progress=False
            )
            
            # Clean the extracted text
            text = clean_text(str(md_text))
            
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
            else:
                print(f"  ⚠ {pdf_path.name}: Text too short ({len(text)} chars)")
                
        except Exception as e:
            print(f"  ✗ {pdf_path.name}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n📊 Total chunks: {len(all_chunks)}")
    return all_chunks


def extract_text_with_layout(pdf_path: Path) -> str:
    """Extract text from PDF with layout preservation using pymupdf.layout."""
    try:
        import pymupdf as fitz
        from typing import cast, Dict, Any
        
        doc = fitz.open(pdf_path)
        text_parts = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Use pymupdf.layout for better text extraction
            text_dict = cast(Dict[str, Any], page.get_text("dict"))
            blocks = text_dict["blocks"]
            
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        line_text = ""
                        for span in line["spans"]:
                            # Preserve font and layout information
                            font_size = span["size"]
                            font_name = span["font"]
                            span_text = span["text"]
                            
                            # Add text with layout hints
                            if font_size > 12:
                                line_text += f"# {span_text} "
                            elif font_size > 10:
                                line_text += f"## {span_text} "
                            else:
                                line_text += span_text
                        
                        text_parts.append(line_text.strip())
        
        doc.close()
        return "\n".join(text_parts)
        
    except Exception as e:
        print(f"Error extracting text with layout from {pdf_path.name}: {e}")
        return ""


# =============================================================================
# Teacher Model
# =============================================================================
class TeacherModel:
    """Teacher model for generating synthetic QA pairs."""
    
    def __init__(self, model_name: str = TEACHER_MODEL, device: str = DEVICE):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
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
                "Qwen/Qwen2.5-1.5B-Instruct",              # Public instruction-tuned
                "microsoft/Phi-3-mini-4k-instruct",        # Small but capable
            ]
            
            for model_name in models_to_try:
                try:
                    print(f"Trying to load model: {model_name}")
                    self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                    
                    # Set padding token if not exists
                    if self.tokenizer.pad_token is None:
                        self.tokenizer.pad_token = self.tokenizer.eos_token
                    
                    # Load model
                    self.model = AutoModelForCausalLM.from_pretrained(
                        model_name,
                        torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                        device_map="auto" if self.device == "cuda" else None,
                        low_cpu_mem_usage=True,
                        trust_remote_code=True,
                    )
                    
                    # Create pipeline
                    self.pipeline = pipeline(
                        "text-generation",
                        model=self.model,
                        tokenizer=self.tokenizer,
                        device=0 if self.device == "cuda" else -1,
                        max_new_tokens=MAX_NEW_TOKENS,
                        temperature=TEMPERATURE,
                        top_p=TOP_P,
                        do_sample=True,
                        pad_token_id=self.tokenizer.eos_token_id,
                    )
                    
                    print(f"✅ Teacher model loaded successfully: {model_name}")
                    self.model_name = model_name
                    return True
                    
                except Exception as e:
                    print(f"❌ Failed to load {model_name}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            raise RuntimeError("All teacher models failed to load.")
            
        except Exception as e:
            print(f"❌ Failed to load teacher model: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _parse_questions(self, generated_text: str, expected_count: int) -> list[dict]:
        """Parse generated text to extract questions and answers."""
        import json
        import re
        
        # Clean the generated text
        text = generated_text.strip()
        
        # Try to extract JSON array (most common pattern)
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if json_match:
            try:
                questions = json.loads(json_match.group(0))
                if isinstance(questions, list):
                    # Validate and clean each question
                    valid_questions = []
                    for q in questions:
                        if isinstance(q, dict) and 'question' in q and 'answer' in q:
                            valid_q = {
                                'question': str(q['question']).strip(),
                                'answer': str(q['answer']).strip(),
                                'type': str(q.get('type', 'factual')).lower()
                            }
                            # Basic validation
                            if valid_q['question'] and valid_q['answer']:
                                valid_questions.append(valid_q)
                    
                    # Limit to expected count
                    return valid_questions[:expected_count]
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                pass
        
        # Alternative JSON patterns
        patterns = [
            r'\{.*?"question".*?"answer".*?\}',  # Single object
            r'"questions".*?\:.*?\[.*?\]',  # Questions array in object
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                try:
                    questions = []
                    for match in matches:
                        data = json.loads(match)
                        if isinstance(data, dict):
                            if 'questions' in data and isinstance(data['questions'], list):
                                questions.extend(data['questions'])
                            elif 'question' in data and 'answer' in data:
                                questions.append(data)
                    
                    if questions:
                        valid_questions = []
                        for q in questions:
                            if isinstance(q, dict) and 'question' in q and 'answer' in q:
                                valid_q = {
                                    'question': str(q['question']).strip(),
                                    'answer': str(q['answer']).strip(),
                                    'type': str(q.get('type', 'factual')).lower()
                                }
                                if valid_q['question'] and valid_q['answer']:
                                    valid_questions.append(valid_q)
                        
                        return valid_questions[:expected_count]
                except json.JSONDecodeError:
                    continue
        
        # Fallback: Parse line by line
        questions = []
        lines = text.split('\n')
        current_q = {}
        
        for line in lines:
            line = line.strip()
            
            # Check for question markers (case insensitive)
            if re.search(r'question\s*\d*[:.]', line, re.IGNORECASE):
                if current_q and 'question' in current_q and 'answer' in current_q:
                    questions.append(current_q)
                    current_q = {}
                
                # Extract question
                match = re.search(r'question\s*\d*[:.]\s*(.+)', line, re.IGNORECASE)
                if match:
                    question = match.group(1).strip()
                    current_q = {'question': question, 'type': 'factual'}
            
            elif re.search(r'answer\s*\d*[:.]', line, re.IGNORECASE) and current_q:
                # Extract answer
                match = re.search(r'answer\s*\d*[:.]\s*(.+)', line, re.IGNORECASE)
                if match:
                    answer = match.group(1).strip()
                    current_q['answer'] = answer
        
        # Add last question if complete
        if current_q and 'question' in current_q and 'answer' in current_q:
            questions.append(current_q)
        
        # If we still don't have questions, try simple Q/A pattern
        if not questions:
            q_pattern = r'Q[:\s]+(.+?)(?=A[:\s]|$)'
            a_pattern = r'A[:\s]+(.+)'
            
            q_matches = re.findall(q_pattern, text, re.DOTALL | re.IGNORECASE)
            a_matches = re.findall(a_pattern, text, re.DOTALL | re.IGNORECASE)
            
            for q, a in zip(q_matches, a_matches):
                if q.strip() and a.strip():
                    questions.append({
                        'question': q.strip(),
                        'answer': a.strip(),
                        'type': 'factual'
                    })
        
        return questions[:expected_count]
        
    def generate_questions(
        self,
        chunk_text: str,
        num_questions: int = 5,
        context: str = "Uganda Revenue Authority (URA) tax documentation"
    ) -> list[dict]:
        """Generate questions from a text chunk."""
        
        if self.pipeline is None:
            print("      ✗ Pipeline not loaded, skipping generation")
            return []
        
        # Truncate chunk if too long to avoid tokenization errors
        max_chunk_length = 2000  # Increased for better context
        if len(chunk_text) > max_chunk_length:
            chunk_text = chunk_text[:max_chunk_length] + "..."
        
        # Enhanced prompt for better JSON generation
        prompt = f"""You are a helpful assistant that generates educational questions and answers from text passages.

CONTEXT: {context}
TEXT PASSAGE:
{chunk_text}

INSTRUCTIONS:
Generate exactly {num_questions} questions and answers based on the text passage above.

REQUIREMENTS:
1. Questions should be factual and answerable directly from the text
2. Answers should be concise and accurate
3. Include different types of questions (factual, conceptual, application)
4. Format output as a JSON array of objects

OUTPUT FORMAT (JSON):
[
  {{
    "question": "What is the main topic?",
    "answer": "The main topic is...",
    "type": "factual"
  }},
  {{
    "question": "How does the process work?",
    "answer": "The process works by...",
    "type": "conceptual"
  }}
]

JSON OUTPUT:"""
        
        try:
            # Generate with the pipeline
            result = self.pipeline(
                prompt,
                max_new_tokens=MAX_NEW_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
                num_return_sequences=1,
            )[0]['generated_text']
            
            # Extract generated part (remove prompt if present)
            if result.startswith(prompt):
                result = result[len(prompt):].strip()
            
            print(f"      Generated text length: {len(result)} chars")
            
            # Parse questions
            questions = self._parse_questions(result, num_questions)
            
            if questions:
                print(f"      Successfully parsed {len(questions)} questions")
            else:
                print(f"      Could not parse questions from generated text")
                # Debug: save problematic generation
                with open("debug_generation.txt", "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*50}\nPROMPT:\n{prompt}\n\nRESULT:\n{result}\n")
            
            return questions
            
        except Exception as e:
            print(f"      ✗ Generation error: {e}")
            import traceback
            traceback.print_exc()
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
        print(f"   Chunk text preview: {chunk['text'][:100]}...")
        
        try:
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
            
        except Exception as e:
            chunk_time = time.time() - chunk_start
            print(f"   ✗ Error processing chunk: {e} ({chunk_time:.1f}s)")
            import traceback
            traceback.print_exc()
        
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
        description="Generate QA pairs from PDFs using teacher model"
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
    parser.add_argument(
        "--use-layout", action="store_true",
        help="Use pymupdf.layout for enhanced text extraction (experimental)"
    )
    
    args = parser.parse_args()
    
    print("="*70)
    print("🎓 TEACHER MODEL QA GENERATION")
    print("="*70)
    print(f"\nTeacher Model: {args.model}")
    print(f"Questions per chunk: {args.questions}")
    print(f"Device: {DEVICE}")
    print(f"Using pymupdf.layout: {args.use_layout}")
    
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