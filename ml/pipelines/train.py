"""
Model Training Pipeline
Trains the URA chatbot tag classifier and exports artifacts.

2026 note: this path is the "tag classifier" used by the agentic router.
Full LLM fine-tuning lives in ``ml/scripts/fine_tune_gemma.py``. Both
pipelines log to the same MLflow experiment when ``--report-to mlflow``
is set (see ``_init_mlflow`` below).
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Any, Tuple

import numpy as np
import pandas as pd
import yaml
import joblib

# ML imports
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    classification_report
)

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config(config_path: str) -> Dict[str, Any]:
    """Load training configuration from YAML."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return "unknown"


@contextlib.contextmanager
def _init_mlflow(config: Dict[str, Any], run_name: str):
    """Start an MLflow run if tracking is configured, else yield a no-op.

    Reads ``experiment_tracking.*`` from the YAML config. Failures never
    break training — if mlflow isn't installed or the tracking URI is
    unreachable we yield a dummy object with the same API.
    """
    class _Noop:
        def log_params(self, *a, **k): pass
        def log_metrics(self, *a, **k): pass
        def log_artifact(self, *a, **k): pass
        def set_tag(self, *a, **k): pass

    tracking = config.get("mlflow") or config.get("experiment_tracking") or {}
    # Opt-out: set ``MLFLOW_DISABLED=1`` in CI where no server is wanted.
    if os.environ.get("MLFLOW_DISABLED"):
        yield _Noop()
        return

    try:
        import mlflow  # type: ignore
    except ImportError:
        print("⚠️  mlflow not installed — skipping experiment logging")
        yield _Noop()
        return

    uri = tracking.get("tracking_uri") or os.environ.get("MLFLOW_TRACKING_URI", "mlruns")
    experiment = tracking.get("experiment_name") or "ura-chatbot"
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment)

    class _Active:
        def __init__(self, m): self.m = m
        def log_params(self, d):
            try: self.m.log_params({k: str(v)[:500] for k, v in d.items()})
            except Exception: pass
        def log_metrics(self, d, step=None):
            for k, v in d.items():
                if isinstance(v, (int, float)):
                    try: self.m.log_metric(k, float(v), step=step)
                    except Exception: pass
        def log_artifact(self, path):
            try: self.m.log_artifact(str(path))
            except Exception: pass
        def set_tag(self, k, v):
            try: self.m.set_tag(k, v)
            except Exception: pass

    try:
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("git.commit", _git_sha())
            mlflow.set_tag("pipeline", "classifier")
            yield _Active(mlflow)
    except Exception as exc:
        print(f"⚠️  mlflow: failed to start run ({exc}); continuing without tracking")
        yield _Noop()


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if pd.isna(text):
        return ''
    txt = str(text).replace('\n', ' ').replace('\r', ' ')
    return ' '.join(txt.split())


