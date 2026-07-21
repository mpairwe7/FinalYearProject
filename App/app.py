# =============================================================================
# URA Chatbot - Gradio Web Application (Hugging Face Spaces)
#
# UI is a faithful port of the Next.js frontend's 2026 design system
# (frontend/src/app/globals.css):
#   - Official URA palette: Navy #003087, Gold #F9C74F, Teal #00A88F
#   - Deep-dark canvas with an animated blue/teal/gold gradient mesh
#   - Floating glass panels, gold-tinted user bubbles, bubble-less assistant
#   - Grok-style centred hero, segmented locale switch, floating composer
#
# Deploys on HF Spaces free CPU out of the box; ZeroGPU-ready via the
# optional ``spaces`` package (the @GPU decorator attaches a free GPU slice
# per inference call when the Space hardware is set to ZeroGPU).
# =============================================================================

import os
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import gradio as gr
import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Local assets bundled with the Space (logo for the hero + chatbot avatar).
ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "ura-assistant-logo.svg"

# Configuration
MODEL_DIR = PROJECT_ROOT / "Model"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_MODEL_REPO = os.environ.get("HF_MODEL_REPO", "mpairweLandwind/ura-chatbot")

# -----------------------------------------------------------------------------
# Optional Hugging Face ZeroGPU support ("free GPU" on Spaces).
# When the Space hardware is ZeroGPU the ``spaces`` package is present and
# ``@GPU`` attaches an Nvidia slice for the duration of each decorated call.
# Locally / on CPU Spaces the import fails and ``GPU`` is a no-op passthrough,
# so the exact same file runs unchanged everywhere.
# -----------------------------------------------------------------------------
try:
    import spaces  # type: ignore

    GPU = spaces.GPU
    SPACES_SDK = True
except Exception:  # pragma: no cover - only taken off-Spaces / on CPU
    SPACES_SDK = False

    def GPU(*dargs, **dkwargs):  # type: ignore
        """Stand-in for ``spaces.GPU`` when the package is absent.

        Supports both ``@GPU`` and ``@GPU(duration=...)`` usage so call sites
        don't have to know whether ZeroGPU is available.
        """
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def _wrap(fn):
            return fn

        return _wrap


# True only when actually running on ZeroGPU hardware — HF sets this env var
# on ZeroGPU Spaces. The ``spaces`` package merely being importable does NOT
# mean a GPU is attached, so the status badge keys off this instead.
ZEROGPU_ACTIVE = os.environ.get("SPACES_ZERO_GPU", "").lower() in ("true", "1")


# Global model instances
clf = None
encoder = None
embedder = None
qa_knowledge = {}  # Store Q&A pairs for retrieval
qa_embeddings = {}  # Pre-computed embeddings for fast retrieval

# 2026 unification: when the full FastAPI backend is importable, delegate
# generate_response() to ``ChatModel`` — we get the entire 6-phase RAG
# pipeline (hybrid retrieval, guardrails, circuit breakers, self-reflect,
# structured output, etc.) for free.  On HF Spaces, where only the legacy
# classifier is shipped, this import fails and the fallback path runs.
_chat_model = None
_chat_model_available = None  # lazy; None=untried, True/False=resolved


def _load_chat_model():
    """Attempt to load the full FastAPI ChatModel singleton.

    Returns the instance on success, None on failure.  Cached so we
    don't pay the import / model-load cost more than once per process.
    """
    global _chat_model, _chat_model_available
    if _chat_model_available is False:
        return None
    if _chat_model is not None:
        return _chat_model
    try:
        from App.backend.app.service import ChatModel  # type: ignore

        _chat_model = ChatModel()
        _chat_model_available = True
        print(f"✓ Unified ChatModel active ({len(_chat_model._faq_index)} tags)")
        return _chat_model
    except Exception as e:
        _chat_model_available = False
        print(f"ℹ Full ChatModel unavailable; using legacy classifier path ({e})")
        return None


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
    except Exception:
        pass

    # Return first answer as fallback
    return qa_pairs[0]['answer'] if qa_pairs else None


def format_tag_display(tag: str) -> str:
    """Format tag for display."""
    return tag.replace('_', ' ').title()


