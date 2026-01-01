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
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

import pandas as pd
import numpy as np

# Import pymupdf.layout and pymupdf4llm for enhanced PDF processing
try:
    import pymupdf.layout
    import pymupdf4llm
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False
    print("Warning: pymupdf4llm not installed. PDF processing will be limited.")
    print("Install with: pip install pymupdf4llm")

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    HAS_LANGCHAIN_SPLITTER = True
except ImportError:
    RecursiveCharacterTextSplitter = None  # type: ignore
    HAS_LANGCHAIN_SPLITTER = False
    print("Warning: langchain_text_splitters not installed. Using enhanced chunking.")

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
np.random.seed(RANDOM_SEED)

# Gemma instruction format templates
INSTRUCTION_TEMPLATES = [
    "Answer this tax question: {question}",
    "As a URA customer service assistant, answer: {question}",
    "Help me understand: {question}",
    "Tax query: {question}",
    "URA FAQ: {question}",
    "{question}",
    "Provide guidance on: {question}",
    "What is the URA policy regarding: {question}",
]

# System prompts for chat format
SYSTEM_PROMPTS = [
    "You are a helpful URA (Uganda Revenue Authority) customer service assistant. Answer tax-related questions accurately and concisely.",
    "You are an expert on Ugandan tax laws and URA procedures. Provide clear, accurate answers.",
    "As a URA assistant, help users with their tax questions. Be professional and informative.",
    "You are a URA tax expert. Provide detailed, accurate information about Ugandan tax regulations.",
    "Assist users with URA procedures and tax compliance. Be helpful and precise.",
]

# Question generation templates for PDF content
PDF_QUESTION_TEMPLATES = [
    "What does the URA say about {topic}?",
    "Explain the URA policy on {topic}.",
    "How does {topic} work according to URA?",
    "What are the requirements for {topic}?",
    "Tell me about {topic} in Uganda.",
    "What is the procedure for {topic} with URA?",
    "How do I comply with {topic} regulations?",
    "Explain {topic} in the context of URA.",
]

# PDF-specific prompt templates
PDF_PROMPT_TEMPLATES = [
    "Based on the following URA document excerpt, answer the question: {question}\n\nExcerpt: {context}",
    "Using this URA documentation, provide information about: {question}\n\nDocumentation: {context}",
    "Context from URA guidelines: {context}\n\nQuestion: {question}",
]

# =============================================================================
# Enhanced Text Processing Utilities
# =============================================================================

def clean_text(text: str, preserve_formatting: bool = False) -> str:
    """Clean and normalize text with options for PDF layout preservation."""
    if pd.isna(text) or not text:
        return ""
    
    text = str(text)
    
    # Remove excessive whitespace but preserve paragraph breaks if requested
    if preserve_formatting:
        # Replace multiple newlines with double newline (paragraph break)
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        # Collapse multiple spaces but keep single spaces
        text = re.sub(r'[ \t]+', ' ', text)
    else:
        # Standard cleaning
        text = re.sub(r'\s+', ' ', text)
    
    # Remove special characters but keep punctuation and basic symbols
    text = re.sub(r'[^\w\s.,;:!?\'\"()\-\–\—]', '', text)
    
    # Remove leading/trailing whitespace
    text = text.strip()
    
    return text


def read_csv_with_fallback(path: Path) -> pd.DataFrame:
    """Read CSV with fallback encodings for non-UTF-8 files."""
    encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-8-sig']
    for enc in encodings:
        try:
            df = pd.read_csv(path, encoding=enc, on_bad_lines='skip')
            if not df.empty:
                return df
        except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
            continue
    
    # If all fail, try with low-level reading
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        # Try to parse as CSV with different separators
        for sep in [',', ';', '\t', '|']:
            try:
                df = pd.read_csv(path, sep=sep, engine='python', on_bad_lines='skip')
                if len(df.columns) > 1 and not df.empty:
                    return df
            except:
                continue
    except Exception as e:
        print(f"  ⚠️ All CSV reading attempts failed for {path.name}: {e}")
    
    return pd.DataFrame()


def extract_topic_from_text(text: str, max_words: int = 5) -> str:
    """Extract a topic phrase from text for question generation."""
    # Get first meaningful sentence
    sentences = re.split(r'[.!?]', text)
    if sentences:
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence.split()) >= 3:  # Need at least 3 words for a meaningful topic
                words = sentence.split()[:max_words]
                topic = ' '.join(words).lower()
                # Clean up the topic
                topic = re.sub(r'[^\w\s]', '', topic)
                return topic
    
    # Fallback: first N words
    words = text.split()[:max_words]
    topic = ' '.join(words).lower()
    topic = re.sub(r'[^\w\s]', '', topic)
    return topic


# ...existing code...