def load_qa_data(datasets_dir: Path, config: Dict) -> pd.DataFrame:
    """Load and preprocess QA datasets."""
    question_candidates = {'question', 'questions', 'q'}
    answer_candidates = {'answer', 'answers', 'a', 'response', 'resp'}
    
    frames = []
    csv_files = sorted(datasets_dir.glob('*.csv'))
    
    for path in csv_files:
        try:
            df = pd.read_csv(path)
            columns_lower = {c.lower(): c for c in df.columns}
            
            q_col = next(
                (columns_lower[c] for c in columns_lower if c in question_candidates),
                df.columns[0]
            )
            a_col = next(
                (columns_lower[c] for c in columns_lower if c in answer_candidates and columns_lower[c] != q_col),
                df.columns[-1]
            )
            
            df = df[[q_col, a_col]].rename(columns={q_col: 'question', a_col: 'answer'})
            df['question'] = df['question'].apply(clean_text)
            df['answer'] = df['answer'].apply(clean_text)
            df['context'] = df['answer']
            df['tag'] = path.stem.replace('_', ' ')
            df['source'] = path.name
            frames.append(df)
        except Exception as e:
            print(f"Warning: Failed to load {path.name}: {e}")
    
    if not frames:
        return pd.DataFrame(columns=['question', 'answer', 'context', 'tag', 'source'])
    
    qa_df = pd.concat(frames, ignore_index=True)
    
    # Preprocess
    min_q = config['data'].get('min_question_words', 3)
    min_a = config['data'].get('min_answer_words', 3)
    max_words = config['data'].get('max_words', 512)
    
    qa_df['question'] = qa_df['question'].fillna('').str.strip()
    qa_df['answer'] = qa_df['answer'].fillna('').str.strip()
    
    mask = (qa_df['question'] != '') & (qa_df['answer'] != '')
    qa_df = qa_df[mask]
    
    qa_df['q_words'] = qa_df['question'].str.split().apply(len)
    qa_df['a_words'] = qa_df['answer'].str.split().apply(len)
    
    qa_df = qa_df[
        (qa_df['q_words'] >= min_q) & 
        (qa_df['a_words'] >= min_a) &
        (qa_df['q_words'] <= max_words) &
        (qa_df['a_words'] <= max_words)
    ]
    
    qa_df = qa_df.drop_duplicates(subset=['question', 'answer'])
    qa_df = qa_df.drop(columns=['q_words', 'a_words'])
    
    return qa_df.reset_index(drop=True)


def create_embeddings(texts: list, model_name: str) -> np.ndarray:
    """Create embeddings for texts."""
    from langchain_community.embeddings import HuggingFaceEmbeddings
    
    embedder = HuggingFaceEmbeddings(model_name=model_name)
    return np.array(embedder.embed_documents(texts))


def train_classifier(
    X: np.ndarray,
    y: np.ndarray,
    config: Dict
) -> Tuple[Any, Dict[str, float]]:
    """Train classifier with cross-validation."""
    clf_config = config['models']['classifier']
    
    # Cross-validation
    n_splits = min(config['training']['cv_folds'], len(np.unique(y)))
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=config['data']['random_seed'])
    
    cv_metrics = []
    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y), 1):
        clf = SGDClassifier(
            loss=clf_config['loss'],
            penalty=clf_config['penalty'],
            alpha=clf_config['alpha'],
            max_iter=clf_config['max_iter'],
            early_stopping=clf_config['early_stopping'],
            validation_fraction=clf_config['validation_fraction'],
            n_iter_no_change=clf_config['n_iter_no_change'],
            random_state=fold,
        )
        clf.fit(X[train_idx], y[train_idx])
        val_pred = clf.predict(X[val_idx])
        
        cv_metrics.append({
            'fold': fold,
            'accuracy': accuracy_score(y[val_idx], val_pred),
            'f1_macro': f1_score(y[val_idx], val_pred, average='macro', zero_division=0),
            'precision': precision_score(y[val_idx], val_pred, average='macro', zero_division=0),
            'recall': recall_score(y[val_idx], val_pred, average='macro', zero_division=0),
        })
        print(f"  Fold {fold}: Acc={cv_metrics[-1]['accuracy']:.4f}, F1={cv_metrics[-1]['f1_macro']:.4f}")
    
    # Train final model
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=config['data']['test_size'],
        stratify=y,
        random_state=config['data']['random_seed']
    )
    
    final_clf = SGDClassifier(
        loss=clf_config['loss'],
        penalty=clf_config['penalty'],
        alpha=clf_config['alpha'],
        max_iter=clf_config['max_iter'],
        early_stopping=clf_config['early_stopping'],
        validation_fraction=0.1,
        n_iter_no_change=clf_config['n_iter_no_change'],
        random_state=config['data']['random_seed'],
    )
    final_clf.fit(X_train, y_train)
    
    # Test metrics
    y_pred = final_clf.predict(X_test)
    test_metrics = {
        'accuracy': accuracy_score(y_test, y_pred),
        'f1_macro': f1_score(y_test, y_pred, average='macro', zero_division=0),
        'f1_weighted': f1_score(y_test, y_pred, average='weighted', zero_division=0),
        'precision': precision_score(y_test, y_pred, average='macro', zero_division=0),
        'recall': recall_score(y_test, y_pred, average='macro', zero_division=0),
    }
    
    # Combine metrics
    cv_df = pd.DataFrame(cv_metrics)
    all_metrics = {
        'cv_accuracy_mean': cv_df['accuracy'].mean(),
        'cv_accuracy_std': cv_df['accuracy'].std(),
        'cv_f1_mean': cv_df['f1_macro'].mean(),
        'cv_f1_std': cv_df['f1_macro'].std(),
        'test_accuracy': test_metrics['accuracy'],
        'test_f1_macro': test_metrics['f1_macro'],
        'test_f1_weighted': test_metrics['f1_weighted'],
        'test_precision': test_metrics['precision'],
        'test_recall': test_metrics['recall'],
    }
    
    return final_clf, all_metrics


