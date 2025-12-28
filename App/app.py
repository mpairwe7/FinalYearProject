# =============================================================================
# URA Chatbot - Enhanced Gradio Web Application
# Hugging Face Spaces deployment with modern UI matching Next.js frontend
# Full implementation of frontend design with enhanced UX fluidity
# =============================================================================

import os
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
import time
import json

import gradio as gr
import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configuration
MODEL_DIR = PROJECT_ROOT / "Model"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_MODEL_REPO = os.environ.get("HF_MODEL_REPO", "mpairweLandwind/ura-chatbot")

# Global model instances
clf = None
encoder = None
embedder = None
qa_knowledge = {}  # Store Q&A pairs for retrieval
qa_embeddings = {}  # Pre-computed embeddings for fast retrieval


def load_models():
    """Load classifier, embedder and knowledge base."""
    global clf, encoder, embedder, qa_knowledge
    
    try:
        import joblib
        from sentence_transformers import SentenceTransformer
        
        # Try loading from Hugging Face Hub first (for deployment)
        try:
            from huggingface_hub import hf_hub_download
            clf_path = hf_hub_download(repo_id=HF_MODEL_REPO, filename="tag_classifier.joblib")
            encoder_path = hf_hub_download(repo_id=HF_MODEL_REPO, filename="label_encoder.joblib")
            clf = joblib.load(clf_path)
            encoder = joblib.load(encoder_path)
            print(f"✓ Loaded models from Hugging Face: {HF_MODEL_REPO}")
        except Exception as e:
            print(f"⚠ HF Hub load failed: {e}, trying local...")
            # Fallback to local Model directory
            clf_path = MODEL_DIR / "tag_classifier.joblib"
            encoder_path = MODEL_DIR / "label_encoder.joblib"
            
            if clf_path.exists() and encoder_path.exists():
                clf = joblib.load(clf_path)
                encoder = joblib.load(encoder_path)
                print(f"✓ Loaded classifier from {clf_path}")
            else:
                print(f"⚠ Model files not found in {MODEL_DIR}")
                return False
        
        # Load embedder
        embedder = SentenceTransformer(EMBED_MODEL)
        print(f"✓ Loaded embedder: {EMBED_MODEL}")
        
        # Load Q&A knowledge base for better responses
        load_knowledge_base()
        
        return True
    except Exception as e:
        print(f"❌ Error loading models: {e}")
        return False


def load_knowledge_base():
    """Load Q&A pairs from CSV files for response generation."""
    global qa_knowledge, qa_embeddings, embedder
    import pandas as pd
    
    data_dir = PROJECT_ROOT / "Data" / "dataset"
    if not data_dir.exists():
        data_dir = PROJECT_ROOT / "datasets"
    
    if data_dir.exists():
        for csv_file in data_dir.glob("*.csv"):
            try:
                df = pd.read_csv(csv_file)
                # Find question/answer columns
                q_col = next((c for c in df.columns if c.lower() in ['question', 'q', 'query']), None)
                a_col = next((c for c in df.columns if c.lower() in ['answer', 'a', 'response']), None)
                
                if q_col and a_col:
                    tag = csv_file.stem.replace('ura_', '').replace('_faqs', '')
                    if tag not in qa_knowledge:
                        qa_knowledge[tag] = []
                    
                    for _, row in df.iterrows():
                        if pd.notna(row[q_col]) and pd.notna(row[a_col]):
                            qa_knowledge[tag].append({
                                'question': str(row[q_col]),
                                'answer': str(row[a_col])
                            })
            except Exception as e:
                print(f"⚠ Error loading {csv_file}: {e}")
        
        print(f"✓ Loaded knowledge base: {len(qa_knowledge)} categories")
        
        # Pre-compute embeddings for faster retrieval
        if embedder is not None:
            for tag, qa_pairs in qa_knowledge.items():
                questions = [qa['question'] for qa in qa_pairs[:100]]  # Limit for memory
                if questions:
                    qa_embeddings[tag] = embedder.encode(questions)