def extract_pdf_with_layout(pdf_path: Path) -> Dict[str, Any]:
    """
    Extract PDF content with layout preservation using pymupdf.layout.
    Returns structured content with metadata.
    """
    if not PYPDF_AVAILABLE:
        return {"text": "", "pages": [], "metadata": {}}
    
    try:
        import pymupdf as fitz

        doc = fitz.open(pdf_path)
        metadata = doc.metadata or {}
        content = {
            "text": "",
            "pages": [],
            "metadata": {
                "title": metadata.get("title", pdf_path.stem),
                "author": metadata.get("author", ""),
                "subject": metadata.get("subject", ""),
                "pages": len(doc),
                "file_size": pdf_path.stat().st_size,
            }
        }

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Get text with layout information
            text = page.get_text("dict")

            # Extract structured content
            page_content = {
                "page_number": page_num + 1,
                "blocks": [],
                "text": "",
            }

            if isinstance(text, dict) and "blocks" in text:
                for block in text["blocks"]:
                    if "lines" in block:
                        block_text = ""
                        for line in block["lines"]:
                            line_text = ""
                            for span in line["spans"]:
                                # Preserve font size info for headings
                                font_size = span.get("size", 10)
                                span_text = span.get("text", "").strip()

                                if span_text:
                                    # Add formatting hints for large text (potential headings)
                                    if font_size > 14:
                                        line_text += f"## {span_text} "
                                    elif font_size > 12:
                                        line_text += f"### {span_text} "
                                    else:
                                        line_text += span_text + " "

                            if line_text.strip():
                                block_text += line_text.strip() + "\n"

                        if block_text.strip():
                            page_content["blocks"].append(block_text.strip())
                            page_content["text"] += block_text.strip() + "\n\n"

            content["pages"].append(page_content)
            content["text"] += page_content["text"] + "\n\n"

        doc.close()

        # Clean the extracted text
        content["text"] = clean_text(content["text"], preserve_formatting=True)

        return content

    except Exception as e:
        print(f"  ⚠️ Error extracting PDF with layout {pdf_path.name}: {e}")
        return {"text": "", "pages": [], "metadata": {}}


def smart_chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """
    Split text into semantically meaningful chunks without fragmentation.
    Enhanced version with paragraph preservation.
    """
    if not text or len(text.strip()) < 50:
        return []
    
    # First, preserve paragraph structure
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    for paragraph in paragraphs:
        para_len = len(paragraph)
        
        # If paragraph itself is larger than chunk_size, split it
        if para_len > chunk_size:
            # Try to split paragraph by sentences
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            for sentence in sentences:
                sent_len = len(sentence)
                
                if current_length + sent_len > chunk_size and current_chunk:
                    chunk_text = '\n\n'.join(current_chunk)
                    if len(chunk_text) > 30:
                        chunks.append(chunk_text)
                    
                    # Keep last paragraph for overlap
                    if chunk_overlap > 0 and current_chunk:
                        overlap_paras = []
                        overlap_len = 0
                        for p in reversed(current_chunk):
                            if overlap_len + len(p) <= chunk_overlap:
                                overlap_paras.insert(0, p)
                                overlap_len += len(p)
                            else:
                                break
                        current_chunk = overlap_paras
                        current_length = overlap_len
                    else:
                        current_chunk = []
                        current_length = 0
                
                current_chunk.append(sentence)
                current_length += sent_len
        else:
            # Handle normal paragraph
            if current_length + para_len > chunk_size and current_chunk:
                chunk_text = '\n\n'.join(current_chunk)
                if len(chunk_text) > 30:
                    chunks.append(chunk_text)
                
                # Keep last paragraph for overlap
                if chunk_overlap > 0 and current_chunk:
                    overlap_paras = []
                    overlap_len = 0
                    for p in reversed(current_chunk):
                        if overlap_len + len(p) <= chunk_overlap:
                            overlap_paras.insert(0, p)
                            overlap_len += len(p)
                        else:
                            break
                    current_chunk = overlap_paras
                    current_length = overlap_len
                else:
                    current_chunk = []
                    current_length = 0
            
            current_chunk.append(paragraph)
            current_length += para_len
    
    # Add final chunk
    if current_chunk:
        chunk_text = '\n\n'.join(current_chunk)
        if len(chunk_text) > 30:
            chunks.append(chunk_text)
    
    # Filter out very small chunks
    chunks = [c for c in chunks if len(c) > 30]
    
    return chunks


def extract_key_phrases(text: str, max_phrases: int = 5) -> List[str]:
    """Extract key phrases from text for better question generation."""
    # Simple extraction based on capitalized phrases and important terms
    phrases = []
    
    # Look for capitalized phrases (potential proper nouns or important terms)
    capitalized = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
    phrases.extend(capitalized[:max_phrases])
    
    # Look for URA-specific terms
    ura_terms = ['tax', 'vat', 'tin', 'withholding', 'corporation', 'individual', 'business', 'compliance', 'payment', 'return', 'assessment']
    for term in ura_terms:
        if term in text.lower() and len(phrases) < max_phrases:
            phrases.append(term.upper())
    
    # Deduplicate and clean
    unique_phrases = []
    seen = set()
    for phrase in phrases:
        if phrase.lower() not in seen and len(phrase) > 2:
            unique_phrases.append(phrase)
            seen.add(phrase.lower())
    
    return unique_phrases[:max_phrases]


