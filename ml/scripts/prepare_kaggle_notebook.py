"""
Kaggle Notebook Preparation Script
Prepares and uploads notebook to Kaggle for remote training
"""

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Optional, List

PROJECT_ROOT = Path(__file__).parent.parent.parent


def prepare_notebook(
    notebook_name: str, 
    enable_gpu: bool = True, 
    data_sources: Optional[List[str]] = None, 
    dataset_sources: Optional[List[str]] = None
) -> None:
    """Prepare Kaggle kernel metadata and files."""
    
    kaggle_dir = PROJECT_ROOT / 'ml' / 'kaggle'
    # Clean up previous runs
    if kaggle_dir.exists():
        shutil.rmtree(kaggle_dir)
    kaggle_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy data sources if provided
    if data_sources:
        for source in data_sources:
            source_path = Path(source)
            if not source_path.is_absolute():
                source_path = PROJECT_ROOT / source_path
                
            if source_path.exists():
                if source_path.is_file():
                    # If it's a file in artifacts, put it in the root of kaggle_dir
                    if 'artifacts' in source_path.parts:
                        dest_path = kaggle_dir / source_path.name
                    else:
                        # Otherwise try to preserve some structure or just put in root
                        dest_path = kaggle_dir / source_path.name
                    
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(source_path, dest_path)
                    print(f"✓ Copied file {source_path.name} to {dest_path}")
                elif source_path.is_dir():
                    # For directories, we want to preserve the folder name
                    dest_dir = kaggle_dir / 'Data' / source_path.name
                    shutil.copytree(source_path, dest_dir, dirs_exist_ok=True)
                    print(f"✓ Copied directory {source_path.name} to {dest_dir}")
            else:
                print(f"⚠ Warning: Data source {source} not found")

    # Define notebook mappings
    # If notebook_name is a path, use it directly
    if notebook_name.endswith('.ipynb'):
        source_notebook = Path(notebook_name)
        if not source_notebook.is_absolute():
            source_notebook = PROJECT_ROOT / source_notebook
        notebook_slug = source_notebook.stem
    else:
        notebook_paths = {
            'ura-training': PROJECT_ROOT / 'Notebooks' / 'ura-training.ipynb',
            'embedding-fine-tune': PROJECT_ROOT / 'Notebooks' / 'embedding-fine-tune.ipynb',
        }
        source_notebook = notebook_paths.get(notebook_name, PROJECT_ROOT / 'Notebooks' / 'ura-training.ipynb')
        notebook_slug = notebook_name

    if not source_notebook.exists():
        print(f"❌ Error: Notebook {source_notebook} not found")
        return
    
    # Copy notebook
    target_notebook = kaggle_dir / f'{notebook_slug}.ipynb'
    shutil.copy(source_notebook, target_notebook)
    
    # Create kernel metadata
    kaggle_username = os.environ.get('KAGGLE_USERNAME', 'your-username')
    import datetime
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    
    kernel_metadata = {
        "id": f"{kaggle_username}/{notebook_slug}-{timestamp}",
        "title": f"{notebook_slug.replace('-', ' ').title()} {timestamp}",
        "code_file": f"{notebook_slug}.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": enable_gpu,
        "accelerator": "nvidiaTeslaT4" if enable_gpu else "none",
        "enable_tpu": False,
        "enable_internet": True,
        "dataset_sources": dataset_sources or [],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": []
    }
    
    metadata_path = kaggle_dir / 'kernel-metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(kernel_metadata, f, indent=2)
    
    print(f"✓ Prepared Kaggle kernel: {notebook_slug}")


def main():
    parser = argparse.ArgumentParser(description='Prepare Kaggle notebook')
    parser.add_argument('--notebook', type=str, default='ura-training')
    parser.add_argument('--gpu', type=str, default='true')
    parser.add_argument('--data', action='append', help='Data files or directories to include')
    parser.add_argument('--dataset', action='append', help='Kaggle dataset IDs to include')
    args = parser.parse_args()
    
    enable_gpu = args.gpu.lower() == 'true'
    prepare_notebook(args.notebook, enable_gpu, args.data, args.dataset)


if __name__ == "__main__":
    main()
