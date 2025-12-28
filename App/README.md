# 🇺🇬 URA Chatbot - Application Directory

This directory contains all application components for the URA Chatbot project.

## Directory Structure

```
App/
├── app.py              # Main Gradio app (HF Spaces deployment)
├── classifier.py       # Legacy classifier interface
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── README_HF.md       # Hugging Face Spaces README
├── backend/           # FastAPI backend API
│   ├── app/
│   │   ├── main.py    # API entry point
│   │   ├── models.py  # Pydantic models
│   │   └── service.py # Business logic
│   └── requirements.txt
└── frontend/          # Next.js web frontend
    ├── src/
    │   ├── app/       # Next.js pages
    │   └── store/     # Zustand state management
    └── package.json
```

## Components

### 1. Gradio App (`app.py`)

Modern chat interface for Hugging Face Spaces deployment.

**Features:**
- 💬 Natural language chat interface
- 🎨 Modern dark theme matching frontend design
- 📱 Responsive layout with sidebar
- 🏷️ AI-powered query classification
- 📚 Knowledge base integration

**Run locally:**
```bash
cd App
pip install -r requirements.txt
python app.py
```

### 2. Backend API (`backend/`)

FastAPI-based REST API for production deployment.

**Endpoints:**
- `GET /health` - Health check
- `POST /v1/chat` - Chat completion

**Run locally:**
```bash
cd App/backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 3. Frontend (`frontend/`)

Next.js 14 web application with modern UI.

**Features:**
- Speech recognition support
- Real-time chat interface
- Glassmorphism design
- Zustand state management

**Run locally:**
```bash
cd App/frontend
bun install
bun dev
```

## Deployment

### Hugging Face Spaces

1. Copy `app.py`, `requirements.txt`, and `README_HF.md` to your Space
2. Rename `README_HF.md` to `README.md`
3. Ensure model files are in the HF Model repository

### Docker

```bash
# Build
docker build -t ura-chatbot .

# Run
docker run -p 7860:7860 ura-chatbot
```

### Vercel (Frontend)

The frontend is configured for Vercel deployment via `vercel.json`.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HF_MODEL_REPO` | Hugging Face model repository | `mpairweLandwind/ura-chatbot` |
| `HF_TOKEN` | Hugging Face API token | - |
| `API_URL` | Backend API URL | `http://localhost:8000` |

## Development

### Prerequisites

- Python 3.11+
- Node.js 18+ / Bun
- Trained model files in `Model/` directory

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run Gradio app
python app.py

# App will be available at http://localhost:7860
```

## Links

- [GitHub Repository](https://github.com/mpairweLandwind/FinalYearProject)
- [Hugging Face Space](https://huggingface.co/spaces/mpairweLandwind/ura-chatbot)
- [URA Official Website](https://www.ura.go.ug)


### Python API
```python
from classifier import predict_tag

result = predict_tag("How do I pay VAT?")
print(f"Tag: {result['tag']}")
print(f"Confidence: {result['confidence']:.2%}")
```

## Project Structure

```
App/
├── classifier.py      # Main Gradio application
├── README.md          # This file (HF Space metadata)
└── requirements.txt   # Python dependencies
```

## Training

The model is trained on URA FAQ datasets. To retrain:

```bash
python ml/pipelines/train.py --config ml/configs/training_config.yaml
```

## Links

- **Repository**: [github.com/mpairweLandwind/FinalYearProject](https://github.com/mpairweLandwind/FinalYearProject)
- **Documentation**: [MLOps Pipeline Guide](../docs/MLOPS_PIPELINE.md)

## License

MIT License - See repository for details.