# =============================================================================
# Enhanced Data Loading
# =============================================================================

def load_csv_faqs(verbose: bool = True) -> pd.DataFrame:
    """Load all CSV/XLSX FAQ files with enhanced error handling."""
    data_files = sorted(list(DATASETS_DIR.glob("*.csv")) + list(DATASETS_DIR.glob("*.xlsx")))
    
    if verbose:
        print(f"Found {len(data_files)} data files in {DATASETS_DIR}")
    
    question_candidates = {'question', 'questions', 'q', 'query', 'queries', 'faq'}
    answer_candidates = {'answer', 'answers', 'a', 'response', 'resp', 'reply', 'solution'}
    
    frames = []
    for path in data_files:
        try:
            if path.suffix == '.csv':
                df = read_csv_with_fallback(path)
            else:
                df = pd.read_excel(path, engine='openpyxl')
            
            if df.empty:
                if verbose:
                    print(f"  ⚠️ {path.name} is empty or could not be read properly")
                continue
            
            # Clean column names
            df.columns = [str(col).strip() for col in df.columns]
            columns_lower = {c.lower(): c for c in df.columns}
            
            # Find question and answer columns
            q_col = next((columns_lower[c] for c in columns_lower if c in question_candidates), None)
            a_col = next((columns_lower[c] for c in columns_lower if c in answer_candidates), None)
            
            if not q_col or not a_col:
                # Try to guess columns
                if len(df.columns) >= 2:
                    q_col = df.columns[0]
                    a_col = df.columns[1]
                    if verbose:
                        print(f"  ⚠️ {path.name}: Using first two columns as Q/A")
                else:
                    if verbose:
                        print(f"  ⚠️ {path.name}: Could not identify Q/A columns")
                    continue
            
            df = df[[q_col, a_col]].copy()
            df.columns = ['question', 'answer']
            
            df['question'] = df['question'].apply(clean_text)
            df['answer'] = df['answer'].apply(clean_text)
            
            # Filter empty rows
            df = df[(df['question'].str.len() > 10) & (df['answer'].str.len() > 10)]
            
            if not df.empty:
                df['source'] = path.name
                df['category'] = path.stem.replace('ura_', '').replace('_faqs', '').replace('_', ' ').replace('-', ' ').lower()
                df['data_type'] = 'faq'
                frames.append(df)
                
                if verbose:
                    print(f"  ✓ {path.name}: {len(df)} Q/A pairs")
            
        except Exception as e:
            if verbose:
                print(f"  ✗ Error loading {path.name}: {e}")
    
    if frames:
        result = pd.concat(frames, ignore_index=True)
        if verbose:
            print(f"  Total: {len(result)} Q/A pairs from CSVs")
        return result
    
    return pd.DataFrame(columns=['question', 'answer', 'source', 'category', 'data_type'])

# ...existing code...

def load_pdf_content(verbose: bool = True, use_layout: bool = True) -> List[Dict[str, Any]]:
    """Load and chunk PDF content using smart splitting and layout preservation."""
    if not PYPDF_AVAILABLE:
        if verbose:
            print("  ✗ Skipping PDF loading (pymupdf4llm not installed)")
        return []
    
    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if verbose:
        print(f"Found {len(pdf_files)} PDF files in {PDF_DIR}")
    
    pdf_chunks = []
    for path in pdf_files:
        try:
            if verbose:
                print(f"  Processing {path.name}...")
            
            text = ""  # Initialize text to avoid "possibly unbound" error
            extracted_content = None
            if use_layout:
                # Try layout-based extraction first
                extracted_content = extract_pdf_with_layout(path)
                text = extracted_content.get("text", "")
                
                if len(text) > 100:
                    if verbose:
                        print(f"    ✓ Layout extraction: {len(text)} chars")
                else:
                    # Fallback to pymupdf4llm
                    if verbose:
                        print(f"    ⚠️ Layout extraction insufficient, using pymupdf4llm")
                    use_layout = False
            
            if not use_layout or not extracted_content or len(text) < 100:
                # Use pymupdf4llm
                md_result = pymupdf4llm.to_markdown(
                    str(path),
                    pages=None,
                    show_progress=False
                )
                
                if isinstance(md_result, list):
                    text = clean_text("\n\n".join(
                        chunk.get('text', str(chunk)) if isinstance(chunk, dict) else str(chunk)
                        for chunk in md_result
                    ), preserve_formatting=True)
                else:
                    text = clean_text(str(md_result), preserve_formatting=True)
            
            if len(text) > 100:
                # Extract key phrases for better categorization
                key_phrases = extract_key_phrases(text, max_phrases=3)
                
                # Use smart chunking to avoid fragmentation
                chunks = smart_chunk_text(text, chunk_size=800, chunk_overlap=100)
                
                for i, chunk in enumerate(chunks):
                    if len(chunk) > 100:  # Minimum chunk size
                        pdf_chunks.append({
                            'text': chunk,
                            'source': path.name,
                            'chunk_id': i,
                            'total_chunks': len(chunks),
                            'category': path.stem.replace('-', ' ').replace('_', ' ').lower(),
                            'key_phrases': key_phrases,
                            'data_type': 'pdf',
                            'extraction_method': 'layout' if use_layout and extracted_content else 'pymupdf4llm'
                        })
                
                if verbose:
                    print(f"    ✓ Created {len(chunks)} chunks from {path.name}")
            
        except Exception as e:
            if verbose:
                print(f"    ✗ Error loading {path.name}: {e}")
    
    if verbose:
        print(f"  Total: Created {len(pdf_chunks)} chunks from PDFs")
    
    return pdf_chunks


