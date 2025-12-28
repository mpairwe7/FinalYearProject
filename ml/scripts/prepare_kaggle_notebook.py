"""
Kaggle Notebook Preparation Script
Prepares and uploads notebook to Kaggle for remote training
"""

import argparse
import json
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def prepare_notebook(notebook_name: str, enable_gpu: bool = True, training_data: str = None) -> None:
    """Prepare Kaggle kernel metadata and files."""
    
    kaggle_dir = PROJECT_ROOT / 'ml' / 'kaggle'
    kaggle_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy training data if provided
    if training_data:
        training_data_path = Path(training_data)
        if training_data_path.exists():
            target_training_data = kaggle_dir / 'training_data.jsonl'
            shutil.copy(training_data_path, target_training_data)
            print(f"✓ Copied training data to {target_training_data}")
    
    # Define notebook mappings
    notebook_paths = {
        'ura-training': PROJECT_ROOT / 'Notebooks' / 'ura-training.ipynb',
        'embedding-fine-tune': PROJECT_ROOT / 'Notebooks' / 'embedding-fine-tune.ipynb',
        'full-pipeline': PROJECT_ROOT / 'Notebooks' / 'ura-training.ipynb',
    }
    
    source_notebook = notebook_paths.get(notebook_name)
    if not source_notebook or not source_notebook.exists():
        # Use default
        source_notebook = PROJECT_ROOT / 'Notebooks' / 'ura-training.ipynb'
    
    # Copy notebook
    target_notebook = kaggle_dir / f'{notebook_name}.ipynb'
    shutil.copy(source_notebook, target_notebook)
    
    # Create kernel metadata
    kaggle_username = os.environ.get('KAGGLE_USERNAME', 'your-username')
    
    kernel_metadata = {
        "id": f"{kaggle_username}/{notebook_name}",
        "title": notebook_name.replace('-', ' ').title(),
        "code_file": f"{notebook_name}.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": enable_gpu,
        "enable_tpu": False,
        "enable_internet": True,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": []
    }
    
    metadata_path = kaggle_dir / 'kernel-metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(kernel_metadata, f, indent=2)
    
    print(f"✓ Prepared Kaggle kernel: {notebook_name}")
    print(f"  Notebook: {target_notebook}")
    print(f"  Metadata: {metadata_path}")
    print(f"  GPU enabled: {enable_gpu}")


def main():
    parser = argparse.ArgumentParser(description='Prepare Kaggle notebook')
    parser.add_argument('--notebook', type=str, default='ura-training')
    parser.add_argument('--gpu', type=str, default='true')
    parser.add_argument('--training-data', type=str, default=None,
                        help='Path to training data JSONL file')
    args = parser.parse_args()
    
    enable_gpu = args.gpu.lower() == 'true'
    prepare_notebook(args.notebook, enable_gpu, args.training_data)


if __name__ == "__main__":
    main()