def predict_tag(question: str) -> Dict[str, Any]:
    """Predict tag for a question."""
    global clf, encoder, embedder
    
    if clf is None or encoder is None or embedder is None:
        success = load_models()
        if not success:
            return {"error": "Model not loaded", "tag": None, "confidence": 0, "top_predictions": []}
    
    try:
        embedding = embedder.encode([question])
        pred_idx = clf.predict(embedding)[0]
        tag = encoder.inverse_transform([pred_idx])[0]
        proba = clf.predict_proba(embedding)[0]
        confidence = float(proba.max())
        
        top_indices = np.argsort(proba)[-5:][::-1]
        top_predictions = [
            {"tag": encoder.classes_[i], "confidence": float(proba[i])}
            for i in top_indices
        ]
        
        return {
            "tag": tag,
            "confidence": confidence,
            "top_predictions": top_predictions,
            "error": None
        }
    except Exception as e:
        return {"error": str(e), "tag": None, "confidence": 0, "top_predictions": []}


def find_best_answer(question: str, tag: str) -> Optional[str]:
    """Find the best matching answer from knowledge base using semantic search."""
    global qa_knowledge, qa_embeddings, embedder
    
    if embedder is None:
        return None
    
    # Clean tag name
    tag_key = tag.lower().replace(' ', '_').replace('-', '_')
    
    # Try to find matching Q&A pairs
    qa_pairs = qa_knowledge.get(tag_key, [])
    tag_embs = qa_embeddings.get(tag_key)
    
    if not qa_pairs:
        # Try partial matching
        for key in qa_knowledge.keys():
            if tag_key in key or key in tag_key:
                qa_pairs = qa_knowledge[key]
                tag_embs = qa_embeddings.get(key)
                break
    
    if not qa_pairs:
        return None
    
    try:
        question_emb = embedder.encode([question])[0]
        
        # Use pre-computed embeddings if available
        if tag_embs is not None:
            scores = np.dot(tag_embs, question_emb) / (
                np.linalg.norm(tag_embs, axis=1) * np.linalg.norm(question_emb)
            )
            best_idx = np.argmax(scores)
            best_score = scores[best_idx]
            
            if best_score > 0.45:  # Threshold for relevance
                return qa_pairs[best_idx]['answer']
        else:
            # Fallback to on-the-fly computation
            best_score = -1
            best_answer = None
            
            for qa in qa_pairs[:30]:
                q_emb = embedder.encode([qa['question']])[0]
                score = np.dot(question_emb, q_emb) / (np.linalg.norm(question_emb) * np.linalg.norm(q_emb))
                if score > best_score:
                    best_score = score
                    best_answer = qa['answer']
            
            if best_score > 0.45:
                return best_answer
    except:
        pass
    
    # Return first answer as fallback
    return qa_pairs[0]['answer'] if qa_pairs else None


def format_tag_display(tag: str) -> str:
    """Format tag for display."""
    return tag.replace('_', ' ').title()


def generate_response(message: str) -> Tuple[str, str, str]:
    """Generate response with classification info."""
    if not message.strip():
        return "", "", ""
    
    # Get prediction
    result = predict_tag(message)
    
    if result.get("error"):
        response = f"I apologize, but I'm having trouble processing your question. Please try again."
        tag_display = "Error"
        confidence_display = "0%"
    else:
        tag = result['tag']
        confidence = result['confidence']
        tag_display = format_tag_display(tag)
        confidence_display = f"{confidence*100:.0f}%"
        
        # Try to find a relevant answer
        answer = find_best_answer(message, tag)
        
        if answer:
            response = answer
        else:
            # Provide helpful generic response based on tag
            response = f"""Thank you for your question about **{tag_display}**.

I can help you with information on this topic. For the most accurate and up-to-date information, I recommend:

• Visiting the URA website at [ura.go.ug](https://www.ura.go.ug)
• Calling the URA helpline
• Visiting your nearest URA office

Is there anything more specific I can help you with?"""
    
    return response, tag_display, confidence_display


