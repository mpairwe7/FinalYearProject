#!/usr/bin/env python3
"""
Data Augmentation Script for URA Tax Chatbot
Combines CSV FAQs and PDF text into Gemma-compatible training format.

Output formats:
- Instruction-tuning format (prompt/response pairs)
- Chat format (multi-turn conversations)
- HuggingFace datasets compatible

Usage:
    python ml/scripts/data_augmentation.py --output artifacts/training_data.jsonl
"""

import argparse
import json
import random
import re
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None
    print("Warning: pymupdf4llm not installed. PDF processing will be skipped.")

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Default directories (can be overridden by CLI args)
DEFAULT_DATASETS_DIR = PROJECT_ROOT / "datasets"
DEFAULT_PDF_DIR = PROJECT_ROOT / "pdfs"
DEFAULT_TTT_DIR = PROJECT_ROOT / "TTT"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts"

# Also check Data/ folder structure
ALT_DATASETS_DIR = PROJECT_ROOT / "Data" / "dataset"
ALT_PDF_DIR = PROJECT_ROOT / "Data" / "pdfs"
ALT_TTT_DIR = PROJECT_ROOT / "Data" / "TTT"

# Initialize globals (will be set in main())
DATASETS_DIR = DEFAULT_DATASETS_DIR
PDF_DIR = DEFAULT_PDF_DIR
TTT_DIR = DEFAULT_TTT_DIR
OUTPUT_DIR = DEFAULT_OUTPUT_DIR

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# Gemma instruction format templates
INSTRUCTION_TEMPLATES = [
    "Answer this tax question: {question}",
    "As a URA customer service assistant, answer: {question}",
    "Help me understand: {question}",
    "Tax query: {question}",
    "URA FAQ: {question}",
    "{question}",
]

# System prompts for chat format
SYSTEM_PROMPTS = [
    "You are a helpful URA (Uganda Revenue Authority) customer service assistant. Answer tax-related questions accurately and concisely.",
    "You are an expert on Ugandan tax laws and URA procedures. Provide clear, accurate answers.",
    "As a URA assistant, help users with their tax questions. Be professional and informative.",
]

# Question generation templates for PDF content
PDF_QUESTION_TEMPLATES = [
    "What does the URA say about {topic}?",
    "Explain the URA policy on {topic}.",
    "How does {topic} work according to URA?",
    "What are the requirements for {topic}?",
    "Tell me about {topic} in Uganda.",
]


# =============================================================================
# Text Processing Utilities
# =============================================================================

def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if pd.isna(text) or not text:
        return ""
    text = str(text)
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove special characters but keep punctuation
    text = re.sub(r'[^\w\s.,;:!?\'\"()-]', '', text)
    return text.strip()


def read_csv_with_fallback(path: Path) -> pd.DataFrame:
    """Read CSV with fallback encodings for non-UTF-8 files."""
    encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    # If all fail, let the last one raise the error
    return pd.read_csv(path)


def extract_topic_from_text(text: str, max_words: int = 5) -> str:
    """Extract a topic phrase from text for question generation."""
    # Get first sentence or first N words
    sentences = re.split(r'[.!?]', text)
    if sentences:
        first_sentence = sentences[0].strip()
        words = first_sentence.split()[:max_words]
        return ' '.join(words).lower()
    return text.split()[:max_words]


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    start = 0
    
    while start < len(words):
        end = start + chunk_size
        chunk = ' '.join(words[start:end])
        chunks.append(chunk)
        start = end - overlap
        
    return chunks


# =============================================================================
# Data Loading
# =============================================================================

def load_csv_faqs() -> pd.DataFrame:
    """Load all CSV/XLSX FAQ files."""
    data_files = sorted(list(DATASETS_DIR.glob("*.csv")) + list(DATASETS_DIR.glob("*.xlsx")))
    print(f"Found {len(data_files)} data files in {DATASETS_DIR}")
    
    question_candidates = {'question', 'questions', 'q'}
    answer_candidates = {'answer', 'answers', 'a', 'response', 'resp'}
    
    frames = []
    for path in data_files:
        try:
            if path.suffix == '.csv':
                df = read_csv_with_fallback(path)
            else:
                df = pd.read_excel(path)
                
            columns_lower = {c.lower(): c for c in df.columns}
            
            q_col = next((columns_lower[c] for c in columns_lower if c in question_candidates), df.columns[0])
            a_col = next((columns_lower[c] for c in columns_lower if c in answer_candidates and columns_lower[c] != q_col), df.columns[-1])
            
            df = df[[q_col, a_col]].rename(columns={q_col: 'question', a_col: 'answer'})
            df['question'] = df['question'].apply(clean_text)
            df['answer'] = df['answer'].apply(clean_text)
            df['source'] = path.name
            df['category'] = path.stem.replace('ura_', '').replace('_faqs', '').replace('_', ' ')
            
            # Filter empty rows
            df = df[(df['question'].str.len() > 10) & (df['answer'].str.len() > 10)]
            frames.append(df)
            
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")
    
    if frames:
        result = pd.concat(frames, ignore_index=True)
        print(f"  Loaded {len(result)} QA pairs from CSVs")
        return result
    
    return pd.DataFrame(columns=['question', 'answer', 'source', 'category'])


