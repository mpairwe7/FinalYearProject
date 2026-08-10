# Gradio Application Documentation

## Overview

The URA Chatbot includes a Gradio-based web interface for interactive tax assistance. This application is designed for deployment on Hugging Face Spaces.

## Application Files

| File | Purpose | Deployment |
|------|---------|------------|
| `App/app.py` | Full-featured UI (1176 lines) | HF Spaces |
| `App/classifier.py` | Simple classifier demo | Local testing |

## Features

### Main Application (`app.py`)

- **Modern UI**: Glassmorphism design with dark theme
- **Chat Interface**: Conversational AI assistant
- **Classification**: Real-time tag prediction
- **Knowledge Base**: FAQ search and display
- **Multi-language**: English and Luganda support
- **Responsive**: Mobile-friendly design

### Design Elements

```
┌─────────────────────────────────────────────────────────────┐
│                    URA TAX ASSISTANT                        │
│           Your AI-Powered Tax Guide for Uganda              │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐   │
│  │  💬 Chat                    📚 Knowledge Base       │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │                                                      │   │
│  │  [Chat messages appear here]                        │   │
│  │                                                      │   │
│  │  User: How do I register for TIN?                   │   │
│  │  AI: To register for a TIN...                       │   │
│  │                                                      │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Type your message...                    [Send]     │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Running Locally

### Quick Start
```bash
# Full application
python App/app.py

# Simple classifier
python App/classifier.py
```

### With Environment
```bash
# Set model path (optional)
export MODEL_PATH=./Model

# Run
python App/app.py
```

### Access
- Local: `http://localhost:7860`
- Network: `http://0.0.0.0:7860`

## Deployment to Hugging Face Spaces

### 1. Create Space

```bash
# Login to Hugging Face
huggingface-cli login

# Create space (via web UI or CLI)
# Go to: huggingface.co/new-space
# Select: Gradio SDK
```

### 2. Configure Space

Create `README.md` (already exists as `App/README_HF.md`):
```yaml
---
title: URA Tax Assistant
emoji: 🏛️
colorFrom: blue
colorTo: cyan
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
license: mit
---
```

### 3. Upload Files

Required files for HF Space:
```
App/
├── app.py              # Main application
├── requirements.txt    # Dependencies
└── README.md          # Space metadata (copy from README_HF.md)
```

### 4. Push to Space

```bash
cd App
git init
git remote add space https://huggingface.co/spaces/mpairweLandwind/ura-chatbot
git add .
git commit -m "Initial upload"
git push space main
```

Or use the `push_to_hub.py` script:
```bash
python ml/pipelines/push_to_hub.py \
  --model-path Model \
  --repo-id mpairweLandwind/ura-chatbot \
  --include-app
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_PATH` | Path to model files | `./Model` |
| `SHARE` | Enable public sharing | `false` |
| `SERVER_PORT` | Server port | `7860` |
| `THEME` | UI theme (dark/light) | `dark` |

### Customization

#### Theme Colors
In `app.py`, modify the CSS:
```python
css = """
:root {
    --primary-color: #00d4ff;    /* Cyan accent */
    --bg-color: #0a0a0f;         /* Dark background */
    --card-bg: rgba(255,255,255,0.05);
}
"""
```

#### Model Loading
```python
# Load from local path
classifier = joblib.load("Model/tag_classifier.joblib")
encoder = joblib.load("Model/label_encoder.joblib")

# Or load from Hugging Face
from huggingface_hub import hf_hub_download
classifier_path = hf_hub_download(
    repo_id="mpairweLandwind/ura-chatbot",
    filename="tag_classifier.joblib"
)
```

## API Endpoints (Gradio)

Gradio exposes automatic API endpoints:

### Chat
```bash
curl -X POST "http://localhost:7860/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"data": ["How do I register for TIN?"]}'
```

### Classify
```bash
curl -X POST "http://localhost:7860/api/classify" \
  -H "Content-Type: application/json" \
  -d '{"data": ["What is VAT?"]}'
```

## Dependencies

```txt
# App/requirements.txt
gradio==4.44.0
joblib>=1.3.0
scikit-learn>=1.3.0
sentence-transformers>=2.2.0
pandas>=2.0.0
numpy>=1.24.0
torch>=2.0.0
```

## File Structure

```python
# app.py structure
"""
1. Imports and Configuration
2. CSS Styling (glassmorphism, dark theme)
3. Model Loading
4. Helper Functions
   - load_knowledge_base()
   - classify_text()
   - generate_response()
5. Gradio Interface
   - Chat Tab
   - Knowledge Base Tab
   - About Tab
6. Launch Configuration
"""
```

## Troubleshooting

### Model Not Loading
```bash
# Check model files exist
ls -la Model/

# Expected files:
# tag_classifier.joblib
# label_encoder.joblib
```

### Port Already in Use
```bash
# Kill existing process
pkill -f "python App/app.py"

# Or use different port
python App/app.py --server-port 7861
```

### CUDA Out of Memory
```python
# Force CPU usage in app.py
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
```

### Slow First Load
The first request loads the embedding model. Subsequent requests are faster.

## Performance Tips

1. **Preload Models**: Load models at startup, not per-request
2. **Batch Processing**: Use batch inference for multiple queries
3. **Caching**: Enable Gradio caching for repeated queries
4. **GPU**: Use CUDA if available for embeddings

## Screenshots

### Chat Interface
The chat interface provides conversational interaction with the tax assistant.

### Knowledge Base
Browse FAQs organized by tax category.

### Classification Results
View confidence scores and top predictions for any query.

## Related Documentation

- [API Reference](API_REFERENCE.md) - REST API documentation
- [MLOps Workflows](mlops-workflows.md) - CI/CD deployment
- [Project Setup](PROJECT_SETUP.md) - Full installation guide
