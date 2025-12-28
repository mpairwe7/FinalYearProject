---
title: URA Chatbot
emoji: 🇺🇬
colorFrom: cyan
colorTo: blue
sdk: gradio
sdk_version: "4.44.0"
app_file: app.py
pinned: true
license: mit
short_description: Uganda Revenue Authority Tax Assistant - AI-powered chatbot
tags:
  - chatbot
  - tax
  - uganda
  - ura
  - nlp
  - classification
---

# 🇺🇬 URA Chatbot - Uganda Revenue Authority Assistant

An AI-powered chatbot that helps users navigate Uganda Revenue Authority (URA) services, tax policies, and procedures.

## Features

- 💬 **Natural Language Chat** - Ask questions in plain English
- 🏷️ **Smart Classification** - AI classifies queries into relevant tax categories
- 📚 **Knowledge Base** - Trained on official URA FAQs and documents
- 🎨 **Modern UI** - Clean, responsive design with dark theme
- ⚡ **Fast Responses** - Instant classification and answer retrieval

## Categories Covered

- VAT (Value Added Tax)
- TIN (Taxpayer Identification Number)
- Customs & Import/Export
- Income Tax
- Withholding Tax
- EFRIS (Electronic Fiscal Receipting)
- Stamp Duty
- Rental Income Tax
- Employment Income
- Business Registration

## How It Works

1. **Question Analysis** - Your question is encoded using Sentence Transformers
2. **Classification** - An SGD classifier identifies the relevant tax category
3. **Answer Retrieval** - We search our knowledge base for the best matching answer
4. **Response Generation** - You get accurate, sourced information

## Technical Stack

- **Frontend**: Gradio with custom CSS
- **ML Model**: SGDClassifier with sentence-transformers embeddings
- **Embedding**: all-MiniLM-L6-v2
- **Backend**: Python with scikit-learn

## Links

- [GitHub Repository](https://github.com/mpairweLandwind/FinalYearProject)
- [URA Official Website](https://www.ura.go.ug)

## License

MIT License - See repository for details
