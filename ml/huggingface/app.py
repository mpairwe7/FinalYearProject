"""
Hugging Face Space - URA Chatbot Demo
Gradio interface for model inference
"""

import gradio as gr
import numpy as np
from huggingface_hub import hf_hub_download
import joblib
import json

# Configuration
MODEL_REPO = "mpairweLandwind/ura-chatbot"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Load models (cached)
def load_models():
    """Load classifier and embedder from HF Hub."""
    try:
        # Download model files
        clf_path = hf_hub_download(MODEL_REPO, "tag_classifier.joblib")
        encoder_path = hf_hub_download(MODEL_REPO, "label_encoder.joblib")
        
        clf = joblib.load(clf_path)
        encoder = joblib.load(encoder_path)
        
        # Load embedder
        from sentence_transformers import SentenceTransformer
        embedder = SentenceTransformer(EMBED_MODEL)
        
        return clf, encoder, embedder
    except Exception as e:
        print(f"Error loading models: {e}")
        return None, None, None

# Global model instances
clf, encoder, embedder = None, None, None

def initialize():
    """Initialize models on startup."""
    global clf, encoder, embedder
    clf, encoder, embedder = load_models()

def predict_tag(question: str, context: str = "") -> dict:
    """Predict tag for a question."""
    global clf, encoder, embedder
    
    if clf is None:
        initialize()
    
    if clf is None:
        return {
            "error": "Model not loaded",
            "tag": None,
            "confidence": 0
        }
    
    try:
        # Create input text
        text = f"{question} [SEP] {context}" if context else question
        
        # Get embedding
        embedding = embedder.encode([text])
        
        # Predict
        pred_idx = clf.predict(embedding)[0]
        tag = encoder.inverse_transform([pred_idx])[0]
        
        # Get probabilities
        proba = clf.predict_proba(embedding)[0]
        confidence = float(proba.max())
        
        # Top 3 predictions
        top_indices = np.argsort(proba)[-3:][::-1]
        top_tags = [
            {"tag": encoder.classes_[i], "confidence": float(proba[i])}
            for i in top_indices
        ]
        
        return {
            "tag": tag,
            "confidence": confidence,
            "top_predictions": top_tags
        }
    except Exception as e:
        return {
            "error": str(e),
            "tag": None,
            "confidence": 0
        }


def gradio_predict(question: str, context: str = "") -> str:
    """Gradio-friendly prediction function."""
    result = predict_tag(question, context)
    
    if "error" in result and result["error"]:
        return f"❌ Error: {result['error']}"
    
    output = f"## Predicted Tag: **{result['tag']}**\n\n"
    output += f"Confidence: {result['confidence']:.2%}\n\n"
    output += "### Top Predictions:\n"
    
    for pred in result.get("top_predictions", []):
        output += f"- {pred['tag']}: {pred['confidence']:.2%}\n"
    
    return output


# Gradio Interface
with gr.Blocks(title="URA Chatbot - Tag Classifier") as demo:
    gr.Markdown("""
    # 🇺🇬 URA Chatbot - Query Classification
    
    This model classifies Uganda Revenue Authority (URA) queries into relevant categories.
    Enter your tax-related question to see which topic it belongs to.
    """)
    
    with gr.Row():
        with gr.Column():
            question_input = gr.Textbox(
                label="Question",
                placeholder="e.g., How do I pay VAT online?",
                lines=2
            )
            context_input = gr.Textbox(
                label="Additional Context (optional)",
                placeholder="Any additional context for the question...",
                lines=2
            )
            submit_btn = gr.Button("Classify", variant="primary")
        
        with gr.Column():
            output = gr.Markdown(label="Prediction")
    
    submit_btn.click(
        fn=gradio_predict,
        inputs=[question_input, context_input],
        outputs=output
    )
    
    gr.Examples(
        examples=[
            ["How do I register for TIN?", ""],
            ["What are the VAT rates in Uganda?", ""],
            ["How to file annual tax returns?", ""],
            ["What documents are needed at customs?", ""],
            ["How to pay taxes online?", ""],
        ],
        inputs=[question_input, context_input]
    )
    
    gr.Markdown("""
    ---
    **Model Details:**
    - Embedding: sentence-transformers/all-MiniLM-L6-v2
    - Classifier: SGDClassifier (logistic regression)
    - Repository: [mpairweLandwind/ura-chatbot](https://huggingface.co/mpairweLandwind/ura-chatbot)
    """)

if __name__ == "__main__":
    initialize()
    demo.launch()
