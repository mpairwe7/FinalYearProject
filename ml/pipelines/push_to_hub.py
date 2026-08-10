"""
Push to Hugging Face Hub Pipeline
Uploads trained models and artifacts to HF Hub
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def push_to_hub(
    model_path: str, repo_id: str, commit_message: str = "Update model", private: bool = False
) -> str:
    """Push model artifacts to Hugging Face Hub."""
    from huggingface_hub import HfApi, create_repo, upload_folder

    HfApi()

    # Create repo if it doesn't exist
    try:
        create_repo(repo_id, private=private, exist_ok=True)
        print(f"✓ Repository ready: https://huggingface.co/{repo_id}")
    except Exception as e:
        print(f"Note: {e}")

    # Upload model artifacts
    model_dir = Path(model_path)

    # Create model card
    model_card = create_model_card(model_dir, repo_id)
    readme_path = model_dir / "README.md"
    with open(readme_path, "w") as f:
        f.write(model_card)

    # Upload
    url = upload_folder(
        folder_path=str(model_dir),
        repo_id=repo_id,
        commit_message=commit_message,
    )

    print(f"✓ Model uploaded: {url}")
    return url


def create_model_card(model_dir: Path, repo_id: str) -> str:
    """Generate HF model card."""
    # Load metrics if available
    metrics_path = Path(PROJECT_ROOT) / "Results" / "metrics" / "training_metrics.json"
    metrics = {}
    if metrics_path.exists():
        with open(metrics_path) as f:
            metrics = json.load(f)

    # Load class labels
    labels_path = model_dir / "class_labels.json"
    labels = []
    if labels_path.exists():
        with open(labels_path) as f:
            labels = json.load(f)

    card = f"""---
license: mit
tags:
  - text-classification
  - sentence-transformers
  - ura-chatbot
  - uganda
  - tax
language:
  - en
  - lg
pipeline_tag: text-classification
---

# URA Chatbot Tag Classifier

This model classifies Uganda Revenue Authority (URA) queries into relevant topic categories.

## Model Description

- **Model Type:** Linear classifier (SGDClassifier with logistic loss)
- **Embedding Model:** sentence-transformers/all-MiniLM-L6-v2
- **Number of Classes:** {len(labels)}
- **Training Framework:** scikit-learn, PyTorch

## Performance Metrics

| Metric | Value |
|--------|-------|
| Accuracy | {metrics.get('test_accuracy', 'N/A'):.4f if isinstance(metrics.get('test_accuracy'), float) else 'N/A'} |
| F1 (macro) | {metrics.get('test_f1_macro', 'N/A'):.4f if isinstance(metrics.get('test_f1_macro'), float) else 'N/A'} |
| Precision | {metrics.get('test_precision', 'N/A'):.4f if isinstance(metrics.get('test_precision'), float) else 'N/A'} |
| Recall | {metrics.get('test_recall', 'N/A'):.4f if isinstance(metrics.get('test_recall'), float) else 'N/A'} |

## Available Formats

- `tag_classifier.joblib` - scikit-learn format
- `tag_classifier.pth` - PyTorch state dict
- `tag_classifier.onnx` - ONNX format
- `tag_classifier_scripted.pt` - TorchScript

## Usage

### Python (sklearn)

```python
import joblib
from sentence_transformers import SentenceTransformer

# Load model
clf = joblib.load("tag_classifier.joblib")
encoder = joblib.load("label_encoder.joblib")
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# Predict
text = "How do I pay VAT?"
embedding = embedder.encode([text])
pred_idx = clf.predict(embedding)[0]
tag = encoder.inverse_transform([pred_idx])[0]
print(f"Predicted tag: {{tag}}")
```

### ONNX Runtime

```python
import onnxruntime as ort
from sentence_transformers import SentenceTransformer
import json

# Load
session = ort.InferenceSession("tag_classifier.onnx")
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
with open("class_labels.json") as f:
    labels = json.load(f)

# Predict
text = "How do I register for TIN?"
embedding = embedder.encode([text]).astype("float32")
logits = session.run(None, {{"embedding": embedding}})[0]
pred_idx = logits.argmax(axis=1)[0]
print(f"Predicted tag: {{labels[pred_idx]}}")
```

## Training Data

Trained on URA FAQ datasets covering:
- VAT and tax obligations
- TIN registration
- Customs procedures
- Business registration
- And more...

## License

MIT License

## Citation

```bibtex
@misc{{ura-chatbot,
  title={{URA Chatbot Tag Classifier}},
  author={{mpairweLandwind}},
  year={{2024}},
  url={{https://huggingface.co/{repo_id}}}
}}
```
"""
    return card


def main():
    parser = argparse.ArgumentParser(description="Push model to Hugging Face Hub")
    parser.add_argument("--model-path", type=str, required=True)
    parser.add_argument("--repo-id", type=str, required=True)
    parser.add_argument("--commit-message", type=str, default="Update model from CI/CD")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("PUSH TO HUGGING FACE HUB")
    print("=" * 60)

    # Check token
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN environment variable not set")
        sys.exit(1)

    # Push
    push_to_hub(args.model_path, args.repo_id, args.commit_message, args.private)

    print(f"\n✓ Model available at: https://huggingface.co/{args.repo_id}")


if __name__ == "__main__":
    main()
