# ML Module Initialization
"""
URA Chatbot ML Pipeline
=======================

This module contains the machine learning pipelines for:
- Data validation
- Model training
- Model evaluation
- Model export and deployment
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ML_ROOT = Path(__file__).parent

__version__ = "0.1.0"
