---
title: URA Tax Assistant
emoji: 🏛️
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: "5.43.1"
app_file: app.py
pinned: true
license: mit
short_description: Uganda Revenue Authority Tax Assistant — AI-powered chatbot
tags:
  - chatbot
  - tax
  - uganda
  - ura
  - nlp
  - classification
---

# 🇺🇬 URA Tax Assistant — Uganda Revenue Authority

An AI-powered chatbot that helps users navigate Uganda Revenue Authority (URA)
services, tax policies, and procedures. The interface is a faithful port of the
project's Next.js frontend design system.

## Features

- 💬 **Natural Language Chat** — ask questions in plain English (Luganda toggle included)
- 🏷️ **Smart Classification** — AI classifies queries into the relevant tax category
- 📚 **Knowledge Base** — grounded in official URA FAQs and documents
- 🎨 **Official URA Design** — Navy / Gold / Teal palette, animated gradient mesh,
  floating glass panels, gold-tinted user bubbles, bubble-less assistant replies
- ⚡ **Fast Responses** — instant classification and answer retrieval

## Categories Covered

VAT · TIN · Customs & Import/Export · Income Tax · Withholding Tax · EFRIS ·
Stamp Duty · Rental Income Tax · Employment Income · Business Registration

## How It Works

1. **Question Analysis** — your question is encoded with Sentence Transformers
2. **Classification** — an SGD classifier identifies the relevant tax category
3. **Answer Retrieval** — the knowledge base is searched for the best match
4. **Response Generation** — you get accurate, sourced information

When the full FastAPI backend is bundled, the app automatically delegates to the
unified `ChatModel` (hybrid retrieval, guardrails, LLM synthesis); otherwise it
runs the lightweight classifier + retrieval path shown above.

## Hardware

This Space runs comfortably on **free CPU basic** — the classifier and the
`all-MiniLM-L6-v2` embedder are small.

To use a **free GPU**, set the Space hardware to **ZeroGPU** (Settings →
Hardware). `app.py` already imports `spaces` and decorates inference with
`@spaces.GPU`, so a GPU slice is attached per request automatically; on CPU the
decorator is a no-op, so no code change is needed either way. Note: hosting a
ZeroGPU Space requires a Hugging Face **PRO** account, but GPU *usage* is free.

## Technical Stack

- **UI**: Gradio 5 with a custom CSS port of the Next.js design system
- **ML Model**: SGDClassifier over sentence-transformers embeddings
- **Embedding**: `all-MiniLM-L6-v2`
- **Backend**: Python (scikit-learn); optional unified `ChatModel` RAG pipeline

## Links

- [GitHub Repository](https://github.com/mpairweLandwind/FinalYearProject)
- [URA Official Website](https://www.ura.go.ug)

## License

MIT License — see repository for details.