def load_luganda_translations(verbose: bool = True) -> pd.DataFrame:
    """Load English-Luganda parallel data with enhanced handling."""
    ttt_files = sorted(list(TTT_DIR.glob("*.csv")) + list(TTT_DIR.glob("*.xlsx")) + 
                      list(TTT_DIR.glob("*.xls")) + list(TTT_DIR.glob("*.txt")))
    
    if verbose:
        print(f"Found {len(ttt_files)} translation files in {TTT_DIR}")
    
    translations = []
    for path in ttt_files:
        try:
            if path.suffix == '.csv':
                df = read_csv_with_fallback(path)
            elif path.suffix in ['.xlsx', '.xls']:
                df = pd.read_excel(path, engine='openpyxl' if path.suffix == '.xlsx' else 'xlrd')
            elif path.suffix == '.txt':
                # Try to parse as TSV or space-separated
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                
                data = []
                for line in lines:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        data.append({'english': parts[0], 'luganda': parts[1]})
                df = pd.DataFrame(data)
            else:
                continue
            
            if df.empty:
                continue
            
            # Clean column names and find language columns
            df.columns = [str(col).strip().lower() for col in df.columns]
            
            en_col = next((c for c in df.columns if 'english' in c or 'en' == c), None)
            lg_col = next((c for c in df.columns if 'luganda' in c or 'lg' == c or 'lug' in c), None)
            
            if not en_col or not lg_col:
                # Try to guess columns
                if len(df.columns) >= 2:
                    en_col = df.columns[0]
                    lg_col = df.columns[1]
                else:
                    continue
            
            for _, row in df.iterrows():
                en_text = clean_text(str(row[en_col]))
                lg_text = clean_text(str(row[lg_col]))
                
                if en_text and lg_text and len(en_text) > 5 and len(lg_text) > 5:
                    translations.append({
                        'english': en_text,
                        'luganda': lg_text,
                        'source': path.name,
                        'data_type': 'translation'
                    })
            
            if verbose:
                print(f"  ✓ {path.name}: {len([t for t in translations if t['source'] == path.name])} pairs")
                
        except Exception as e:
            if verbose:
                print(f"  ✗ Error loading {path.name}: {e}")
    
    result = pd.DataFrame(translations) if translations else pd.DataFrame()
    
    if verbose:
        print(f"  Total: {len(result)} translation pairs")
    
    return result


# =============================================================================
# Enhanced Training Data Generation
# =============================================================================

