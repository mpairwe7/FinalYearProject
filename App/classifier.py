# =============================================================================
# URA Chatbot - Web Application
# Gradio-based classifier interface for deployment
# =============================================================================

import os
import sys
from pathlib import Path

import gradio as gr
import numpy as np
import joblib

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configuration
MODEL_DIR = PROJECT_ROOT / "Model"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Global model instances
clf = None
encoder = None
embedder = None


def load_models():
    """Load classifier and embedder from local Model directory."""
    global clf, encoder, embedder
    
    try:
        # Load from local Model directory
        clf_path = MODEL_DIR / "tag_classifier.joblib"
        encoder_path = MODEL_DIR / "label_encoder.joblib"
        
        if clf_path.exists() and encoder_path.exists():
            clf = joblib.load(clf_path)
            encoder = joblib.load(encoder_path)
            print(f"✓ Loaded classifier from {clf_path}")
            print(f"✓ Loaded encoder with {len(encoder.classes_)} classes")
        else:
            print(f"⚠ Model files not found in {MODEL_DIR}")
            print("  Run training pipeline first: python ml/pipelines/train.py")
            return False
        
        # Load embedder
        from sentence_transformers import SentenceTransformer
        embedder = SentenceTransformer(EMBED_MODEL)
        print(f"✓ Loaded embedder: {EMBED_MODEL}")
        
        return True
    except Exception as e:
        print(f"❌ Error loading models: {e}")
        return False


def predict_tag(question: str, context: str = "") -> dict:
    """
    Predict tag for a question with optional context.
    
    Args:
        question: The user's question text
        context: Optional additional context
        
    Returns:
        Dictionary with prediction results
    """
    global clf, encoder, embedder
    
    if clf is None or encoder is None or embedder is None:
        success = load_models()
        if not success:
            return {
                "error": "Model not loaded. Please train the model first.",
                "tag": None,
                "confidence": 0,
                "top_predictions": []
            }
    
    try:
        # Create input text
        text = f"{question} [SEP] {context}" if context.strip() else question
        
        # Get embedding
        embedding = embedder.encode([text])
        
        # Predict
        pred_idx = clf.predict(embedding)[0]
        tag = encoder.inverse_transform([pred_idx])[0]
        
        # Get probabilities
        proba = clf.predict_proba(embedding)[0]
        confidence = float(proba.max())
        
        # Top 5 predictions
        top_indices = np.argsort(proba)[-5:][::-1]
        top_predictions = [
            {
                "tag": encoder.classes_[i],
                "confidence": float(proba[i]),
                "percentage": f"{proba[i]*100:.1f}%"
            }
            for i in top_indices
        ]
        
        return {
            "tag": tag,
            "confidence": confidence,
            "percentage": f"{confidence*100:.1f}%",
            "top_predictions": top_predictions,
            "error": None
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "tag": None,
            "confidence": 0,
            "top_predictions": []
        }


def format_prediction(question: str, context: str = "") -> str:
    """Format prediction for Gradio display."""
    result = predict_tag(question, context)
    
    if result.get("error"):
        return f"❌ **Error:** {result['error']}"
    
    output = f"## 🏷️ Predicted Tag: **{result['tag']}**\n\n"
    output += f"**Confidence:** {result['percentage']}\n\n"
    output += "---\n\n"
    output += "### Top 5 Predictions:\n\n"
    output += "| Rank | Tag | Confidence |\n"
    output += "|------|-----|------------|\n"
    
    for i, pred in enumerate(result.get("top_predictions", []), 1):
        output += f"| {i} | {pred['tag']} | {pred['percentage']} |\n"
    
    return output


def get_model_info() -> str:
    """Get information about loaded model."""
    global clf, encoder
    
    if clf is None:
        load_models()
    
    if clf is None or encoder is None:
        return "Model not loaded"
    
    return f"""
**Model Information:**
- Classifier: SGDClassifier (Logistic Regression)
- Embedding Model: {EMBED_MODEL}
- Number of Classes: {len(encoder.classes_)}
- Model Directory: {MODEL_DIR}
"""


# =============================================================================
# Gradio Interface
# =============================================================================

with gr.Blocks(
    title="URA Chatbot - Tax Query Classifier",
    theme=gr.themes.Soft(),
    css="""
        .container { max-width: 900px; margin: auto; }
        .header { text-align: center; margin-bottom: 20px; }
    """
) as demo:
    
    gr.Markdown("""
    # 🇺🇬 URA Chatbot - Tax Query Classifier
    
    This application classifies Uganda Revenue Authority (URA) tax-related queries 
    into relevant categories to help route questions to the appropriate resources.
    
    **How to use:**
    1. Enter your tax-related question in the text box
    2. Optionally add context for better classification
    3. Click "Classify" to see the predicted category
    """)
    
    with gr.Row():
        with gr.Column(scale=1):
            question_input = gr.Textbox(
                label="📝 Your Question",
                placeholder="e.g., How do I pay VAT online?",
                lines=3,
                max_lines=5
            )
            
            context_input = gr.Textbox(
                label="📋 Additional Context (Optional)",
                placeholder="Any additional context to help classify the question...",
                lines=2,
                max_lines=3
            )
            
            with gr.Row():
                clear_btn = gr.Button("🗑️ Clear", variant="secondary")
                submit_btn = gr.Button("🔍 Classify", variant="primary")
        
        with gr.Column(scale=1):
            output = gr.Markdown(
                label="Classification Result",
                value="*Enter a question and click Classify*"
            )
    
    # Examples
    gr.Markdown("### 💡 Example Questions")
    gr.Examples(
        examples=[
            ["How do I register for TIN?", ""],
            ["What are the VAT rates in Uganda?", ""],
            ["How to file annual tax returns?", ""],
            ["What documents are needed at customs?", ""],
            ["How to pay taxes online?", ""],
            ["What is withholding tax?", ""],
            ["How to register a business for tax?", ""],
            ["What are the penalties for late filing?", ""],
        ],
        inputs=[question_input, context_input],
        outputs=output,
        fn=format_prediction,
        cache_examples=False
    )
    
    # Model info
    with gr.Accordion("ℹ️ Model Information", open=False):
        model_info = gr.Markdown(get_model_info())
        refresh_btn = gr.Button("🔄 Refresh Info", size="sm")
        refresh_btn.click(fn=get_model_info, outputs=model_info)
    
    # Event handlers
    submit_btn.click(
        fn=format_prediction,
        inputs=[question_input, context_input],
        outputs=output
    )
    
    clear_btn.click(
        fn=lambda: ("", "", "*Enter a question and click Classify*"),
        outputs=[question_input, context_input, output]
    )
    
    # Footer
    gr.Markdown("""
    ---
    **Built with:** Gradio • scikit-learn • Sentence Transformers
    
    **Repository:** [github.com/mpairweLandwind/FinalYearProject](https://github.com/mpairweLandwind/FinalYearProject)
    """)


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("URA Chatbot - Tax Query Classifier")
    print("=" * 60)
    
    # Initialize models
    success = load_models()
    
    if not success:
        print("\n⚠️ Warning: Models not loaded. The app will show errors until trained.")
        print("   Run: python ml/pipelines/train.py --config ml/configs/training_config.yaml")
    
    # Launch Gradio app
    print("\n🚀 Launching Gradio interface...")
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True
    )
