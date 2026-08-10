# Model Directory — Trained Artifacts

This directory contains trained model files for the URA chatbot.

## Tag Classifier Models

| File | Format | Description | Use Case |
|------|--------|-------------|----------|
| `tag_classifier.joblib` | sklearn | SGDClassifier model | Backend API |
| `label_encoder.joblib` | sklearn | Label encoder for 41 classes | Backend API |
| `tag_classifier.pth` | PyTorch | PyTorch state dict | Web / fine-tuning |
| `tag_classifier.onnx` | ONNX | Cross-platform (opset 12) | ONNX Runtime |
| `tag_classifier_scripted.pt` | TorchScript | JIT-compiled model | Mobile (LibTorch) |
| `class_labels.json` | JSON | List of class labels | All platforms |

## LLM Models

| Model | Format | Use Case | Size |
|-------|--------|----------|------|
| Qwen2.5-3B-Instruct | HF safetensors | API backend inference (`App/backend/app/llm.py`) | ~6 GB |
| Gemma-2-2B (fine-tuned) | GGUF Q4_K_M | On-device mobile inference | ~1.5 GB |

**API backend**: Qwen2.5-3B-Instruct auto-downloads from HuggingFace on first request.

**Mobile**: Gemma-2-2B GGUF is exported via `ml/scripts/export_mobile.py` to `artifacts/mobile/`.

### Mobile Model Pipeline

```
google/gemma-2-2b-it (base, ~4 GB FP16)
  → QLoRA fine-tune (LoRA r=8, α=16, NF4 4-bit)
    → Merge LoRA adapters → safetensors
      → Convert to GGUF F16 (llama.cpp)
        → Quantize to Q4_K_M (~1.5 GB)
          → Validate (SHA-256 + load test + manifest)
```

| Quantization | Size | Quality | Use Case |
|-------------|------|---------|----------|
| Q4_K_M | ~1.5 GB | Good (recommended) | Production mobile |
| Q5_K_M | ~1.8 GB | Better | High-end devices |
| Q8_0 | ~2.5 GB | High | Tablets / dev |
| F16 | ~4.0 GB | Lossless | Benchmarking only |

## Classifier Info

- **Type**: SGDClassifier (Logistic Regression)
- **Embedding**: sentence-transformers/all-MiniLM-L6-v2 (384-dim)
- **Training**: Stratified 5-Fold Cross-Validation

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

### GGUF (mobile — llama-cpp-python)
```python
from llama_cpp import Llama

llm = Llama(model_path="artifacts/mobile/ura-gemma-2b-q4_k_m.gguf", n_ctx=1024)
output = llm("What is VAT?", max_tokens=200)
```

## Training & Export

```bash
# Train tag classifier
python ml/pipelines/train.py --config ml/configs/training_config.yaml --output-dir Model

# Fine-tune Gemma-2B for mobile
python ml/scripts/fine_tune_gemma.py --target mobile_gemma_2b

# Export to GGUF for mobile
python ml/scripts/export_mobile.py --adapter artifacts/models/ura-gemma-2-2b-it-*/final
```

## Version Control

Model files are in `.gitignore` (generated artifacts).
Use DVC or Git LFS for versioning large model files.
Production models are stored on Hugging Face Hub (`mpairweLandwind/ura-chatbot`).