def create_instruction_format(question: str, answer: str, category: str = "", context: str = "") -> dict:
    """Create instruction-tuning format for Gemma with optional context."""
    template = random.choice(INSTRUCTION_TEMPLATES)
    instruction = template.format(question=question)
    
    # Add category context sometimes
    if category and random.random() > 0.3:
        instruction = f"[{category.upper()}] {instruction}"
    
    # Add PDF context if available
    if context and random.random() > 0.7:
        # Use PDF-specific prompt
        pdf_template = random.choice(PDF_PROMPT_TEMPLATES)
        instruction = pdf_template.format(question=question, context=context[:500])
    
    output = {
        "instruction": instruction,
        "input": "",
        "output": answer,
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
    
    if context:
        output["context"] = context[:500]
    
    return output


def create_chat_format(question: str, answer: str, category: str = "", context: str = "") -> dict:
    """Create chat format for Gemma (multi-turn ready)."""
    system = random.choice(SYSTEM_PROMPTS)
    
    # Enhance system prompt with category sometimes
    if category and random.random() > 0.5:
        system = f"{system} Specializing in {category}."
    
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    
    output = {
        "messages": messages,
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
    
    if context:
        output["context"] = context[:500]
    
    return output


def create_gemma_format(question: str, answer: str, category: str = "") -> dict:
    """Create Gemma-specific instruction format."""
    # Gemma uses <start_of_turn> and <end_of_turn> tokens
    formatted_text = f"<start_of_turn>user\n{question}<end_of_turn>\n<start_of_turn>model\n{answer}<end_of_turn>"
    
    return {
        "text": formatted_text,
        "question": question,
        "answer": answer,
        "category": category,
        "format": "gemma",
        "timestamp": datetime.now().isoformat(),
    }


def generate_qa_from_pdf_chunk(chunk: dict, enhanced: bool = True) -> List[Dict[str, Any]]:
    """Generate QA pairs from a PDF chunk with enhanced methods."""
    text = chunk['text']
    category = chunk['category']
    key_phrases = chunk.get('key_phrases', [])
    
    qa_pairs = []
    
    # Method 1: Topic-based questions
    topic = extract_topic_from_text(text)
    if len(topic) > 3:
        for template in random.sample(PDF_QUESTION_TEMPLATES, min(3, len(PDF_QUESTION_TEMPLATES))):
            question = template.format(topic=topic)
            answer = f"Based on URA documentation: {text[:300]}..."
            
            qa_pairs.append({
                "question": question,
                "answer": answer,
                "source": chunk['source'],
                "category": category,
                "generated": True,
                "method": "topic_extraction",
            })
    
    # Method 2: Key phrase-based questions (enhanced)
    if enhanced and key_phrases:
        for phrase in key_phrases[:2]:
            question = f"What is {phrase} according to URA?"
            answer = f"According to URA guidelines: {text[:400]}..."
            
            qa_pairs.append({
                "question": question,
                "answer": answer,
                "source": chunk['source'],
                "category": category,
                "generated": True,
                "method": "key_phrase",
            })
    
    # Method 3: Summary-based Q/A
    if len(text) > 200:
        # Create summary question
        summary = text[:150] + "..."
        question = f"Summarize the key points from this URA document on {category}"
        answer = f"The document discusses: {summary}"
        
        qa_pairs.append({
            "question": question,
            "answer": answer,
            "source": chunk['source'],
            "category": category,
            "generated": True,
            "method": "summary",
        })
    
    # Method 4: Direct extraction (use chunk as context for specific questions)
    if enhanced and len(text) > 100:
        # Extract potential questions from the text itself
        sentences = re.split(r'[.!?]', text)
        meaningful_sentences = [s.strip() for s in sentences if len(s.split()) > 5]
        
        for sentence in meaningful_sentences[:2]:
            # Convert statement to question
            words = sentence.split()
            if len(words) > 5:
                question = f"What does URA say about {' '.join(words[:4]).lower()}?"
                answer = sentence
                
                qa_pairs.append({
                    "question": question,
                    "answer": answer,
                    "source": chunk['source'],
                    "category": category,
                    "generated": True,
                    "method": "direct_extraction",
                })
    
    return qa_pairs


def create_luganda_training_pair(translation: dict) -> List[Dict[str, Any]]:
    """Create training pairs with Luganda translation."""
    en_text = translation['english']
    lg_text = translation['luganda']
    source = translation['source']
    
    pairs = []
    
    # Format 1: English to Luganda translation
    pairs.append({
        "instruction": f"Translate this English text to Luganda: {en_text}",
        "input": "",
        "output": lg_text,
        "category": "translation_en_to_lg",
        "source": source,
        "data_type": "translation",
    })
    
    # Format 2: Luganda to English translation
    pairs.append({
        "instruction": f"Translate this Luganda text to English: {lg_text}",
        "input": "",
        "output": en_text,
        "category": "translation_lg_to_en",
        "source": source,
        "data_type": "translation",
    })
    
    # Format 3: Bilingual response
    pairs.append({
        "instruction": f"Provide information about this in both English and Luganda: {en_text[:50]}",
        "input": "",
        "output": f"English: {en_text}\n\nLuganda: {lg_text}",
        "category": "bilingual",
        "source": source,
        "data_type": "translation",
    })
    
    # Format 4: Gemma format for English-Luganda
    pairs.append({
        "text": f"<start_of_turn>user\nTranslate to Luganda: {en_text}<end_of_turn>\n<start_of_turn>model\n{lg_text}<end_of_turn>",
        "question": f"Translate to Luganda: {en_text}",
        "answer": lg_text,
        "category": "translation",
        "source": source,
        "format": "gemma",
    })
    
    return pairs


# =============================================================================
# Enhanced Data Augmentation
# =============================================================================

def augment_question(question: str, category: str = "") -> List[str]:
    """Generate enhanced variations of a question."""
    variations = [question]
    
    # Add politeness variations
    polite_prefixes = ["Please ", "Could you ", "Can you help me understand ", "I need help with "]
    for prefix in polite_prefixes:
        if not question.lower().startswith(prefix.lower().split()[0]):
            new_question = f"{prefix}{question[0].lower()}{question[1:]}"
            if new_question not in variations:
                variations.append(new_question)
    
    # Add URA context if not present
    if "ura" not in question.lower():
        variations.append(f"{question} (URA question)")
    
    # Add category context
    if category:
        variations.append(f"[{category.upper()}] {question}")
    
    # Rephrase questions
    rephrases = [
        question.replace("What is", "Explain").replace("what is", "explain"),
        question.replace("How do", "What is the procedure for").replace("how do", "what is the procedure for"),
        f"Tell me about {question.lower().replace('what is', '').replace('how do', '').strip()}",
    ]
    
    for rephrase in rephrases:
        if rephrase not in variations and len(rephrase) > 10:
            variations.append(rephrase)
    
    return list(set(variations[:5]))  # Limit to 5 unique variations


def augment_dataset(
    qa_pairs: List[Dict[str, Any]],
    pdf_chunks: List[Dict[str, Any]],
    translations: pd.DataFrame,
    augment_factor: int = 2,
    use_enhanced_pdf: bool = True,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """Augment and combine all data sources with enhanced methods."""
    augmented = []
    
    if verbose:
        print(f"\n🔄 Augmenting {len(qa_pairs)} QA pairs...")
    
    # Process CSV QA pairs
    for pair in qa_pairs:
        category = pair.get('category', '')
        question = pair['question']
        answer = pair['answer']
        
        # Original pair in multiple formats
        augmented.append(create_instruction_format(question, answer, category))
        augmented.append(create_chat_format(question, answer, category))
        augmented.append(create_gemma_format(question, answer, category))
        
        # Question variations
        if augment_factor > 1:
            variants = augment_question(question, category)
            for variant in variants[1:min(len(variants), augment_factor)]:
                augmented.append(create_instruction_format(variant, answer, category))
                augmented.append(create_gemma_format(variant, answer, category))
    
    # Process PDF chunks
    if verbose:
        print(f"Processing {len(pdf_chunks)} PDF chunks...")
    
    pdf_qa_count = 0
    for chunk in pdf_chunks:
        qa_pairs_from_chunk = generate_qa_from_pdf_chunk(chunk, enhanced=use_enhanced_pdf)
        
        for qa in qa_pairs_from_chunk:
            # Add context from PDF chunk
            context = chunk['text'][:500]  # Use first 500 chars as context
            
            # Create multiple formats
            augmented.append(create_instruction_format(
                qa['question'], qa['answer'], qa['category'], context
            ))
            augmented.append(create_chat_format(
                qa['question'], qa['answer'], qa['category'], context
            ))
            augmented.append(create_gemma_format(
                qa['question'], qa['answer'], qa['category']
            ))
            
            pdf_qa_count += 1
    
    if verbose:
        print(f"  Generated {pdf_qa_count} Q/A pairs from PDFs")
    
    # Process translations
    if not translations.empty:
        if verbose:
            print(f"Processing {len(translations)} translation pairs...")
        
        translation_count = 0
        for _, row in translations.iterrows():
            pairs = create_luganda_training_pair(row.to_dict())
            augmented.extend(pairs)
            translation_count += len(pairs)
        
        if verbose:
            print(f"  Created {translation_count} translation training examples")
    
    # Add metadata to all examples
    for i, item in enumerate(augmented):
        if 'id' not in item:
            item['id'] = f"sample_{i:06d}"
        
        # Ensure timestamp
        if 'timestamp' not in item:
            item['timestamp'] = datetime.now().isoformat()
    
    # Shuffle
    random.shuffle(augmented)
    
    # Statistics
    if verbose:
        stats = {
            'instruction': sum(1 for d in augmented if 'instruction' in d),
            'chat': sum(1 for d in augmented if 'messages' in d),
            'gemma': sum(1 for d in augmented if 'text' in d and 'messages' not in d),
            'translation': sum(1 for d in augmented if d.get('category', '').startswith('translation')),
            'pdf_based': sum(1 for d in augmented if d.get('generated', False)),
        }
        
        print(f"\n📊 Augmentation Statistics:")
        print(f"  Total samples: {len(augmented)}")
        print(f"  Instruction format: {stats['instruction']}")
        print(f"  Chat format: {stats['chat']}")
        print(f"  Gemma format: {stats['gemma']}")
        print(f"  Translation examples: {stats['translation']}")
        print(f"  PDF-generated Q/A: {stats['pdf_based']}")
    
    return augmented


# =============================================================================
# Enhanced Export Functions
# =============================================================================

def export_jsonl(data: List[Dict[str, Any]], output_path: Path, verbose: bool = True):
    """Export to JSONL format (one JSON object per line)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    if verbose:
        print(f"✓ Exported {len(data)} samples to {output_path}")


def export_hf_dataset(data: List[Dict[str, Any]], output_path: Path, verbose: bool = True):
    """Export to HuggingFace datasets format."""
    try:
        from datasets import Dataset, DatasetDict
        
        # Separate by format
        instruction_data = [d for d in data if 'instruction' in d]
        chat_data = [d for d in data if 'messages' in d]
        gemma_data = [d for d in data if 'text' in d and 'messages' not in d]
        
        datasets_dict = {}
        
        # Save instruction format
        if instruction_data:
            ds = Dataset.from_list(instruction_data)
            datasets_dict['instruction'] = ds
        
        # Save chat format
        if chat_data:
            ds = Dataset.from_list(chat_data)
            datasets_dict['chat'] = ds
        
        # Save Gemma format
        if gemma_data:
            ds = Dataset.from_list(gemma_data)
            datasets_dict['gemma'] = ds
        
        # Create dataset dictionary
        if datasets_dict:
            dataset_dict = DatasetDict(datasets_dict)
            dataset_dict.save_to_disk(str(output_path))
            
            if verbose:
                print(f"✓ Saved HuggingFace dataset to {output_path}")
                for name, ds in dataset_dict.items():
                    print(f"  {name}: {len(ds)} samples")
        
    except ImportError:
        if verbose:
            print("⚠️ Warning: datasets library not installed. Skipping HF export.")
    except Exception as e:
        if verbose:
            print(f"⚠️ Error exporting HF dataset: {e}")


def export_train_val_split(data: List[Dict[str, Any]], output_dir: Path, val_ratio: float = 0.1, verbose: bool = True):
    """Export with train/validation split with balanced categories."""
    random.shuffle(data)
    
    # Group by category for stratified split (if categories available)
    categories = {}
    for item in data:
        cat = item.get('category', 'unknown')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)
    
    train_data = []
    val_data = []
    
    for cat, items in categories.items():
        split_idx = int(len(items) * (1 - val_ratio))
        train_data.extend(items[:split_idx])
        val_data.extend(items[split_idx:])
    
    # Shuffle again
    random.shuffle(train_data)
    random.shuffle(val_data)
    
    # Export
    train_path = output_dir / 'train.jsonl'
    val_path = output_dir / 'val.jsonl'
    
    export_jsonl(train_data, train_path, verbose=False)
    export_jsonl(val_data, val_path, verbose=False)
    
    if verbose:
        print(f"\n📊 Train/Validation Split:")
        print(f"  Train: {len(train_data)} samples")
        print(f"  Validation: {len(val_data)} samples")
        print(f"  Split ratio: {100*(1-val_ratio):.0f}/{100*val_ratio:.0f}")
        print(f"  Files: {train_path}, {val_path}")


def export_statistics(data: List[Dict[str, Any]], output_path: Path):
    """Export dataset statistics to a JSON file."""
    stats = {
        "total_samples": len(data),
        "by_format": {
            "instruction": sum(1 for d in data if 'instruction' in d),
            "chat": sum(1 for d in data if 'messages' in d),
            "gemma": sum(1 for d in data if 'text' in d and 'messages' not in d),
        },
        "by_category": {},
        "by_source": {},
        "by_data_type": {},
        "timestamp": datetime.now().isoformat(),
    }
    
    # Collect categories, sources, and data types
    for item in data:
        # Category
        cat = item.get('category', 'unknown')
        stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
        
        # Source
        src = item.get('source', 'unknown')
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
        
        # Data type
        dtype = item.get('data_type', 'unknown')
        stats["by_data_type"][dtype] = stats["by_data_type"].get(dtype, 0) + 1
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Statistics exported to {output_path}")
    
    return stats


# =============================================================================
# Main
# =============================================================================

def main():
    global DATASETS_DIR, PDF_DIR, TTT_DIR, OUTPUT_DIR
    
    parser = argparse.ArgumentParser(description="Data augmentation for URA Tax Assistant fine-tuning")
    
    # Directory arguments
    parser.add_argument("--csv-dir", type=str, default=None,
                        help="Directory containing CSV FAQ files (default: Data/dataset)")
    parser.add_argument("--pdf-dir", type=str, default=None,
                        help="Directory containing PDF documents (default: Data/pdfs)")
    parser.add_argument("--luganda-dir", type=str, default=None,
                        help="Directory containing Luganda/TTT data (default: Data/TTT)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: Data/artifacts)")
    
    # Output arguments
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path for training data")
    parser.add_argument("--gemma-output", type=str, default=None,
                        help="Output file path for Gemma format")
    parser.add_argument("--instruction-output", type=str, default=None,
                        help="Output file path for instruction format")
    
    # Processing arguments
    parser.add_argument("--augment-factor", type=int, default=2,
                        help="Augmentation factor for question variations")
    parser.add_argument("--split", action="store_true",
                        help="Create train/val split")
    parser.add_argument("--split-ratio", type=float, default=0.1,
                        help="Validation split ratio (default: 0.1)")
    parser.add_argument("--hf-format", action="store_true",
                        help="Also export in HuggingFace datasets format")
    parser.add_argument("--no-layout", action="store_true",
                        help="Disable PDF layout extraction (use pymupdf4llm only)")
    parser.add_argument("--simple-pdf", action="store_true",
                        help="Use simple PDF extraction without enhanced Q/A generation")
    parser.add_argument("--stats", action="store_true",
                        help="Export dataset statistics")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")
    
    args = parser.parse_args()
    
    # Resolve directories
    def resolve_path(cli_arg: Optional[str], default_path: Path) -> Path:
        if cli_arg:
            path = Path(cli_arg)
            if path.is_absolute():
                return path
            if path.exists():
                return path.resolve()
            return PROJECT_ROOT / path
        return default_path
    
    DATASETS_DIR = resolve_path(args.csv_dir, DATA_ROOT / "dataset")
    PDF_DIR = resolve_path(args.pdf_dir, DATA_ROOT / "pdfs")
    TTT_DIR = resolve_path(args.luganda_dir, DATA_ROOT / "TTT")
    OUTPUT_DIR = resolve_path(args.output_dir, DATA_ROOT / "artifacts")
    
    print("="*70)
    print("URA TAX ASSISTANT - DATA AUGMENTATION PIPELINE")
    print("="*70)
    print(f"Using pymupdf4llm: {'✓' if PYPDF_AVAILABLE else '✗ (install with: pip install pymupdf4llm)'}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print(f"\n📁 Data Directories:")
    print(f"   CSV FAQs:      {DATASETS_DIR} {'✓' if DATASETS_DIR.exists() else '✗'}")
    print(f"   PDFs:          {PDF_DIR} {'✓' if PDF_DIR.exists() else '✗'}")
    print(f"   Luganda:       {TTT_DIR} {'✓' if TTT_DIR.exists() else '✗'}")
    print(f"   Output:        {OUTPUT_DIR}")
    
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load data
    print("\n📂 Loading data sources...")
    qa_df = load_csv_faqs(verbose=args.verbose)
    pdf_chunks = load_pdf_content(verbose=args.verbose, use_layout=not args.no_layout)
    translations = load_luganda_translations(verbose=args.verbose)
    
    # Check if we have enough data
    if qa_df.empty and not pdf_chunks:
        print("\n❌ No training data found!")
        print("   Please check your data directories or run with different paths.")
        sys.exit(1)
    
    # Convert QA dataframe to list
    qa_pairs = qa_df.to_dict('records') if not qa_df.empty else []
    
    # Augment
    print("\n🔄 Augmenting dataset...")
    augmented_data = augment_dataset(
        qa_pairs, 
        pdf_chunks, 
        translations, 
        augment_factor=args.augment_factor,
        use_enhanced_pdf=not args.simple_pdf,
        verbose=args.verbose
    )
    
    if not augmented_data:
        print("\n❌ No augmented data generated!")
        sys.exit(1)
    
    # Export
    print("\n💾 Exporting data...")
    
    # Determine output paths
    if args.output:
        main_output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        main_output_path = OUTPUT_DIR / f"training_data_{timestamp}.jsonl"
    
    main_output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Always export the full dataset
    export_jsonl(augmented_data, main_output_path, verbose=args.verbose)
    
    # Export splits if requested
    if args.split:
        split_dir = main_output_path.parent / "splits"
        split_dir.mkdir(exist_ok=True)
        export_train_val_split(augmented_data, split_dir, val_ratio=args.split_ratio, verbose=args.verbose)
    
    # Export Gemma format if requested
    if args.gemma_output:
        gemma_path = Path(args.gemma_output)
        gemma_data = [d for d in augmented_data if 'text' in d]
        export_jsonl(gemma_data, gemma_path, verbose=args.verbose)
    
    # Export instruction format if requested
    if args.instruction_output:
        instruction_path = Path(args.instruction_output)
        instruction_data = [d for d in augmented_data if 'instruction' in d]
        export_jsonl(instruction_data, instruction_path, verbose=args.verbose)
    
    # Export HF format if requested
    if args.hf_format:
        hf_dir = OUTPUT_DIR / "hf_dataset"
        export_hf_dataset(augmented_data, hf_dir, verbose=args.verbose)
    
    # Export statistics if requested
    if args.stats:
        stats_path = main_output_path.parent / "dataset_statistics.json"
        stats = export_statistics(augmented_data, stats_path)
        
        if args.verbose:
            print(f"\n📈 Dataset Statistics Summary:")
            print(f"   Total Samples: {stats['total_samples']}")
            print(f"   Categories: {len(stats['by_category'])}")
            print(f"   Sources: {len(stats['by_source'])}")
            print(f"   Top 5 Categories:")
            sorted_cats = sorted(stats['by_category'].items(), key=lambda x: x[1], reverse=True)[:5]
            for cat, count in sorted_cats:
                print(f"     - {cat}: {count}")
    
    print("\n" + "="*70)
    print("✅ DATA AUGMENTATION COMPLETE")
    print("="*70)
    print(f"\n🎉 Generated {len(augmented_data)} training examples")
    print(f"📁 Main output: {main_output_path}")
    
    # Show next steps
    print(f"\n🚀 Next steps:")
    print(f"   1. Fine-tune model: python ml/scripts/fine_tune_gemma.py --data {main_output_path}")
    print(f"   2. Test data quality: python ml/scripts/validate_data.py --data {main_output_path}")
    print(f"   3. Generate teacher Q/A: python ml/scripts/teacher_qa_generation.py")
    
    if not PYPDF_AVAILABLE:
        print(f"\n⚠️  Recommendation:")
        print(f"   Install pymupdf4llm for better PDF processing:")
        print(f"   pip install pymupdf4llm")


if __name__ == "__main__":
    main()