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

    # Recursively copy all files from Data/dataset, Data/TTT, Data/lgaudio, Data/pdfs into ml/kaggle
    data_folders = [
        PROJECT_ROOT / 'Data' / 'dataset',
        PROJECT_ROOT / 'Data' / 'TTT',
        PROJECT_ROOT / 'Data' / 'lgaudio',
        PROJECT_ROOT / 'Data' / 'pdfs',
    ]
    for folder in data_folders:
        if folder.exists():
            for file in folder.rglob('*'):
                if file.is_file():
                    # Preserve subfolder structure inside ml/kaggle
                    rel_path = Path('Data') / file.relative_to(PROJECT_ROOT / 'Data')
                    dest_path = kaggle_dir / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(file, dest_path)
                    print(f"✓ Copied {file} to {dest_path}")
    
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
    import datetime
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    # Allow attaching a Kaggle dataset created via CI by reading the slug from env
    dataset_slug = os.environ.get('KAGGLE_DATASET_SLUG')
    dataset_sources = [dataset_slug] if dataset_slug else []

    kernel_metadata = {
        "id": f"{kaggle_username}/{notebook_name}-{timestamp}",
        "title": f"{notebook_name.replace('-', ' ').title()} {timestamp}",
        "code_file": f"{notebook_name}.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": enable_gpu,
        "accelerator": "nvidiaTeslaT4" if enable_gpu else "none",  # T4 x2 (was P100)
        "enable_tpu": False,
        "enable_internet": True,
        "dataset_sources": dataset_sources,
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