def load_pdf_content() -> list[dict]:
    """Load and chunk PDF content."""
    if pymupdf4llm is None:
        print("Skipping PDF loading (pymupdf4llm not installed)")
        return []
    
    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files in {PDF_DIR}")
    
    pdf_chunks = []
    for path in pdf_files:
        try:
            md_text = pymupdf4llm.to_markdown(path)
            text = clean_text(md_text)
            
            if len(text) > 100:
                chunks = chunk_text(text, chunk_size=400, overlap=50)
                for i, chunk in enumerate(chunks):
                    if len(chunk) > 50:
                        pdf_chunks.append({
                            'text': chunk,
                            'source': path.name,
                            'chunk_id': i,
                            'category': path.stem.replace('-', ' ').lower()
                        })
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")
    
    print(f"  Created {len(pdf_chunks)} chunks from PDFs")
    return pdf_chunks


def load_luganda_translations() -> pd.DataFrame:
    """Load English-Luganda parallel data."""
    ttt_files = sorted(list(TTT_DIR.glob("*.csv")) + list(TTT_DIR.glob("*.xlsx")))
    print(f"Found {len(ttt_files)} TTT files in {TTT_DIR}")
    
    translations = []
    for path in ttt_files:
        try:
            if path.suffix == '.csv':
                df = read_csv_with_fallback(path)
            else:
                df = pd.read_excel(path)
                
            cols_lower = {c.lower(): c for c in df.columns}
            
            en_col = next((cols_lower[c] for c in cols_lower if 'english' in c or 'en' == c), None)
            lg_col = next((cols_lower[c] for c in cols_lower if 'luganda' in c or 'lg' == c), None)
            
            if en_col and lg_col:
                for _, row in df.iterrows():
                    en_text = clean_text(row[en_col])
                    lg_text = clean_text(row[lg_col])
                    if en_text and lg_text and len(en_text) > 5 and len(lg_text) > 5:
                        translations.append({
                            'english': en_text,
                            'luganda': lg_text,
                            'source': path.name
                        })
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")
    
    result = pd.DataFrame(translations)
    print(f"  Loaded {len(result)} translation pairs")
    return result


# =============================================================================
# Training Data Generation
# =============================================================================

def create_instruction_format(question: str, answer: str, category: str = "") -> dict:
    """Create instruction-tuning format for Gemma."""
    template = random.choice(INSTRUCTION_TEMPLATES)
    instruction = template.format(question=question)
    
    # Add category context sometimes
    if category and random.random() > 0.5:
        instruction = f"[{category.upper()}] {instruction}"
    
    return {
        "instruction": instruction,
        "input": "",
        "output": answer,
        "category": category,
    }


def create_chat_format(question: str, answer: str, category: str = "") -> dict:
    """Create chat format for Gemma (multi-turn ready)."""
    system = random.choice(SYSTEM_PROMPTS)
    
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ],
        "category": category,
    }


def create_gemma_format(question: str, answer: str) -> dict:
    """Create Gemma-specific instruction format."""
    # Gemma uses <start_of_turn> and <end_of_turn> tokens
    formatted_text = f"<start_of_turn>user\n{question}<end_of_turn>\n<start_of_turn>model\n{answer}<end_of_turn>"
    
    return {
        "text": formatted_text,
        "question": question,
        "answer": answer,
    }


def generate_qa_from_pdf_chunk(chunk: dict) -> Optional[dict]:
    """Generate a QA pair from a PDF chunk."""
    text = chunk['text']
    category = chunk['category']
    
    # Extract topic for question
    topic = extract_topic_from_text(text)
    if len(topic) < 3:
        return None
    
    # Generate question
    template = random.choice(PDF_QUESTION_TEMPLATES)
    question = template.format(topic=topic)
    
    # Use chunk as answer context
    answer = f"According to URA documentation: {text}"
    
    return {
        "question": question,
        "answer": answer,
        "source": chunk['source'],
        "category": category,
        "generated": True,
    }


def create_luganda_training_pair(translation: dict) -> dict:
    """Create training pair with Luganda translation."""
    en_text = translation['english']
    lg_text = translation['luganda']
    
    # Create translation task
    formats = [
        {
            "instruction": f"Translate to Luganda: {en_text}",
            "output": lg_text,
        },
        {
            "instruction": f"Translate to English: {lg_text}",
            "output": en_text,
        },
        {
            "instruction": "Respond in Luganda to this question.",
            "input": en_text,
            "output": lg_text,
        },
    ]
    
    selected = random.choice(formats)
    selected["category"] = "translation"
    selected["source"] = translation['source']
    
    return selected