def export_models(
    clf: Any,
    label_encoder: LabelEncoder,
    X_sample: np.ndarray,
    output_dir: Path,
    config: Dict
) -> Dict[str, str]:
    """Export model in multiple formats."""
    import torch
    import torch.nn as nn
    
    exports = {}
    
    # 1. Joblib (sklearn)
    joblib.dump(clf, output_dir / 'tag_classifier.joblib')
    joblib.dump(label_encoder, output_dir / 'label_encoder.joblib')
    exports['joblib'] = str(output_dir / 'tag_classifier.joblib')
    
    # 2. PyTorch
    class TagClassifier(nn.Module):
        def __init__(self, input_dim, num_classes):
            super().__init__()
            self.linear = nn.Linear(input_dim, num_classes)
        
        def forward(self, x):
            return self.linear(x)
    
    input_dim = X_sample.shape[1]
    num_classes = len(label_encoder.classes_)
    
    pytorch_clf = TagClassifier(input_dim, num_classes)
    with torch.no_grad():
        pytorch_clf.linear.weight.copy_(torch.tensor(clf.coef_, dtype=torch.float32))
        pytorch_clf.linear.bias.copy_(torch.tensor(clf.intercept_, dtype=torch.float32))
    
    pytorch_clf.eval()
    
    # Save PyTorch
    torch.save({
        'model_state_dict': pytorch_clf.state_dict(),
        'input_dim': input_dim,
        'num_classes': num_classes,
        'classes': list(label_encoder.classes_),
        'embed_model': config['models']['embedding']['name'],
    }, output_dir / 'tag_classifier.pth')
    exports['pytorch'] = str(output_dir / 'tag_classifier.pth')
    
    # 3. ONNX
    dummy_input = torch.randn(1, input_dim)
    onnx_path = output_dir / 'tag_classifier.onnx'
    torch.onnx.export(
        pytorch_clf,
        dummy_input,
        str(onnx_path),
        export_params=True,
        opset_version=12,
        input_names=['embedding'],
        output_names=['logits'],
        dynamic_axes={'embedding': {0: 'batch_size'}, 'logits': {0: 'batch_size'}}
    )
    exports['onnx'] = str(onnx_path)
    
    # 4. TorchScript
    scripted = torch.jit.script(pytorch_clf)
    ts_path = output_dir / 'tag_classifier_scripted.pt'
    scripted.save(str(ts_path))
    exports['torchscript'] = str(ts_path)
    
    return exports