def chat_fn(message: str, history: List[List[str]]) -> Tuple[List[List[str]], str, str]:
    """Chat function for Gradio chatbot."""
    if not message.strip():
        return history, "", ""
    
    response, tag, confidence = generate_response(message)
    history.append([message, response])
    
    return history, tag, confidence


def clear_chat() -> Tuple[List[List[str]], str, str, str]:
    """Clear chat and reset state."""
    initial_message = [
        [None, "👋 Hi! I'm the URA Chatbot. I can answer your questions about Uganda Revenue Authority services, tax policies, and procedures. Type or speak your question to begin!"]
    ]
    return initial_message, "", "", ""


def set_example(example: str) -> str:
    """Set example text in input."""
    return example


# =============================================================================
# Custom CSS - Exact replica of Next.js frontend design
# Glassmorphism, animations, and modern UI fluidity
# =============================================================================

CUSTOM_CSS = """
/* ============================================
   CSS Variables - Exact match to frontend
   ============================================ */
:root {
    --bg: #05060d;
    --panel: #0d1221;
    --panel-2: #11172a;
    --card: #0d1221;
    --glass: rgba(255,255,255,0.05);
    --accent: #82dbf7;
    --accent-strong: #16a9d7;
    --muted: #9fb4d0;
    --text: #e9eef6;
    --shadow: 0 12px 35px rgba(5, 6, 13, 0.55);
}

/* ============================================
   Global Styles & Background
   ============================================ */
* {
    box-sizing: border-box;
}

.gradio-container {
    font-family: 'Space Grotesk', 'Soehne', 'Manrope', system-ui, -apple-system, sans-serif !important;
    background: 
        radial-gradient(circle at 15% 20%, rgba(130,219,247,0.18), transparent 35%),
        radial-gradient(circle at 85% 0%, rgba(116,128,255,0.16), transparent 32%),
        linear-gradient(140deg, #04050a 0%, #0c1324 35%, #080b17 100%) !important;
    color: var(--text) !important;
    min-height: 100vh !important;
    padding: 0 !important;
}

.gradio-container > .main {
    max-width: 1220px !important;
    margin: 0 auto !important;
    padding: 2.25rem 1.25rem 3.25rem !important;
}

/* ============================================
   Hero Section
   ============================================ */
.hero-section {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}

.hero-content {
    flex: 1;
    min-width: 300px;
}

.hero-title {
    font-size: clamp(1.5rem, 4vw, 2.25rem);
    font-weight: 700;
    margin: 0.5rem 0 0.35rem;
    background: linear-gradient(135deg, #e9eef6, #82dbf7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.hero-sub {
    color: var(--muted);
    margin: 0;
    max-width: 60ch;
    font-size: 1rem;
    line-height: 1.5;
}

/* ============================================
   Badge & Pill Components
   ============================================ */
.badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.4rem 0.8rem;
    border-radius: 999px;
    background: rgba(130,219,247,0.14);
    color: var(--accent);
    font-weight: 700;
    font-size: 0.9rem;
    letter-spacing: 0.01em;
    border: 1px solid rgba(130,219,247,0.25);
    box-shadow: 0 10px 30px rgba(0,0,0,0.25);
    animation: pulse-glow 3s ease-in-out infinite;
}

@keyframes pulse-glow {
    0%, 100% { box-shadow: 0 10px 30px rgba(0,0,0,0.25); }
    50% { box-shadow: 0 10px 30px rgba(130,219,247,0.3); }
}

.pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0.65rem;
    border-radius: 999px;
    background: rgba(255,255,255,0.05);
    color: var(--muted);
    font-weight: 600;
    font-size: 0.85rem;
    border: 1px solid rgba(255,255,255,0.08);
}

.status-indicator {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.45rem 0.65rem;
    background: rgba(255,255,255,0.04);
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.06);
    color: var(--muted);
    font-weight: 600;
    font-size: 0.85rem;
}

.status-indicator.active {
    background: rgba(130,219,247,0.1);
    border-color: rgba(130,219,247,0.25);
    color: var(--accent);
}

/* ============================================
   Card Component - Glassmorphism
   ============================================ */
.card, .block, .form, .panel {
    background: linear-gradient(150deg, rgba(17,24,39,0.75), rgba(11,17,32,0.92)) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 18px !important;
    padding: 1.35rem !important;
    box-shadow: var(--shadow) !important;
    backdrop-filter: blur(10px) !important;
    -webkit-backdrop-filter: blur(10px) !important;
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
}

.card-title {
    margin: 0;
    color: var(--text);
    font-size: 1.1rem;
    font-weight: 600;
}

.card-subtitle {
    color: var(--muted);
    font-size: 0.9rem;
    margin: 0;
}

/* ============================================
   Chat Interface
   ============================================ */
.chat-container {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    min-height: 520px;
}

/* Chatbot styling */
.chatbot, #chatbot {
    background: transparent !important;
    border: none !important;
    flex: 1 !important;
    overflow-y: auto !important;
}

.chatbot .message-wrap {
    padding: 0 !important;
}

.chatbot .message {
    padding: 0.95rem 1.05rem !important;
    border-radius: 14px !important;
    line-height: 1.55 !important;
    max-width: 85% !important;
    animation: message-in 0.3s ease-out !important;
}

@keyframes message-in {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.chatbot .user {
    background: rgba(130,219,247,0.12) !important;
    border: 1px solid rgba(130,219,247,0.32) !important;
    box-shadow: 0 12px 24px rgba(0,0,0,0.28) !important;
    margin-left: auto !important;
}

.chatbot .bot {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    box-shadow: 0 12px 24px rgba(0,0,0,0.28) !important;
}

/* Avatar styling */
.chatbot .avatar-container {
    width: 38px !important;
    height: 38px !important;
    border-radius: 12px !important;
    box-shadow: 0 10px 22px rgba(0,0,0,0.35) !important;
}

.chatbot .avatar-container.user {
    background: rgba(130,219,247,0.15) !important;
    border: 1px solid rgba(130,219,247,0.35) !important;
}

.chatbot .avatar-container.bot {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
}

/* ============================================
   Input Row
   ============================================ */
.input-row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 0.6rem;
    align-items: center;
}

/* Textbox styling */
textarea, input[type="text"], .textbox {
    width: 100% !important;
    padding: 0.95rem 1rem !important;
    border-radius: 14px !important;
    border: 1px solid rgba(255,255,255,0.09) !important;
    background: #0b1120 !important;
    color: var(--text) !important;
    font-size: 1rem !important;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03) !important;
    transition: border-color 150ms ease, box-shadow 150ms ease !important;
}

textarea:focus, input[type="text"]:focus, .textbox:focus {
    outline: none !important;
    border-color: rgba(130,219,247,0.4) !important;
    box-shadow: 0 0 0 3px rgba(130,219,247,0.1), inset 0 1px 0 rgba(255,255,255,0.03) !important;
}

textarea::placeholder, input::placeholder {
    color: rgba(159, 180, 208, 0.6) !important;
}

/* ============================================
   Buttons - Matching frontend exactly
   ============================================ */
button, .button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0.4rem !important;
    padding: 0.8rem 1.05rem !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    cursor: pointer !important;
    transition: transform 120ms ease, box-shadow 140ms ease, opacity 120ms ease, background 120ms ease !important;
}

button.primary, .gr-button-primary {
    background: linear-gradient(135deg, var(--accent), var(--accent-strong)) !important;
    color: #04101f !important;
    border: none !important;
    box-shadow: 0 10px 28px rgba(130,219,247,0.35) !important;
}

button.primary:hover, .gr-button-primary:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 14px 32px rgba(130,219,247,0.45) !important;
}

button.primary:active, .gr-button-primary:active {
    transform: translateY(0) !important;
}

button.secondary, .gr-button-secondary {
    background: rgba(255,255,255,0.05) !important;
    color: var(--text) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    box-shadow: 0 8px 18px rgba(0,0,0,0.25) !important;
}

button.secondary:hover, .gr-button-secondary:hover {
    transform: translateY(-1px) !important;
    border-color: rgba(130,219,247,0.3) !important;
    background: rgba(130,219,247,0.08) !important;
}

button:disabled {
    opacity: 0.5 !important;
    cursor: not-allowed !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ============================================
   Quick Prompt Chips
   ============================================ */
.chip-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.5rem;
}

.chip, .example-btn {
    display: inline-flex !important;
    align-items: center !important;
    gap: 0.5rem !important;
    width: 100% !important;
    padding: 0.8rem 0.95rem !important;
    border-radius: 12px !important;
    background: rgba(255,255,255,0.04) !important;
    color: var(--text) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    cursor: pointer !important;
    text-align: left !important;
    font-size: 0.9rem !important;
    transition: transform 120ms ease, border-color 120ms ease, background 120ms ease !important;
}

.chip:hover, .example-btn:hover {
    transform: translateY(-2px) !important;
    border-color: rgba(130,219,247,0.4) !important;
    background: rgba(130,219,247,0.08) !important;
    box-shadow: 0 8px 20px rgba(130,219,247,0.15) !important;
}

/* ============================================
   Info Panel & Accordion
   ============================================ */
.info-panel {
    background: linear-gradient(120deg, rgba(130,219,247,0.12), rgba(9,14,28,0.95)) !important;
    border: 1px solid rgba(130,219,247,0.25) !important;
    border-radius: 14px !important;
    padding: 1rem !important;
    margin-top: 1rem !important;
}

.accordion {
    background: linear-gradient(150deg, rgba(17,24,39,0.6), rgba(11,17,32,0.8)) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 14px !important;
    margin-bottom: 0.75rem !important;
}

.accordion summary, .accordion-header {
    color: var(--text) !important;
    font-weight: 600 !important;
    padding: 0.75rem 1rem !important;
    cursor: pointer !important;
}

.accordion-content {
    padding: 0 1rem 1rem !important;
    color: var(--muted) !important;
    line-height: 1.6 !important;
}

/* ============================================
   Category Tags
   ============================================ */
.category-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
}

.category-tag {
    display: inline-flex;
    align-items: center;
    padding: 0.35rem 0.7rem;
    background: rgba(130,219,247,0.1);
    border: 1px solid rgba(130,219,247,0.2);
    border-radius: 8px;
    font-size: 0.8rem;
    color: var(--accent);
    transition: all 120ms ease;
}

.category-tag:hover {
    background: rgba(130,219,247,0.18);
    border-color: rgba(130,219,247,0.35);
}

/* ============================================
   Classification Display
   ============================================ */
.classification-box {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1rem;
    background: rgba(130,219,247,0.08);
    border: 1px solid rgba(130,219,247,0.2);
    border-radius: 12px;
    margin-top: 0.5rem;
}

.classification-label {
    color: var(--muted);
    font-size: 0.85rem;
}

.classification-value {
    color: var(--accent);
    font-weight: 700;
    font-size: 1rem;
}

.confidence-bar {
    flex: 1;
    height: 6px;
    background: rgba(255,255,255,0.1);
    border-radius: 3px;
    overflow: hidden;
}

.confidence-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent-strong));
    border-radius: 3px;
    transition: width 0.5s ease;
}

/* ============================================
   Footer
   ============================================ */
.footer {
    text-align: center;
    padding: 2rem 1rem 1rem;
    color: var(--muted);
    font-size: 0.9rem;
}

.footer a {
    color: var(--accent);
    text-decoration: none;
    transition: color 120ms ease;
}

.footer a:hover {
    color: var(--accent-strong);
    text-decoration: underline;
}

/* ============================================
   Scrollbar - Thin & Elegant
   ============================================ */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

::-webkit-scrollbar-track {
    background: rgba(255,255,255,0.02);
    border-radius: 3px;
}

::-webkit-scrollbar-thumb {
    background: rgba(130,219,247,0.3);
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(130,219,247,0.5);
}

/* Firefox scrollbar */
* {
    scrollbar-width: thin;
    scrollbar-color: rgba(130,219,247,0.3) rgba(255,255,255,0.02);
}

/* ============================================
   Responsive Design
   ============================================ */
@media (max-width: 900px) {
    .hero-section {
        flex-direction: column;
        text-align: center;
    }
    
    .chat-container {
        min-height: 400px;
    }
    
    .input-row {
        grid-template-columns: 1fr;
        gap: 0.5rem;
    }
    
    .input-row button {
        width: 100%;
    }
}

/* ============================================
   Gradio Specific Overrides
   ============================================ */
.contain {
    background: transparent !important;
}

.wrap {
    background: transparent !important;
}

label {
    color: var(--muted) !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
}

.prose, .markdown-text, .md {
    color: var(--text) !important;
}

.prose h1, .prose h2, .prose h3, .prose h4,
.markdown-text h1, .markdown-text h2, .markdown-text h3 {
    color: var(--accent) !important;
    font-weight: 700 !important;
}

.prose strong, .markdown-text strong {
    color: var(--text) !important;
}

.prose a, .markdown-text a {
    color: var(--accent) !important;
}

.prose ul, .prose ol {
    color: var(--muted) !important;
}

/* Hide Gradio footer */
footer {
    display: none !important;
}

/* Loading animation */
.generating {
    animation: pulse 1.5s ease-in-out infinite !important;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
"""

