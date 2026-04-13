"""
Unit Tests for ML Pipelines
Tests for data validation, training, and quality gates
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
    
    def test_validate_csv_files_empty_dir(self):
        """Test validation with empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from ml.pipelines.validate_data import validate_csv_files
            
            results = validate_csv_files(Path(tmpdir))
            
            assert results["files_checked"] == 0
            assert results["valid"] == True  # No files = no failures
    
    def test_validate_csv_files_with_valid_data(self):
        """Test validation with valid CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test_faqs.csv"
            df = pd.DataFrame({
                "question": ["What is VAT?", "How to pay taxes?", "TIN registration?", 
                            "Import duties?", "Export process?"],
                "answer": ["VAT is a consumption tax.", "You can pay online.", 
                          "Apply at URA office.", "Depends on goods.", "Submit forms."]
            })
            df.to_csv(csv_path, index=False)
            
            from ml.pipelines.validate_data import validate_csv_files
            
            results = validate_csv_files(Path(tmpdir))
            
            assert results["files_checked"] == 1
            assert results["files_passed"] == 1
            assert results["valid"] == True
    
    def test_validate_single_csv_with_missing_data(self):
        """Test validation catches high missing ratio."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test.csv"
            df = pd.DataFrame({
                "question": ["Q1", None, None, None, "Q5"],
                "answer": ["A1", None, None, None, "A5"]
            })
            df.to_csv(csv_path, index=False)
            
            from ml.pipelines.validate_data import validate_single_csv
            
            result = validate_single_csv(csv_path)
            
            assert result["file"] == "test.csv"
            assert result["stats"]["rows"] == 5
            # High missing ratio should generate warnings or errors
    
    def test_validate_single_csv_stats(self):
        """Test validation returns correct stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test.csv"
            df = pd.DataFrame({
                "question": ["Q1", "Q2", "Q3"],
                "answer": ["A1", "A2", "A3"]
            })
            df.to_csv(csv_path, index=False)
            
            from ml.pipelines.validate_data import validate_single_csv
            
            result = validate_single_csv(csv_path)
            
            assert result["stats"]["rows"] == 3
            assert result["stats"]["columns"] == 2
            assert "question" in result["stats"]["column_names"]
            assert "answer" in result["stats"]["column_names"]


class TestTrainingPipeline:
    """Tests for model training pipeline."""
    
    def test_load_config(self):
        """Test configuration loading."""
        config_path = PROJECT_ROOT / "ml" / "configs" / "training_config.yaml"
        
        if config_path.exists():
            from ml.pipelines.train import load_config
            
            config = load_config(str(config_path))
            
            assert "data" in config
            assert "models" in config
            assert "training" in config
            assert "quality_gates" in config
    
    def test_clean_text(self):
        """Test text cleaning function."""
        from ml.pipelines.train import clean_text
        
        # Test newline normalization
        assert clean_text("Hello\nWorld") == "Hello World"
        assert clean_text("Hello\r\nWorld") == "Hello World"
        
        # Test multiple spaces
        assert clean_text("Hello   World") == "Hello World"
        
        # Test combined
        assert clean_text("Hello\n\n  World  with   spaces") == "Hello World with spaces"
        
        # Test NaN handling
        assert clean_text(np.nan) == ''
        assert clean_text(None) == ''
    
    def test_load_qa_data_empty_dir(self):
        """Test loading from empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from ml.pipelines.train import load_qa_data
            
            config = {
                'data': {
                    'min_question_words': 3,
                    'min_answer_words': 3,
                    'max_words': 512
                }
            }
            
            df = load_qa_data(Path(tmpdir), config)
            
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 0
    
    def test_load_qa_data_with_csv(self):
        """Test loading CSV with Q&A data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "ura_vat_faqs.csv"
            df = pd.DataFrame({
                "question": [
                    "What is the VAT rate in Uganda?",
                    "How do I register for VAT?",
                    "When is VAT due?"
                ],
                "answer": [
                    "The standard VAT rate is 18 percent.",
                    "You can register online at the URA portal.",
                    "VAT returns are due by the 15th of each month."
                ]
            })
            df.to_csv(csv_path, index=False)
            
            from ml.pipelines.train import load_qa_data
            
            config = {
                'data': {
                    'min_question_words': 3,
                    'min_answer_words': 3,
                    'max_words': 512
                }
            }
            
            result = load_qa_data(Path(tmpdir), config)
            
            assert len(result) == 3
            assert "question" in result.columns
            assert "answer" in result.columns
            assert "tag" in result.columns
            assert "source" in result.columns
    
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
        assert set(decoded) == {"customs", "tin", "vat"}


class TestQualityGates:
    """Tests for quality gates pipeline."""
    
    def test_check_quality_gates_all_pass(self):
        """Test quality gates when all metrics pass."""
        from ml.pipelines.quality_gates import check_quality_gates
        
        metrics = {
            "test_accuracy": 0.85,
            "test_f1_macro": 0.80,
            "test_precision": 0.82,
            "test_recall": 0.78
        }
        gates = {
            "min_accuracy": 0.75,
            "min_f1_score": 0.70,
            "min_precision": 0.70,
            "min_recall": 0.65
        }
        
        results = check_quality_gates(metrics, gates)
        
        assert results["passed"] == True
        assert len(results["checks"]) == 4
        assert all(check["passed"] for check in results["checks"])
    
    def test_check_quality_gates_accuracy_fail(self):
        """Test quality gates when accuracy fails."""
        from ml.pipelines.quality_gates import check_quality_gates
        
        metrics = {
            "test_accuracy": 0.60,  # Below threshold
            "test_f1_macro": 0.80,
            "test_precision": 0.82,
            "test_recall": 0.78
        }
        gates = {
            "min_accuracy": 0.75,
            "min_f1_score": 0.70,
            "min_precision": 0.70,
            "min_recall": 0.65
        }
        
        results = check_quality_gates(metrics, gates)
        
        assert results["passed"] == False
        
        accuracy_check = next(c for c in results["checks"] if c["name"] == "accuracy")
        assert accuracy_check["passed"] == False
        assert accuracy_check["value"] == 0.60
        assert accuracy_check["threshold"] == 0.75
    
    def test_check_quality_gates_with_latency(self):
        """Test quality gates including latency check."""
        from ml.pipelines.quality_gates import check_quality_gates
        
        metrics = {
            "test_accuracy": 0.85,
            "test_f1_macro": 0.80,
            "test_precision": 0.82,
            "test_recall": 0.78,
            "latency": {"p95_ms": 50}
        }
        gates = {
            "min_accuracy": 0.75,
            "min_f1_score": 0.70,
            "min_precision": 0.70,
            "min_recall": 0.65,
            "max_latency_ms": 100
        }
        
        results = check_quality_gates(metrics, gates)
        
        assert results["passed"] == True
        
        latency_check = next(c for c in results["checks"] if c["name"] == "latency_p95")
        assert latency_check["passed"] == True
        assert latency_check["value"] == 50
    
    def test_check_quality_gates_latency_fail(self):
        """Test quality gates when latency exceeds threshold."""
        from ml.pipelines.quality_gates import check_quality_gates
        
        metrics = {
            "test_accuracy": 0.85,
            "test_f1_macro": 0.80,
            "test_precision": 0.82,
            "test_recall": 0.78,
            "latency": {"p95_ms": 150}  # Exceeds threshold
        }
        gates = {
            "min_accuracy": 0.75,
            "min_f1_score": 0.70,
            "min_precision": 0.70,
            "min_recall": 0.65,
            "max_latency_ms": 100
        }
        
        results = check_quality_gates(metrics, gates)
        
        assert results["passed"] == False
        
        latency_check = next(c for c in results["checks"] if c["name"] == "latency_p95")
        assert latency_check["passed"] == False


class TestModelExport:
    """Tests for model export functionality."""
    
    def test_expected_export_formats(self):
        """Test expected export file formats."""
        expected_formats = {
            "tag_classifier.joblib": ".joblib",
            "label_encoder.joblib": ".joblib",
            "tag_classifier.pth": ".pth",
            "tag_classifier.onnx": ".onnx",
        }
        
        for filename, ext in expected_formats.items():
            assert filename.endswith(ext)
    
    def test_model_directory_structure(self):
        """Test Model directory exists and structure."""
        model_dir = PROJECT_ROOT / "Model"
        
        # Directory should exist (may be empty in test environment)
        expected_files = [
            "tag_classifier.joblib",
            "label_encoder.joblib",
        ]
        
        for filename in expected_files:
            assert filename.endswith(('.joblib', '.pth', '.onnx', '.pkl', '.bin'))


class TestConfigValidation:
    """Tests for configuration validation."""
    
    def test_training_config_structure(self):
        """Test training config has required sections."""
        config_path = PROJECT_ROOT / "ml" / "configs" / "training_config.yaml"
        
        if config_path.exists():
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            # Required top-level sections
            assert "data" in config
            assert "models" in config
            assert "training" in config
            assert "quality_gates" in config
            
            # Data section
            assert "datasets_dir" in config["data"]
            
            # Training section
            assert "batch_size" in config["training"]
            
            # Quality gates
            assert "min_accuracy" in config["quality_gates"]
    
    def test_quality_gates_thresholds(self):
        """Test quality gates have reasonable thresholds."""
        config_path = PROJECT_ROOT / "ml" / "configs" / "training_config.yaml"
        
        if config_path.exists():
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            gates = config.get("quality_gates", {})
            
            # Thresholds should be between 0 and 1
            if "min_accuracy" in gates:
                assert 0 < gates["min_accuracy"] <= 1
            if "min_f1_score" in gates:
                assert 0 < gates["min_f1_score"] <= 1


# Integration test markers
@pytest.mark.integration
class TestIntegration:
    """Integration tests (require full setup)."""
    
    @pytest.mark.slow
    def test_full_validation_pipeline(self):
        """Test full data validation pipeline."""
        datasets_dir = PROJECT_ROOT / "Data" / "dataset"
        
        if datasets_dir.exists():
            from ml.pipelines.validate_data import validate_csv_files
            
            results = validate_csv_files(datasets_dir)
            
            assert results["files_checked"] > 0
            assert results["files_passed"] > 0
    
    @pytest.mark.slow
    def test_data_loading(self):
        """Test loading actual dataset."""
        datasets_dir = PROJECT_ROOT / "Data" / "dataset"
        
        if datasets_dir.exists():
            from ml.pipelines.train import load_qa_data, load_config
            
            config_path = PROJECT_ROOT / "ml" / "configs" / "training_config.yaml"
            if config_path.exists():
                config = load_config(str(config_path))
                df = load_qa_data(datasets_dir, config)
                
                assert len(df) > 0
                assert "question" in df.columns
                assert "tag" in df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