def main():
    parser = argparse.ArgumentParser(description='Train URA chatbot model')
    parser.add_argument('--config', type=str, required=True, help='Path to config YAML')
    parser.add_argument('--output-dir', type=str, default='artifacts/models', help='Output directory')
    args = parser.parse_args()

    print("=" * 60)
    print("MODEL TRAINING PIPELINE")
    print("=" * 60)

    # Load config
    config = load_config(args.config)

    # Setup directories
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Results directory for metrics and plots
    results_dir = PROJECT_ROOT / config['data'].get('results_dir', 'Results')
    metrics_dir = results_dir / 'metrics'
    plots_dir = results_dir / 'plots'
    metrics_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    datasets_dir = PROJECT_ROOT / config['data']['datasets_dir']

    run_name = f"classifier-{time.strftime('%Y%m%d-%H%M%S')}"
    with _init_mlflow(config, run_name=run_name) as tracker:
        tracker.log_params({
            "datasets_dir": str(datasets_dir),
            "embedding_model": config['models']['embedding']['name'],
            "classifier_type": config['models']['classifier']['type'],
            "random_seed": config['data']['random_seed'],
            "test_size": config['data']['test_size'],
            "cv_folds": config['training']['cv_folds'],
            "git_commit": _git_sha(),
        })

        # Load data
        print("\n[1/5] Loading data...")
        qa_df = load_qa_data(datasets_dir, config)
        print(f"  Loaded {len(qa_df)} QA pairs from {qa_df['source'].nunique()} files")

        if qa_df.empty:
            print("ERROR: No data loaded!")
            sys.exit(1)

        # Create supervised dataset
        print("\n[2/5] Creating embeddings...")
        supervised_df = qa_df[['question', 'context', 'tag']].copy()
        supervised_df['text'] = supervised_df['question'] + ' [SEP] ' + supervised_df['context']

        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(supervised_df['tag'])

        embed_model = config['models']['embedding']['name']
        X = create_embeddings(supervised_df['text'].tolist(), embed_model)
        print(f"  Created embeddings: {X.shape}")

        # Train model
        print("\n[3/5] Training classifier...")
        start_time = time.time()
        clf, metrics = train_classifier(X, y, config)
        train_time = time.time() - start_time

        metrics['training_time_seconds'] = train_time
        metrics['num_samples'] = len(X)
        metrics['num_classes'] = len(label_encoder.classes_)

        print(f"\n  Training completed in {train_time:.2f}s")
        print(f"  Test Accuracy: {metrics['test_accuracy']:.4f}")
        print(f"  Test F1-macro: {metrics['test_f1_macro']:.4f}")

        # Log classifier metrics to MLflow
        tracker.log_metrics({
            k: v for k, v in metrics.items()
            if isinstance(v, (int, float))
        })

        # Export models
        print("\n[4/5] Exporting models...")
        exports = export_models(clf, label_encoder, X, output_dir, config)

        for fmt, path in exports.items():
            size_kb = os.path.getsize(path) / 1024
            print(f"  {fmt}: {path} ({size_kb:.2f} KB)")

        # Save metrics
        print("\n[5/5] Saving metrics...")
        metrics['exports'] = exports
        metrics['config'] = {
            'embedding_model': embed_model,
            'classifier_type': config['models']['classifier']['type'],
        }

        metrics_path = metrics_dir / 'training_metrics.json'
        with open(metrics_path, 'w') as f:
            json.dump(metrics, f, indent=2)
        print(f"  Metrics saved to: {metrics_path}")

        # Save class labels
        labels_path = output_dir / 'class_labels.json'
        with open(labels_path, 'w') as f:
            json.dump(list(label_encoder.classes_), f, indent=2)

        tracker.log_artifact(metrics_path)
        tracker.log_artifact(labels_path)
        for p in exports.values():
            tracker.log_artifact(p)

        print("\n" + "=" * 60)
        print("TRAINING COMPLETE")
        print("=" * 60)
        print(f"Accuracy: {metrics['test_accuracy']:.4f}")
        print(f"F1-Score: {metrics['test_f1_macro']:.4f}")
        print(f"Models exported to: {output_dir}")


if __name__ == "__main__":
    main()