# =============================================================================
# Data Augmentation
# =============================================================================

def augment_question(question: str) -> list[str]:
    """Generate variations of a question."""
    variations = [question]
    
    # Add politeness variations
    polite_prefixes = ["Please ", "Could you ", "Can you help me understand "]
    for prefix in polite_prefixes:
        if not question.lower().startswith(prefix.lower().split()[0]):
            variations.append(f"{prefix}{question[0].lower()}{question[1:]}")
    
    # Add URA context
    if "ura" not in question.lower():
        variations.append(f"{question} (URA)")
    
    return variations[:3]  # Limit variations


def augment_dataset(
    qa_pairs: list[dict],
    pdf_chunks: list[dict],
    translations: pd.DataFrame,
    augment_factor: int = 2,
) -> list[dict]:
    """Augment and combine all data sources."""
    augmented = []
    
    print(f"\nAugmenting {len(qa_pairs)} QA pairs...")
    
    # Process CSV QA pairs
    for pair in qa_pairs:
        # Original pair in multiple formats
        augmented.append(create_instruction_format(
            pair['question'], pair['answer'], pair.get('category', '')
        ))
        augmented.append(create_chat_format(
            pair['question'], pair['answer'], pair.get('category', '')
        ))
        augmented.append(create_gemma_format(
            pair['question'], pair['answer']
        ))
        
        # Question variations
        if augment_factor > 1:
            for variant in augment_question(pair['question'])[1:]:
                augmented.append(create_instruction_format(
                    variant, pair['answer'], pair.get('category', '')
                ))
    
    # Process PDF chunks
    print(f"Processing {len(pdf_chunks)} PDF chunks...")
    for chunk in pdf_chunks:
        qa = generate_qa_from_pdf_chunk(chunk)
        if qa:
            augmented.append(create_instruction_format(
                qa['question'], qa['answer'], qa['category']
            ))
    
    # Process translations
    if not translations.empty:
        print(f"Processing {len(translations)} translation pairs...")
        for _, row in translations.iterrows():
            augmented.append(create_luganda_training_pair(row.to_dict()))
    
    # Shuffle
    random.shuffle(augmented)
    
    return augmented


# =============================================================================
# Export Functions
# =============================================================================

