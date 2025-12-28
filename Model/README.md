# =============================================================================
# Model Directory
# Trained Model Artifacts
# =============================================================================

This directory contains trained model files for the URA chatbot classifier.

## Model Files

| File | Format | Description |
|------|--------|-------------|
| `tag_classifier.joblib` | sklearn | Main classifier model |
| `label_encoder.joblib` | sklearn | Label encoder for classes |
| `tag_classifier.pth` | PyTorch | PyTorch state dict |
| `tag_classifier.onnx` | ONNX | Cross-platform format |
| `tag_classifier_scripted.pt` | TorchScript | Mobile deployment |
| `class_labels.json` | JSON | List of class labels |

## Model Information

- **Type**: SGDClassifier (Logistic Regression)
- **Embedding**: sentence-transformers/all-MiniLM-L6-v2
- **Input Dimension**: 384
- **Training**: Stratified K-Fold Cross-Validation

## Loading Models

### Python (sklearn)
```python
import joblib

clf = joblib.load("Model/tag_classifier.joblib")
encoder = joblib.load("Model/label_encoder.joblib")
```

### PyTorch
```python
import torch

checkpoint = torch.load("Model/tag_classifier.pth")
# Access: checkpoint['model_state_dict'], checkpoint['classes']
```

### ONNX
```python
import onnxruntime as ort

session = ort.InferenceSession("Model/tag_classifier.onnx")
```

## Training New Models

```bash
python ml/pipelines/train.py --config ml/configs/training_config.yaml --output-dir Model
```

## Version Control

Model files are in `.gitignore` (generated artifacts).
Use DVC or Git LFS for versioning large model files.

For production, models are stored on Hugging Face Hub.
