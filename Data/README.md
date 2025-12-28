# =============================================================================
# Data Directory
# URA Chatbot Training Datasets
# =============================================================================

This directory contains all training data for the URA chatbot classifier.

## Directory Structure

```
Data/
├── README.md           # This file
├── dataset/            # CSV files for training
│   ├── ura_vat_faqs.csv
│   ├── ura_tin_faqs.csv
│   └── ... (41 CSV files)
├── pdfs/               # PDF reference documents
│   ├── TAXATION-HANDBOOK-FY-2025-26-1.pdf
│   └── ... (45 PDF files)
├── TTT/                # Translation corpus data
│   ├── Luganda.csv
│   ├── Luganda_Agriculture-specific_dataset-1.csv
│   └── ... (translation datasets)
└── lgaudio/            # Luganda audio data
```

## Dataset Folder (`dataset/`)

Contains CSV files used for training the URA chatbot classifier.

### Data Format

Each CSV file should contain at minimum:
- `question` (or `q`): The question text
- `answer` (or `a`, `response`): The answer text

### File Naming Convention

Files follow the pattern: `ura_{category}_faqs.csv`

Examples:
- `ura_vat_faqs.csv` - VAT related FAQs
- `ura_tin_faqs.csv` - TIN registration FAQs
- `ura_customs_faqs.csv` - Customs procedures FAQs

## PDFs Folder (`pdfs/`)

Contains reference PDF documents including:
- Taxation handbooks by fiscal year
- Sector-specific tax guides
- URA policy documents

## Data Sources

Data is sourced from:
- URA official website FAQs
- URA publications and handbooks
- Tax policy documents

## Adding New Data

1. For CSVs:
   - Place in `dataset/` subfolder
   - Ensure CSV has `question` and `answer` columns
   - Follow naming convention: `ura_{topic}_faqs.csv`
   
2. For PDFs:
   - Place in `pdfs/` subfolder
   - Use descriptive filename

- Minimum 3 words per answer
- No duplicate Q&A pairs
- UTF-8 encoding

## Statistics

Run `python ml/pipelines/validate_data.py` to generate data statistics.