def export_jsonl(data: list[dict], output_path: Path):
    """Export to JSONL format (one JSON object per line)."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(f"✓ Exported {len(data)} samples to {output_path}")


def export_hf_dataset(data: list[dict], output_path: Path):
    """Export to HuggingFace datasets format."""
    try:
        from datasets import Dataset
        
        # Separate by format
        instruction_data = [d for d in data if 'instruction' in d]
        chat_data = [d for d in data if 'messages' in d]
        gemma_data = [d for d in data if 'text' in d and 'messages' not in d]
        
        # Save instruction format
        if instruction_data:
            ds = Dataset.from_list(instruction_data)
            ds.save_to_disk(str(output_path / 'instruction_format'))
            print(f"✓ Saved {len(instruction_data)} instruction samples")
        
        # Save chat format
        if chat_data:
            ds = Dataset.from_list(chat_data)
            ds.save_to_disk(str(output_path / 'chat_format'))
            print(f"✓ Saved {len(chat_data)} chat samples")
        
        # Save Gemma format
        if gemma_data:
            ds = Dataset.from_list(gemma_data)
            ds.save_to_disk(str(output_path / 'gemma_format'))
            print(f"✓ Saved {len(gemma_data)} Gemma-formatted samples")
            
    except ImportError:
        print("Warning: datasets library not installed. Skipping HF export.")


def export_train_val_split(data: list[dict], output_dir: Path, val_ratio: float = 0.1):
    """Export with train/validation split."""
    random.shuffle(data)
    split_idx = int(len(data) * (1 - val_ratio))
    
    train_data = data[:split_idx]
    val_data = data[split_idx:]
    
    export_jsonl(train_data, output_dir / 'train.jsonl')
    export_jsonl(val_data, output_dir / 'val.jsonl')
    
    print(f"\n📊 Split Statistics:")
    print(f"  Train: {len(train_data)} samples")
    print(f"  Val:   {len(val_data)} samples")


# =============================================================================
# Main
# =============================================================================

def main():
    global DATASETS_DIR, PDF_DIR, TTT_DIR, OUTPUT_DIR
    
    parser = argparse.ArgumentParser(description="Data augmentation for Gemma fine-tuning")
    
    # Directory arguments
    parser.add_argument("--csv-dir", type=str, default=None,
                        help="Directory containing CSV FAQ files")
    parser.add_argument("--pdf-dir", type=str, default=None,
                        help="Directory containing PDF documents")
    parser.add_argument("--luganda-dir", type=str, default=None,
                        help="Directory containing Luganda/TTT data")
    
    # Output arguments
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path for training data")
    parser.add_argument("--gemma-output", type=str, default=None,
                        help="Output file path for Gemma format")
    parser.add_argument("--instruction-output", type=str, default=None,
                        help="Output file path for instruction format")
    
    # Other arguments
    parser.add_argument("--augment-factor", type=int, default=2,
                        help="Augmentation factor for question variations")
    parser.add_argument("--split", action="store_true",
                        help="Create train/val split")
    parser.add_argument("--hf-format", action="store_true",
                        help="Also export in HuggingFace datasets format")
    args = parser.parse_args()
    
    # Resolve directories - check CLI args, then defaults, then alternatives
    def resolve_dir(cli_arg, default_dir, alt_dir):
        if cli_arg:
            path = Path(cli_arg)
            if path.is_absolute():
                return path
            return PROJECT_ROOT / path
        if default_dir.exists():
            return default_dir
        if alt_dir.exists():
            return alt_dir
        return default_dir
    
    DATASETS_DIR = resolve_dir(args.csv_dir, DEFAULT_DATASETS_DIR, ALT_DATASETS_DIR)
    PDF_DIR = resolve_dir(args.pdf_dir, DEFAULT_PDF_DIR, ALT_PDF_DIR)
    TTT_DIR = resolve_dir(args.luganda_dir, DEFAULT_TTT_DIR, ALT_TTT_DIR)
    OUTPUT_DIR = DEFAULT_OUTPUT_DIR
    
    print("="*70)
    print("URA CHATBOT DATA AUGMENTATION")
    print("="*70)
    print(f"\n📁 Directories:")
    print(f"   CSV FAQs:  {DATASETS_DIR} {'✓' if DATASETS_DIR.exists() else '✗'}")
    print(f"   PDFs:      {PDF_DIR} {'✓' if PDF_DIR.exists() else '✗'}")
    print(f"   Luganda:   {TTT_DIR} {'✓' if TTT_DIR.exists() else '✗'}")
    
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load data
    print("\n📂 Loading data sources...")
    qa_df = load_csv_faqs()
    pdf_chunks = load_pdf_content()
    translations = load_luganda_translations()
    
    # Convert QA dataframe to list
    qa_pairs = qa_df.to_dict('records') if not qa_df.empty else []
    
    # Augment
    print("\n🔄 Augmenting dataset...")
    augmented_data = augment_dataset(
        qa_pairs, pdf_chunks, translations, 
        augment_factor=args.augment_factor
    )
    
    print(f"\n📊 Final Dataset Statistics:")
    print(f"  Total samples: {len(augmented_data)}")
    
    # Count by format
    instruction_count = sum(1 for d in augmented_data if 'instruction' in d)
    chat_count = sum(1 for d in augmented_data if 'messages' in d)
    gemma_count = sum(1 for d in augmented_data if 'text' in d and 'messages' not in d)
    print(f"  Instruction format: {instruction_count}")
    print(f"  Chat format: {chat_count}")
    print(f"  Gemma format: {gemma_count}")
    
    # Export
    print("\n💾 Exporting...")
    
    # Determine output paths
    output_path = Path(args.output) if args.output else OUTPUT_DIR / "training_data.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Always export the full dataset
    export_jsonl(augmented_data, output_path)
    
    if args.split:
        export_train_val_split(augmented_data, output_path.parent)
    
    # Export Gemma format if requested
    if args.gemma_output:
        gemma_path = Path(args.gemma_output)
        gemma_path.parent.mkdir(parents=True, exist_ok=True)
        gemma_data = [d for d in augmented_data if 'text' in d]
        export_jsonl(gemma_data, gemma_path)
        print(f"   Gemma format: {gemma_path} ({len(gemma_data)} samples)")
    
    # Export instruction format if requested
    if args.instruction_output:
        instruction_path = Path(args.instruction_output)
        instruction_path.parent.mkdir(parents=True, exist_ok=True)
        instruction_data = [d for d in augmented_data if 'instruction' in d]
        export_jsonl(instruction_data, instruction_path)
        print(f"   Instruction format: {instruction_path} ({len(instruction_data)} samples)")
    
    if args.hf_format:
        hf_dir = output_path.parent / 'hf_dataset'
        hf_dir.mkdir(exist_ok=True)
        export_hf_dataset(augmented_data, hf_dir)
    
    print("\n✅ Data augmentation complete!")
    print(f"   Output: {output_path}")


if __name__ == "__main__":
    main()