@GPU(duration=60)
def generate_response(message: str, locale: str = "en") -> Tuple[str, str, str]:
    """Generate response with classification info.

    Prefers the unified :class:`ChatModel` pipeline (hybrid retrieval,
    guardrails, LLM synthesis, self-reflection, circuit breakers)
    when available.  Falls back to the legacy classifier+keyword
    path on HF Spaces deployments.

    Decorated with ``@GPU`` so that, on ZeroGPU hardware, the embedding /
    LLM work runs on a free GPU slice; the decorator is a no-op on CPU.
    """
    if not message.strip():
        return "", "", ""

    # -- Unified path: full ChatModel (2026 production) --------------
    chat_model = _load_chat_model()
    if chat_model is not None:
        try:
            result = chat_model.generate(message=message, top_k=4, locale=locale)
            response = str(result.get("reply", "")) or "I could not find an answer in the URA knowledge base."
            # Use the same classify() call the backend exposes so we keep
            # the "Tag" / "Confidence" badges that the Gradio UI renders.
            try:
                cls = chat_model.classify(message, top_k=1)
                if cls["predictions"]:
                    top = cls["predictions"][0]
                    tag_display = top.get("label") or format_tag_display(top["tag"])
                    confidence_display = f"{top['confidence']*100:.0f}%"
                else:
                    tag_display = "General"
                    confidence_display = "—"
            except Exception:
                tag_display = "General"
                confidence_display = "—"
            return response, tag_display, confidence_display
        except Exception as e:
            print(f"⚠ ChatModel.generate failed ({e}); falling back to legacy path")

    # -- Legacy fallback: classifier + keyword answer ----------------
    result = predict_tag(message)

    if result.get("error"):
        response = "I apologize, but I'm having trouble processing your question. Please try again."
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