# =============================================================================
# Gradio Interface - Full replica of Next.js frontend
# =============================================================================

# Starter prompts matching frontend
STARTER_PROMPTS = [
    "What services does URA provide?",
    "How do I submit a document online?",
    "What are the VAT rates in Uganda?",
    "How do I register for TIN?",
    "What documents are needed at customs?",
    "How to pay taxes online?",
]

# SVG Icons matching frontend
ICONS = {
    "sparkles": """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3v3"/><path d="M12 18v3"/><path d="m5.64 5.64 2.12 2.12"/><path d="m16.24 16.24 2.12 2.12"/><path d="M3 12h3"/><path d="M18 12h3"/><path d="m5.64 18.36 2.12-2.12"/><path d="m16.24 7.76 2.12-2.12"/><path d="m9 12 3 3 3-3-3-3-3 3Z"/></svg>""",
    "headphones": """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3"/></svg>""",
    "send": """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 12 3 4l18 8-18 8 1-8h9"/></svg>""",
    "mic": """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"/><path d="M19 12a7 7 0 0 1-14 0"/><path d="M12 19v3"/></svg>""",
}


def create_interface():
    """Create the Gradio interface matching Next.js frontend exactly."""
    
    # Initial greeting
    initial_history = [
        [None, "👋 Hi! I'm the URA Chatbot. I can answer your questions about Uganda Revenue Authority services, tax policies, and procedures.\n\nType your question below or tap one of the quick prompts to get started!"]
    ]
    
    with gr.Blocks(
        title="URA Chatbot - Uganda Revenue Authority Assistant",
        theme=gr.themes.Base(
            primary_hue=gr.themes.colors.cyan,
            secondary_hue=gr.themes.colors.blue,
            neutral_hue=gr.themes.colors.slate,
            font=gr.themes.GoogleFont("Space Grotesk"),
        ),
        css=CUSTOM_CSS,
    ) as demo:
        
        # ============================================
        # Hero Section - Matching frontend exactly
        # ============================================
        gr.HTML(f"""
            <div class="hero-section">
                <div class="hero-content">
                    <div class="badge">
                        {ICONS['sparkles']}
                        <span>Live assistant</span>
                    </div>
                    <h1 class="hero-title">🇺🇬 URA Chatbot</h1>
                    <p class="hero-sub">
                        Natural chat with speech and text. Ask about URA services, policies, 
                        or upload workflows and get concise answers.
                    </p>
                </div>
                <div class="pill">
                    {ICONS['headphones']}
                    <span>Voice ready</span>
                </div>
            </div>
        """)
        
        # ============================================
        # Main Layout - Two Column Grid
        # ============================================
        with gr.Row(equal_height=False):
            
            # ----------------------------------------
            # Left Column - Chat Interface
            # ----------------------------------------
            with gr.Column(scale=3, min_width=400):
                # Card Header
                gr.HTML("""
                    <div class="card-header">
                        <div>
                            <h2 class="card-title">💬 Conversation</h2>
                            <p class="card-subtitle">Context-aware AI responses</p>
                        </div>
                        <div class="status-indicator active">
                            <span style="width: 8px; height: 8px; background: #82dbf7; border-radius: 50%; animation: pulse-glow 2s infinite;"></span>
                            Ready
                        </div>
                    </div>
                """)
                
                # Chat Interface
                chatbot = gr.Chatbot(
                    value=initial_history,
                    height=420,
                    show_label=False,
                    container=False,
                    bubble_full_width=False,
                    avatar_images=(
                        None,  # User avatar
                        "https://api.dicebear.com/7.x/bottts/svg?seed=ura&backgroundColor=82dbf7&scale=90"
                    ),
                    render_markdown=True,
                    likeable=False,
                )
                
                # Classification Display
                with gr.Row(visible=True):
                    with gr.Column(scale=1):
                        tag_display = gr.HTML(
                            value="",
                            visible=False,
                        )
                
                # Input Area
                gr.HTML('<div style="margin-top: 0.75rem;"></div>')
                
                with gr.Row():
                    msg_input = gr.Textbox(
                        placeholder="Ask anything about URA...",
                        show_label=False,
                        container=False,
                        scale=5,
                        lines=1,
                        max_lines=3,
                    )
                    clear_btn = gr.Button(
                        "🗑️",
                        variant="secondary",
                        scale=0,
                        min_width=50,
                    )
                    send_btn = gr.Button(
                        "Send →",
                        variant="primary",
                        scale=1,
                    )
                
                gr.HTML("""
                    <p style="color: var(--muted); font-size: 0.85rem; margin-top: 0.5rem;">
                        Press <kbd style="background: rgba(255,255,255,0.1); padding: 0.15rem 0.4rem; border-radius: 4px; font-size: 0.8rem;">Enter</kbd> to send, or tap the mic to dictate.
                    </p>
                """)
            
            # ----------------------------------------
            # Right Column - Sidebar
            # ----------------------------------------
            with gr.Column(scale=2, min_width=280):
                
                # Quick Prompts Section
                gr.HTML("""
                    <div class="card-header">
                        <div>
                            <h3 class="card-title">💡 Quick prompts</h3>
                            <span class="card-subtitle">Tap to fill and edit</span>
                        </div>
                        <div class="pill">
                            <span>Suggestions</span>
                        </div>
                    </div>
                """)
                
                # Prompt Buttons
                prompt_btns = []
                for i, prompt in enumerate(STARTER_PROMPTS):
                    btn = gr.Button(
                        f"✨ {prompt}",
                        variant="secondary",
                        size="sm",
                        elem_classes=["chip"],
                    )
                    prompt_btns.append((btn, prompt))
                
                gr.HTML('<div style="height: 1rem;"></div>')
                
                # About Section
                with gr.Accordion("ℹ️ About This Assistant", open=False):
                    gr.Markdown("""
**URA Chatbot** uses AI to help you with:

- 🏛️ **Tax Registration** - TIN, VAT, business registration
- 💰 **Tax Payments** - Online payments, deadlines, penalties
- 📋 **Compliance** - Filing returns, required documents
- 🚢 **Customs** - Import/export procedures, duties
- 📊 **Tax Rates** - VAT, income tax, withholding tax

---

**How it works:**
1. Your question is analyzed by our AI classifier
2. We match it to the relevant tax category
3. We search our knowledge base for the best answer
4. You get accurate, sourced information
                    """)
                
                # Categories Section
                with gr.Accordion("🏷️ Tax Categories", open=False):
                    gr.HTML("""
                        <div class="category-tags">
                            <span class="category-tag">VAT</span>
                            <span class="category-tag">TIN</span>
                            <span class="category-tag">Customs</span>
                            <span class="category-tag">Income Tax</span>
                            <span class="category-tag">Withholding</span>
                            <span class="category-tag">EFRIS</span>
                            <span class="category-tag">Stamp Duty</span>
                            <span class="category-tag">Rental Tax</span>
                            <span class="category-tag">Employment</span>
                            <span class="category-tag">Business</span>
                        </div>
                    """)
                
                # Design Principles Panel
                gr.HTML("""
                    <div class="info-panel" style="margin-top: 1rem;">
                        <h4 style="margin: 0 0 0.5rem; color: var(--text); font-size: 0.95rem;">
                            ✨ Design principles
                        </h4>
                        <ul style="padding-left: 1.1rem; margin: 0; color: var(--muted); line-height: 1.6; font-size: 0.9rem;">
                            <li>Clear affordances for text input</li>
                            <li>Readable contrast, gentle glassmorphism</li>
                            <li>Context-aware AI responses</li>
                            <li>Modern, fluid interactions</li>
                        </ul>
                    </div>
                """)
        
        # ============================================
        # Footer
        # ============================================
        gr.HTML("""
            <div class="footer">
                <p style="margin: 0 0 0.5rem;">
                    Built with 💙 using <strong>Gradio</strong> • <strong>scikit-learn</strong> • <strong>Sentence Transformers</strong>
                </p>
                <p style="margin: 0;">
                    <a href="https://github.com/mpairweLandwind/FinalYearProject" target="_blank">GitHub Repository</a>
                    &nbsp;•&nbsp;
                    <a href="https://www.ura.go.ug" target="_blank">URA Official Website</a>
                </p>
            </div>
        """)
        
        # ============================================
        # Event Handlers
        # ============================================
        
        def process_message(message, history):
            """Process user message and generate response."""
            if not message.strip():
                return history, ""
            
            response, tag, confidence = generate_response(message)
            
            # Add classification info to response if available
            if tag and confidence:
                response += f"\n\n---\n📌 *Category: {tag}* • *Confidence: {confidence}*"
            
            history.append([message, response])
            return history, ""
        
        def clear_history():
            """Clear chat and reset."""
            return initial_history, ""
        
        # Message submission
        msg_input.submit(
            fn=process_message,
            inputs=[msg_input, chatbot],
            outputs=[chatbot, msg_input],
        )
        
        send_btn.click(
            fn=process_message,
            inputs=[msg_input, chatbot],
            outputs=[chatbot, msg_input],
        )
        
        # Clear button
        clear_btn.click(
            fn=clear_history,
            outputs=[chatbot, msg_input],
        )
        
        # Quick prompt buttons
        for btn, prompt_text in prompt_btns:
            btn.click(
                fn=lambda p=prompt_text: p,
                outputs=msg_input,
            )
    
    return demo


# =============================================================================
# Main Entry Point
# =============================================================================

# Create the demo instance for Hugging Face Spaces
demo = create_interface()

if __name__ == "__main__":
    print("=" * 60)
    print("🇺🇬 URA Chatbot - Enhanced Gradio Application")
    print("=" * 60)
    
    # Initialize models
    success = load_models()
    
    if not success:
        print("\n⚠️ Warning: Models not loaded. Running with limited functionality.")
        print("   For full features, ensure models are trained and available.")
    
    # Launch app
    print("\n🚀 Launching application...")
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )
