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
from typing import Optional, List

import pandas as pd

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None
    print("Warning: pymupdf4llm not installed. PDF processing will be skipped.")

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    HAS_LANGCHAIN_SPLITTER = True
except ImportError:
    RecursiveCharacterTextSplitter = None  # type: ignore
    HAS_LANGCHAIN_SPLITTER = False
    print("Warning: langchain_text_splitters not installed. Using basic chunking.")

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Standard directory structure: Data/ folder contains all data
DATA_ROOT = PROJECT_ROOT / "Data"
DATASETS_DIR = DATA_ROOT / "dataset"
PDF_DIR = DATA_ROOT / "pdfs"
TTT_DIR = DATA_ROOT / "TTT"
LGAUDIO_DIR = DATA_ROOT / "lgaudio"
OUTPUT_DIR = DATA_ROOT / "artifacts"

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
    return ' '.join(text.split()[:max_words])


def smart_chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """
    Split text into semantically meaningful chunks without fragmentation.
    Uses LangChain's RecursiveCharacterTextSplitter when available,
    otherwise falls back to sentence-aware splitting.
    """
    if not text or len(text.strip()) < 50:
        return []
    
    if HAS_LANGCHAIN_SPLITTER and RecursiveCharacterTextSplitter is not None:
        # Use LangChain's smart splitter - splits on paragraphs, sentences, then words
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""],
            keep_separator=True,
        )
        chunks = splitter.split_text(text)
        return [c.strip() for c in chunks if len(c.strip()) > 30]
    
    # Fallback: sentence-aware splitting
    # First, split into sentences
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, text)
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
            
        sentence_len = len(sentence)
        
        # If adding this sentence exceeds chunk size, finalize current chunk
        if current_length + sentence_len > chunk_size and current_chunk:
            chunk_text = ' '.join(current_chunk)
            if len(chunk_text) > 30:
                chunks.append(chunk_text)
            
            # Keep last sentence for overlap (semantic continuity)
            if chunk_overlap > 0 and current_chunk:
                overlap_sentences = []
                overlap_len = 0
                for s in reversed(current_chunk):
                    if overlap_len + len(s) <= chunk_overlap:
                        overlap_sentences.insert(0, s)
                        overlap_len += len(s)
                    else:
                        break
                current_chunk = overlap_sentences
                current_length = overlap_len
            else:
                current_chunk = []
                current_length = 0
        
        current_chunk.append(sentence)
        current_length += sentence_len
    
    # Add final chunk
    if current_chunk:
        chunk_text = ' '.join(current_chunk)
        if len(chunk_text) > 30:
            chunks.append(chunk_text)
    
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
    """Load and chunk PDF content using smart splitting."""
    if pymupdf4llm is None:
        print("Skipping PDF loading (pymupdf4llm not installed)")
        return []
    
    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files in {PDF_DIR}")
    
    pdf_chunks = []
    for path in pdf_files:
        try:
            md_result = pymupdf4llm.to_markdown(path)
            # Handle both string and list return types
            if isinstance(md_result, list):
                # If it's a list of dicts (page chunks), join their text
                text = clean_text("\n\n".join(
                    chunk.get('text', str(chunk)) if isinstance(chunk, dict) else str(chunk)
                    for chunk in md_result
                ))
            else:
                text = clean_text(str(md_result))
            
            if len(text) > 100:
                # Use smart chunking to avoid fragmentation
                chunks = smart_chunk_text(text, chunk_size=600, chunk_overlap=80)
                for i, chunk in enumerate(chunks):
                    if len(chunk) > 50:
                        pdf_chunks.append({
                            'text': chunk,
                            'source': path.name,
                            'chunk_id': i,
                            'category': path.stem.replace('-', ' ').replace('_', ' ').lower()
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
                        help="Directory containing CSV FAQ files (default: Data/dataset)")
    parser.add_argument("--pdf-dir", type=str, default=None,
                        help="Directory containing PDF documents (default: Data/pdfs)")
    parser.add_argument("--luganda-dir", type=str, default=None,
                        help="Directory containing Luganda/TTT data (default: Data/TTT)")
    
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
    
    # Resolve directories - use CLI args if provided, otherwise use defaults
    def resolve_path(cli_arg: Optional[str], default_path: Path) -> Path:
        if cli_arg:
            path = Path(cli_arg)
            # Handle both absolute and relative paths
            if path.is_absolute():
                return path
            # Check if path exists relative to cwd
            if path.exists():
                return path.resolve()
            # Otherwise resolve relative to PROJECT_ROOT
            return PROJECT_ROOT / path
        return default_path
    
    DATASETS_DIR = resolve_path(args.csv_dir, DATA_ROOT / "dataset")
    PDF_DIR = resolve_path(args.pdf_dir, DATA_ROOT / "pdfs")
    TTT_DIR = resolve_path(args.luganda_dir, DATA_ROOT / "TTT")
    
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