- Visiting the URA website at [ura.go.ug](https://www.ura.go.ug)
- Calling the URA helpline on **0800 117 000 / 0800 217 000** (toll-free)
- Visiting your nearest URA office

Is there anything more specific I can help you with?"""

    return response, tag_display, confidence_display


def clear_chat() -> List[Dict[str, str]]:
    """Reset the conversation to the initial greeting."""
    return [{"role": "assistant", "content": GREETING}]


# =============================================================================
# Custom CSS — port of the Next.js frontend design system
# (frontend/src/app/globals.css): URA palette, animated mesh, glass panels.
# =============================================================================

CUSTOM_CSS = """
/* ── URA design tokens (ported 1:1 from globals.css) ── */
:root, .dark {
  --ura-blue:#003087; --ura-blue-light:#1A4A9E; --ura-blue-bright:#4A90D9;
  --ura-gold:#F9C74F; --ura-gold-muted:#D4A843; --ura-teal:#00A88F; --ura-teal-light:#2EC4A8;

  --bg-0:#0A0A12; --bg-1:#0F1019; --bg-2:#1A1A2E;
  --surface-1:rgba(255,255,255,.03); --surface-2:rgba(255,255,255,.05);
  --surface-3:rgba(255,255,255,.08); --surface-4:rgba(255,255,255,.12);
  --surface-ink:rgba(10,10,18,.78);
  --border-0:rgba(255,255,255,.06); --border-1:rgba(255,255,255,.10); --border-2:rgba(255,255,255,.16);
  --text-0:#F8F9FA; --text-1:#E4E5E9; --text-2:#9CA3AF; --text-3:#848D9C;

  --grad-primary:linear-gradient(135deg,#003087 0%,#005BA0 50%,#00A88F 100%);
  --grad-primary-soft:linear-gradient(135deg,rgba(0,48,135,.18) 0%,rgba(0,91,160,.12) 50%,rgba(0,168,143,.10) 100%);
  --grad-border:linear-gradient(135deg,rgba(0,48,135,.50),rgba(0,168,143,.35),rgba(249,199,79,.25));
  --grad-user:linear-gradient(135deg,rgba(249,199,79,.20),rgba(249,199,79,.08));
  --grad-gold:linear-gradient(135deg,#F9C74F 0%,#F0B429 100%);

  --success:#34d399; --warning:#fbbf24; --danger:#fb7185;
  --shadow-sm:0 4px 12px rgba(0,0,0,.35);
  --shadow-md:0 12px 32px rgba(0,0,0,.45),0 0 0 1px rgba(255,255,255,.04);
  --shadow-lg:0 24px 60px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.05);
  --shadow-gold:0 0 20px rgba(249,199,79,.18);
  --radius-xs:6px; --radius-sm:10px; --radius-md:14px; --radius-lg:18px;
  --radius-xl:24px; --radius-2xl:32px; --radius-pill:999px;
  --blur-sm:12px; --blur-md:20px; --blur-lg:28px;

  /* Map Gradio's own theme variables onto the URA palette so built-in
     components (textbox, buttons, chatbot, accordion) inherit the look. */
  --body-background-fill:transparent;
  --background-fill-primary:var(--surface-ink);
  --background-fill-secondary:var(--surface-1);
  --block-background-fill:var(--surface-ink);
  --block-border-color:var(--border-1);
  --block-radius:var(--radius-xl);
  --block-shadow:var(--shadow-md);
  --block-label-background-fill:transparent;
  --block-label-text-color:var(--text-2);
  --block-title-text-color:var(--text-0);
  --border-color-primary:var(--border-1);
  --border-color-accent:rgba(0,48,135,.45);
  --body-text-color:var(--text-1);
  --body-text-color-subdued:var(--text-3);
  --color-accent:var(--ura-blue-bright);
  --color-accent-soft:rgba(0,48,135,.18);
  --link-text-color:var(--ura-blue-bright);
  --link-text-color-hover:var(--ura-teal-light);
  --input-background-fill:var(--surface-1);
  --input-border-color:var(--border-1);
  --input-border-color-focus:rgba(0,48,135,.45);
  --input-radius:var(--radius-2xl);
  --button-large-radius:var(--radius-xl);
  --button-small-radius:var(--radius-sm);
  --button-primary-background-fill:var(--grad-primary);
  --button-primary-background-fill-hover:var(--grad-primary);
  --button-primary-text-color:var(--ura-gold);
  --button-primary-border-color:transparent;
  --button-secondary-background-fill:var(--surface-3);
  --button-secondary-background-fill-hover:var(--surface-4);
  --button-secondary-text-color:var(--text-0);
  --button-secondary-border-color:var(--border-1);
}

/* ── Deep-dark canvas + animated gradient mesh (blue / teal / gold) ── */
body { background:var(--bg-0) !important; overscroll-behavior-y:none; }
body::before {
  content:""; position:fixed; inset:0; z-index:-2;
  background:
    radial-gradient(ellipse 60% 50% at 20% 10%, rgba(0,48,135,.22), transparent 60%),
    radial-gradient(ellipse 50% 45% at 80% 30%, rgba(0,168,143,.16), transparent 55%),
    radial-gradient(ellipse 55% 40% at 50% 95%, rgba(249,199,79,.06), transparent 60%),
    linear-gradient(180deg, var(--bg-0) 0%, var(--bg-1) 50%, var(--bg-0) 100%);
  animation:mesh-drift 32s ease-in-out infinite alternate;
}
@keyframes mesh-drift {
  0%   { transform:translate3d(0,0,0) scale(1); }
  50%  { transform:translate3d(-2%,1%,0) scale(1.04); }
  100% { transform:translate3d(1%,-1%,0) scale(1.02); }
}
.gradio-container {
  background:transparent !important;
  max-width:960px !important;
  margin:0 auto !important;
  padding:0 clamp(.75rem,2vw,1.25rem) 2rem !important;
  font-family:"Aptos","Avenir Next","Trebuchet MS",Verdana,system-ui,sans-serif !important;
}
footer { display:none !important; }

/* ── Top bar (brand + locale + voice pill) ── */
.ura-topbar {
  display:flex; justify-content:space-between; align-items:center; gap:.75rem;
  padding:.7rem .25rem; margin-bottom:.5rem;
  border-bottom:1px solid var(--border-0); flex-wrap:wrap;
}
.ura-brand { display:flex; align-items:center; gap:.55rem; min-width:0; }
.ura-brand .logo { width:30px; height:30px; border-radius:var(--radius-xs); flex-shrink:0; }
.ura-brand .title { font-size:.98rem; font-weight:600; color:var(--text-0); white-space:nowrap; }
.ura-topbar-right { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.ura-pill {
  display:inline-flex; align-items:center; gap:.4rem; padding:.4rem .7rem;
  border-radius:var(--radius-pill); background:var(--surface-1);
  border:1px solid var(--border-0); color:var(--text-2);
  font-size:.74rem; font-weight:500; white-space:nowrap;
}
.ura-pill svg { width:14px; height:14px; }
.ura-pill.ok { background:rgba(52,211,153,.12); border-color:rgba(52,211,153,.35); color:var(--success); }

/* ── Centred hero (Grok-style dominant branding) ── */
.ura-hero { text-align:center; padding:1.6rem 1rem .4rem; animation:fade-in-up 640ms cubic-bezier(.22,1,.36,1) both; }
.ura-hero-logo {
  width:clamp(92px,16vw,118px); height:clamp(92px,16vw,118px); margin:0 auto .9rem; display:block;
  filter:drop-shadow(0 20px 50px rgba(0,48,135,.35));
}
.ura-hero-title { font-size:clamp(1.7rem,4vw,2.5rem); font-weight:700; margin:0 0 .35rem; color:var(--text-0); }
.ura-hero-sub { color:var(--text-2); font-size:.96rem; line-height:1.55; max-width:46ch; margin:.2rem auto 0; }
.ura-badge {
  display:inline-flex; align-items:center; gap:.45rem; padding:.45rem .85rem; margin-bottom:1rem;
  border-radius:var(--radius-pill); background:var(--grad-primary-soft);
  border:1px solid rgba(0,48,135,.3); color:var(--ura-gold);
  font-size:.74rem; font-weight:600; letter-spacing:.03em; text-transform:uppercase;
  box-shadow:0 0 20px rgba(0,48,135,.22), inset 0 1px 0 rgba(255,255,255,.08);
}
.ura-badge svg { width:15px; height:15px; }

/* ── Chatbot: gold user bubble (with tail) + bubble-less assistant ── */
.chatbot, .chatbot .wrap, .chatbot .panel-wrap, .chatbot .bubble-wrap { background:transparent !important; border:none !important; }
.chatbot { box-shadow:none !important; }
.chatbot .message-row, .chatbot .message-wrap { margin:0 0 1rem !important; padding:0 !important; }
.chatbot .message {
  border-radius:var(--radius-lg) !important; padding:.9rem 1.1rem !important;
  font-size:.95rem !important; line-height:1.7 !important;
  animation:msg-enter .42s cubic-bezier(.22,1,.36,1) both;
}
/* User — gold-tinted glass with an asymmetric tail toward the avatar */
.chatbot .message.user, .chatbot .user .message, .chatbot [data-testid="user"] {
  background:var(--grad-user) !important;
  border:1px solid rgba(249,199,79,.22) !important;
  color:var(--text-0) !important;
  box-shadow:var(--shadow-sm) !important;
  border-bottom-right-radius:var(--radius-xs) !important;
}
/* Assistant — full-width text, no box (2026 chat convention) */
.chatbot .message.bot, .chatbot .bot .message, .chatbot [data-testid="bot"] {
  background:transparent !important; border:none !important; box-shadow:none !important;
  color:var(--text-1) !important; padding:.1rem .1rem !important;
}
.chatbot .avatar-container { background:rgba(0,48,135,.22) !important; border:1px solid rgba(0,48,135,.35) !important; box-shadow:var(--shadow-sm) !important; }
/* Markdown inside assistant replies → URA palette */
.chatbot .message h1, .chatbot .message h2 { color:var(--text-0) !important; font-size:1.15rem !important; font-weight:700 !important; }
.chatbot .message h3, .chatbot .message h4 { color:var(--text-0) !important; }
.chatbot .message a { color:var(--ura-blue-bright) !important; text-decoration:underline; text-underline-offset:2px; }
.chatbot .message a:hover { color:var(--ura-teal-light) !important; }
.chatbot .message strong { color:var(--text-0) !important; }
.chatbot .message ul li::marker, .chatbot .message ol li::marker { color:var(--ura-gold-muted) !important; }
.chatbot .message code:not(pre code) {
  background:rgba(0,48,135,.15) !important; border:1px solid rgba(0,48,135,.2) !important;
  color:var(--ura-blue-bright) !important; border-radius:var(--radius-xs) !important; padding:.15em .4em !important;
}
.chatbot .message pre { background:rgba(0,0,0,.35) !important; border:1px solid var(--border-0) !important; border-radius:var(--radius-sm) !important; }
.chatbot .message hr { border:none !important; border-top:1px solid var(--border-1) !important; }
.chatbot .message blockquote { border-left:3px solid var(--ura-gold-muted) !important; background:rgba(249,199,79,.04) !important; color:var(--text-2) !important; }
.chatbot .message table th { background:rgba(0,48,135,.14) !important; color:var(--text-0) !important; }
.chatbot .message table td, .chatbot .message table th { border-color:var(--border-0) !important; }
@keyframes msg-enter { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }

/* ── Starter chips (gr.Button.ura-chip) — landing-chip look ── */
.ura-chip button, button.ura-chip {
  width:100% !important; justify-content:flex-start !important; text-align:left !important;
  gap:.5rem !important; padding:.8rem 1rem !important; min-height:48px !important;
  background:var(--surface-1) !important; border:1px solid var(--border-0) !important;
  border-radius:var(--radius-md) !important; color:var(--text-2) !important;
  font-size:.86rem !important; font-weight:500 !important; box-shadow:none !important;
  transition:all 200ms cubic-bezier(.22,1,.36,1) !important;
}
.ura-chip button:hover, button.ura-chip:hover {
  background:var(--surface-2) !important; border-color:rgba(249,199,79,.25) !important;
  color:var(--text-0) !important; transform:translateY(-1px) !important; box-shadow:var(--shadow-gold) !important;
}
.ura-prompts-label { color:var(--text-3); font-size:.74rem; font-weight:600; text-transform:uppercase; letter-spacing:.06em; margin:.5rem .25rem .1rem; }

/* ── Floating composer ── */
.ura-composer { gap:.5rem !important; align-items:stretch !important; }
.ura-composer textarea, .ura-composer input[type="text"] {
  background:var(--surface-ink) !important; border:1px solid var(--border-1) !important;
  border-radius:var(--radius-2xl) !important; color:var(--text-0) !important;
  font-size:1rem !important; padding:.9rem 1.1rem !important; box-shadow:var(--shadow-md) !important;
  backdrop-filter:blur(var(--blur-lg)); -webkit-backdrop-filter:blur(var(--blur-lg));
}
.ura-composer textarea:focus, .ura-composer input[type="text"]:focus {
  border-color:rgba(0,48,135,.45) !important; box-shadow:var(--shadow-md), 0 0 0 3px rgba(0,48,135,.15) !important;
}
.ura-composer textarea::placeholder { color:var(--text-3) !important; }
.ura-send button {
  background:var(--grad-primary) !important; color:var(--ura-gold) !important; border:none !important;
  border-radius:var(--radius-xl) !important; font-weight:600 !important; min-height:48px !important;
  box-shadow:0 8px 24px rgba(0,48,135,.35), inset 0 1px 0 rgba(255,255,255,.15) !important;
  transition:transform 150ms ease, box-shadow 180ms ease !important;
}
.ura-send button:hover { transform:translateY(-1px) !important; box-shadow:0 12px 30px rgba(0,48,135,.5), inset 0 1px 0 rgba(255,255,255,.2) !important; }
.ura-clear button {
  background:var(--surface-3) !important; color:var(--text-0) !important;
  border:1px solid var(--border-1) !important; border-radius:var(--radius-xl) !important; min-height:48px !important;
}
.ura-clear button:hover { background:var(--surface-4) !important; border-color:var(--border-2) !important; }
.ura-hint { color:var(--text-3); font-size:.78rem; text-align:center; margin:.6rem 0 0; }
.ura-hint kbd { background:var(--surface-3); border:1px solid var(--border-1); border-radius:4px; padding:.12rem .4rem; font-size:.74rem; }

/* ── Locale segmented switch (gr.Radio) ── */
.ura-locale { background:transparent !important; border:none !important; box-shadow:none !important; padding:0 !important; }
.ura-locale .wrap, .ura-locale fieldset {
  display:inline-flex !important; gap:.2rem !important; padding:.25rem !important;
  background:var(--surface-1) !important; border:1px solid var(--border-1) !important;
  border-radius:var(--radius-pill) !important;
}
.ura-locale label {
  border:none !important; background:transparent !important; color:var(--text-2) !important;
  border-radius:var(--radius-pill) !important; padding:.35rem .9rem !important;
  font-size:.8rem !important; font-weight:500 !important; min-height:auto !important;
}
.ura-locale label.selected, .ura-locale input:checked + label {
  background:var(--grad-primary) !important; color:var(--ura-gold) !important; font-weight:600 !important;
  box-shadow:0 4px 14px rgba(0,48,135,.4) !important;
}
.ura-locale input[type="radio"] { display:none !important; }

/* ── About / Categories accordion ── */
.ura-about { background:var(--surface-ink) !important; border:1px solid var(--border-1) !important; border-radius:var(--radius-xl) !important; }
.ura-cats { display:flex; flex-wrap:wrap; gap:.4rem; }
.ura-cat {
  display:inline-flex; align-items:center; padding:.32rem .7rem; font-size:.78rem; color:var(--ura-gold);
  background:rgba(249,199,79,.1); border:1px solid rgba(249,199,79,.22); border-radius:var(--radius-pill);
}

/* ── Footer ── */
.ura-footer { text-align:center; padding:1.75rem 1rem .5rem; color:var(--text-3); font-size:.84rem; }
.ura-footer a { color:var(--ura-blue-bright); text-decoration:none; }
.ura-footer a:hover { color:var(--ura-teal-light); text-decoration:underline; }

/* ── Scrollbars (URA-tinted) ── */
::-webkit-scrollbar { width:8px; height:8px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:linear-gradient(180deg,rgba(0,48,135,.35),rgba(0,168,143,.25)); border-radius:var(--radius-pill); }
* { scrollbar-width:thin; scrollbar-color:rgba(0,48,135,.35) transparent; }

@keyframes fade-in-up { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }

/* ── Responsive ── */
@media (max-width:760px) {
  .ura-topbar { gap:.5rem; }
  .ura-hero { padding-top:1rem; }
}
"""

# =============================================================================
# Inline SVG icons + brand mark (ported from frontend/src/components/Icons.tsx)
# =============================================================================

ICONS = {
    "sparkles": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3"/><path d="M12 18v3"/><path d="m5.64 5.64 2.12 2.12"/><path d="m16.24 16.24 2.12 2.12"/><path d="M3 12h3"/><path d="M18 12h3"/><path d="m5.64 18.36 2.12-2.12"/><path d="m16.24 7.76 2.12-2.12"/></svg>',
    "headphones": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>',
}

# Compact inline brand mark (URA navy shield + gold AI bubble + Uganda strip).
LOGO_SVG = '''<svg viewBox="0 0 512 512" width="112" height="112" class="ura-hero-logo" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="URA Tax Assistant">
<defs>
<linearGradient id="hs" x1="0" y1="0" x2="0.5" y2="1"><stop offset="0%" stop-color="#003087"/><stop offset="100%" stop-color="#001A4D"/></linearGradient>
<linearGradient id="hb" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#F9C74F"/><stop offset="100%" stop-color="#D4A843"/></linearGradient>
<linearGradient id="ht" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00A88F"/><stop offset="100%" stop-color="#2EC4A8"/></linearGradient>
</defs>
<rect x="16" y="16" width="480" height="480" rx="96" fill="url(#hs)"/>
<rect x="24" y="24" width="464" height="464" rx="88" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="2"/>
<path d="M 140 150 C 140 125 160 108 185 108 L 327 108 C 352 108 372 125 372 150 L 372 255 C 372 280 352 297 327 297 L 220 297 L 175 340 L 185 297 C 160 297 140 280 140 255 Z" fill="url(#hb)" opacity="0.95"/>
<circle cx="210" cy="195" r="16" fill="#003087" opacity="0.8"/><circle cx="256" cy="195" r="16" fill="#003087" opacity="0.8"/><circle cx="302" cy="195" r="16" fill="#003087" opacity="0.8"/>
<circle cx="210" cy="195" r="10" fill="white" opacity="0.9"/><circle cx="256" cy="195" r="10" fill="white" opacity="0.7"/><circle cx="302" cy="195" r="10" fill="white" opacity="0.5"/>
<rect x="140" y="370" width="232" height="6" rx="3" fill="url(#ht)" opacity="0.9"/>
<rect x="330" y="340" width="56" height="32" rx="16" fill="url(#ht)"/>
<text x="358" y="361" font-family="system-ui,sans-serif" font-size="18" font-weight="700" fill="white" text-anchor="middle">AI</text>
<rect x="140" y="392" width="58" height="4" rx="2" fill="#1A1A1A"/><rect x="198" y="392" width="58" height="4" rx="2" fill="#F9C74F"/><rect x="256" y="392" width="58" height="4" rx="2" fill="#CE1126"/><rect x="314" y="392" width="58" height="4" rx="2" fill="#1A1A1A"/>
</svg>'''

# Small brand mark for the top bar (reuses the same crest, smaller).
LOGO_SVG_SM = LOGO_SVG.replace('width="112" height="112" class="ura-hero-logo"', 'width="30" height="30" class="logo"')

# Starter prompts — identical copy to the React landing (page.tsx).
STARTER_PROMPTS = [
    "What services does URA provide?",
    "How do I register for a TIN?",
    "What is the current VAT rate in Uganda?",
    "How do I file my annual tax returns?",
]

LOCALE_LABELS = {"English": "en", "Luganda": "lg"}

GREETING = (
    "👋 **Hi! I'm the URA Tax Assistant.**\n\n"
    "I can help with Uganda Revenue Authority services, tax policy, and procedures — "
    "TIN registration, VAT, customs, filing returns and more.\n\n"
    "Ask a question below, or tap one of the suggestions to begin."
)


# =============================================================================
# Gradio interface — mirrors the React layout (top bar → hero → chat → chips
# → floating composer → about → footer)
# =============================================================================

def create_interface():
    """Create the Gradio interface in the URA frontend's design language."""

    initial_history = [{"role": "assistant", "content": GREETING}]

    theme = gr.themes.Base(
        primary_hue=gr.themes.colors.blue,
        secondary_hue=gr.themes.colors.teal,
        neutral_hue=gr.themes.colors.slate,
        # Offline-safe local font stack (matches the frontend; no remote fetch).
        font=["Aptos", "Avenir Next", "Trebuchet MS", "Verdana", "system-ui", "sans-serif"],
        radius_size=gr.themes.sizes.radius_lg,
    )

    # Force the deep-dark URA canvas (the frontend's default is dark; light is
    # opt-in). Redirects once to ?__theme=dark so Gradio's own components match.
    force_dark_js = """
    () => {
      const url = new URL(window.location.href);
      if (url.searchParams.get('__theme') !== 'dark') {
        url.searchParams.set('__theme', 'dark');
        window.location.replace(url.href);
      }
    }
    """

    with gr.Blocks(
        title="URA Tax Assistant — Uganda Revenue Authority",
        theme=theme,
        css=CUSTOM_CSS,
        js=force_dark_js,
        fill_height=False,
    ) as demo:

        locale_state = gr.State("en")

        # ── Top bar: brand · locale switch · voice-ready pill ──
        gr.HTML(f"""
            <div class="ura-topbar">
              <div class="ura-brand">{LOGO_SVG_SM}<span class="title">URA Tax Assistant</span></div>
              <div class="ura-topbar-right">
                <span class="ura-pill ok">{ICONS['headphones']}<span>{'GPU ready' if ZEROGPU_ACTIVE else 'Online'}</span></span>
              </div>
            </div>
        """)

        # ── Centred hero ──
        gr.HTML(f"""
            <div class="ura-hero">
              {LOGO_SVG}
              <div><span class="ura-badge">{ICONS['sparkles']}<span>Official AI Assistant</span></span></div>
              <h1 class="ura-hero-title">URA Tax Assistant</h1>
              <p class="ura-hero-sub">Official AI-powered assistant for Uganda Revenue Authority — grounded answers on services, tax policy, and procedures.</p>
            </div>
        """)

        # Locale segmented switch (styled like the frontend's .locale-switch)
        with gr.Row():
            locale_radio = gr.Radio(
                choices=list(LOCALE_LABELS.keys()),
                value="English",
                show_label=False,
                container=False,
                elem_classes=["ura-locale"],
            )

        # ── Conversation ──
        chatbot = gr.Chatbot(
            value=initial_history,
            type="messages",
            height=460,
            show_label=False,
            container=False,
            elem_classes=["chatbot"],
            avatar_images=(None, str(LOGO_PATH) if LOGO_PATH.exists() else None),
            show_copy_button=True,
            render_markdown=True,
        )

        # ── Starter chips (2-col grid, identical copy to React) ──
        gr.HTML('<p class="ura-prompts-label">Suggested questions</p>')
        prompt_btns = []
        with gr.Row():
            for prompt in STARTER_PROMPTS[:2]:
                prompt_btns.append((gr.Button(prompt, elem_classes=["ura-chip"]), prompt))
        with gr.Row():
            for prompt in STARTER_PROMPTS[2:]:
                prompt_btns.append((gr.Button(prompt, elem_classes=["ura-chip"]), prompt))

        # ── Floating composer ──
        with gr.Row(elem_classes=["ura-composer"]):
            msg_input = gr.Textbox(
                placeholder="Ask anything about URA…",
                show_label=False,
                container=False,
                scale=8,
                lines=1,
                max_lines=4,
                autofocus=True,
            )
            clear_btn = gr.Button("Clear", scale=1, min_width=72, elem_classes=["ura-clear"])
            send_btn = gr.Button("Send", variant="primary", scale=1, min_width=88, elem_classes=["ura-send"])

        gr.HTML('<p class="ura-hint">Press <kbd>Enter</kbd> to send · answers are AI-generated; verify on <a href="https://www.ura.go.ug" target="_blank" rel="noopener">ura.go.ug</a></p>')

        # ── About / Categories ──
        with gr.Accordion("About this assistant", open=False, elem_classes=["ura-about"]):
            gr.Markdown(
                "The **URA Tax Assistant** helps you navigate Uganda Revenue Authority "
                "services using AI. It classifies your question, retrieves the most "
                "relevant answer from official URA FAQs, and responds with sourced "
                "information.\n\n**Coverage:**"
            )
            gr.HTML("""
                <div class="ura-cats">
                  <span class="ura-cat">VAT</span><span class="ura-cat">TIN</span>
                  <span class="ura-cat">Customs</span><span class="ura-cat">Income Tax</span>
                  <span class="ura-cat">Withholding</span><span class="ura-cat">EFRIS</span>
                  <span class="ura-cat">Stamp Duty</span><span class="ura-cat">Rental Tax</span>
                  <span class="ura-cat">Employment</span><span class="ura-cat">Business Reg.</span>
                </div>
            """)

        # ── Footer ──
        gr.HTML("""
            <div class="ura-footer">
              <p style="margin:0 0 .4rem;">Uganda Revenue Authority · AI Tax Assistant</p>
              <p style="margin:0;">
                <a href="https://github.com/mpairweLandwind/FinalYearProject" target="_blank" rel="noopener">GitHub</a>
                &nbsp;·&nbsp;
                <a href="https://www.ura.go.ug" target="_blank" rel="noopener">URA Official Website</a>
              </p>
            </div>
        """)

        # ── Handlers ──
        def set_locale(label):
            return LOCALE_LABELS.get(label, "en")

        def respond(message, history, locale):
            """Append the user turn + assistant reply (messages format)."""
            if not message or not message.strip():
                return history, ""
            response, tag, confidence = generate_response(message, locale)
            if tag and confidence and confidence not in ("—", "", "0%"):
                response += f"\n\n---\n📌 *Category: {tag} · Confidence: {confidence}*"
            history = (history or []) + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response},
            ]
            return history, ""

        def send_starter(prompt, history, locale):
            return respond(prompt, history, locale)

        locale_radio.change(set_locale, inputs=locale_radio, outputs=locale_state)

        msg_input.submit(respond, [msg_input, chatbot, locale_state], [chatbot, msg_input])
        send_btn.click(respond, [msg_input, chatbot, locale_state], [chatbot, msg_input])
        clear_btn.click(clear_chat, outputs=chatbot).then(lambda: "", outputs=msg_input)

        for btn, prompt_text in prompt_btns:
            btn.click(
                lambda history, locale, p=prompt_text: send_starter(p, history, locale),
                inputs=[chatbot, locale_state],
                outputs=[chatbot, msg_input],
            )

    return demo


# =============================================================================
# Main entry point
# =============================================================================

demo = create_interface()

if __name__ == "__main__":
    print("=" * 60)
    print("🇺🇬 URA Tax Assistant — Gradio Application")
    print(f"   spaces SDK: {SPACES_SDK} | ZeroGPU active: {ZEROGPU_ACTIVE}")
    print("=" * 60)

    # Initialize models (best-effort; UI still loads on failure)
    success = load_models()
    if not success:
        print("\n⚠️ Models not loaded — running with limited functionality.")
        print("   Ensure Model/ files exist or HF_MODEL_REPO is reachable.")

    print("\n🚀 Launching application...")
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("SERVER_PORT", 7860)),
        share=os.environ.get("SHARE", "false").lower() == "true",
        show_error=True,
        allowed_paths=[str(ASSETS_DIR)],
    )
