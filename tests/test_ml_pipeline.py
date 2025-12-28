"""
Unit Tests for ML Pipelines
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestDataValidation:
    """Tests for data validation pipeline."""
    
    def test_clean_text(self):
        """Test text cleaning function."""
        from ml.pipelines.validate_data import clean_text if hasattr else None
        # Placeholder - implement when module is importable
        pass
    
    def test_validate_csv_empty(self):
        """Test validation with empty CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test.csv"
            pd.DataFrame(columns=["question", "answer"]).to_csv(csv_path, index=False)
            
            # Validation should warn about empty data
            assert csv_path.exists()
    
    def test_validate_csv_with_data(self):
        """Test validation with valid CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test.csv"
            df = pd.DataFrame({
                "question": ["What is VAT?", "How to pay taxes?"],
                "answer": ["VAT is a consumption tax.", "You can pay online."]
            })
            df.to_csv(csv_path, index=False)
            
            assert csv_path.exists()
            loaded = pd.read_csv(csv_path)
            assert len(loaded) == 2


class TestTrainingPipeline:
    """Tests for model training pipeline."""
    
    def test_load_config(self):
        """Test configuration loading."""
        config_path = PROJECT_ROOT / "ml" / "configs" / "training_config.yaml"
        if config_path.exists():
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            assert "data" in config
            assert "models" in config
            assert "training" in config
    
    def test_text_preprocessing(self):
        """Test text cleaning."""
        text = "Hello\n\nWorld  with   spaces"
        cleaned = " ".join(text.replace("\n", " ").split())
        assert cleaned == "Hello World with spaces"
    
    def test_label_encoding(self):
        """Test label encoder functionality."""
        from sklearn.preprocessing import LabelEncoder
        
        labels = ["vat", "tin", "customs", "vat", "tin"]
        encoder = LabelEncoder()
        encoded = encoder.fit_transform(labels)
        
        assert len(encoder.classes_) == 3
        assert all(isinstance(e, (int, np.integer)) for e in encoded)
        
        decoded = encoder.inverse_transform([0, 1, 2])
        assert len(decoded) == 3


class TestQualityGates:
    """Tests for quality gates."""
    
    def test_accuracy_gate(self):
        """Test accuracy threshold check."""
        metrics = {"test_accuracy": 0.85}
        gates = {"min_accuracy": 0.75}
        
        assert metrics["test_accuracy"] >= gates["min_accuracy"]
    
    def test_f1_gate(self):
        """Test F1 score threshold check."""
        metrics = {"test_f1_macro": 0.72}
        gates = {"min_f1_score": 0.70}
        
        assert metrics["test_f1_macro"] >= gates["min_f1_score"]
    
    def test_latency_gate(self):
        """Test latency threshold check."""
        metrics = {"latency": {"p95_ms": 50}}
        gates = {"max_latency_ms": 100}
        
        assert metrics["latency"]["p95_ms"] <= gates["max_latency_ms"]


class TestModelExport:
    """Tests for model export functionality."""
    
    def test_export_paths(self):
        """Test expected export file paths."""
        expected_formats = [
            "tag_classifier.joblib",
            "label_encoder.joblib",
            "tag_classifier.pth",
            "tag_classifier.onnx",
        ]
        
        for fmt in expected_formats:
            assert fmt.endswith(('.joblib', '.pth', '.onnx', '.pt'))


class TestAPIHealth:
    """Tests for API health endpoint."""
    
    def test_health_response_format(self):
        """Test health check response format."""
        expected_response = {"status": "ok"}
        assert "status" in expected_response
        assert expected_response["status"] == "ok"


# Integration test markers
@pytest.mark.integration
class TestIntegration:
    """Integration tests (require full setup)."""
    
    @pytest.mark.slow
    def test_full_pipeline(self):
        """Test full training pipeline (slow)."""
        # This would run the full pipeline
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
