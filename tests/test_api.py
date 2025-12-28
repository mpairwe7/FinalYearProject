"""
Tests for Backend API
Tests for FastAPI endpoints and ChatModel service
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))


class TestHealthEndpoint:
    """Tests for health check endpoint."""
    
    def test_health_returns_ok(self):
        """Test health endpoint returns ok status."""
        from App.backend.app.main import health
        
        response = health()
        
        assert response["status"] == "ok"
    
    def test_health_response_format(self):
        """Test health response has correct format."""
        from App.backend.app.main import health
        
        response = health()
        
        assert isinstance(response, dict)
        assert "status" in response


class TestChatRequest:
    """Tests for ChatRequest model."""
    
    def test_chat_request_valid(self):
        """Test valid chat request."""
        from App.backend.app.models import ChatRequest
        
        request = ChatRequest(message="How do I pay VAT?")
        
        assert request.message == "How do I pay VAT?"
        assert request.top_k == 4  # default value
        assert request.conversation_id is None
    
    def test_chat_request_with_options(self):
        """Test chat request with all options."""
        from App.backend.app.models import ChatRequest
        
        request = ChatRequest(
            message="What is VAT?",
            conversation_id="conv-123",
            top_k=6
        )
        
        assert request.message == "What is VAT?"
        assert request.conversation_id == "conv-123"
        assert request.top_k == 6
    
    def test_chat_request_top_k_bounds(self):
        """Test top_k field has proper bounds."""
        from App.backend.app.models import ChatRequest
        from pydantic import ValidationError
        
        # Valid values
        request = ChatRequest(message="test", top_k=1)
        assert request.top_k == 1
        
        request = ChatRequest(message="test", top_k=10)
        assert request.top_k == 10
        
        # Invalid values should raise error
        with pytest.raises(ValidationError):
            ChatRequest(message="test", top_k=0)
        
        with pytest.raises(ValidationError):
            ChatRequest(message="test", top_k=11)
    
    def test_chat_request_empty_message_fails(self):
        """Test empty message fails validation."""
        from App.backend.app.models import ChatRequest
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            ChatRequest(message="")


class TestChatResponse:
    """Tests for ChatResponse model."""
    
    def test_chat_response_format(self):
        """Test chat response format."""
        from App.backend.app.models import ChatResponse
        
        response = ChatResponse(
            reply="You can pay VAT online...",
            sources=["doc-1", "doc-2"],
            model="stub-model"
        )
        
        assert response.reply == "You can pay VAT online..."
        assert response.sources == ["doc-1", "doc-2"]
        assert response.model == "stub-model"
    
    def test_chat_response_default_values(self):
        """Test chat response with defaults."""
        from App.backend.app.models import ChatResponse
        
        response = ChatResponse(reply="Test response")
        
        assert response.reply == "Test response"
        assert response.sources == []
        assert response.model == "stub-model"


class TestChatModel:
    """Tests for ChatModel service."""
    
    def test_model_initialization(self):
        """Test model initializes correctly."""
        from App.backend.app.service import ChatModel
        
        model = ChatModel()
        
        assert model.name == "stub-model"
    
    def test_generate_returns_dict(self):
        """Test generate returns proper dict."""
        from App.backend.app.service import ChatModel
        
        model = ChatModel()
        result = model.generate("How do I pay VAT?")
        
        assert isinstance(result, dict)
        assert "reply" in result
        assert "sources" in result
        assert "model" in result
    
    def test_generate_with_top_k(self):
        """Test generate accepts top_k parameter."""
        from App.backend.app.service import ChatModel
        
        model = ChatModel()
        result = model.generate("Test question", top_k=5)
        
        assert isinstance(result, dict)
        assert "reply" in result
    
    def test_generate_response_content(self):
        """Test generate response contains expected content."""
        from App.backend.app.service import ChatModel
        
        model = ChatModel()
        result = model.generate("What is VAT?")
        
        # Stub model includes message in reply
        assert "What is VAT?" in result["reply"]
        assert isinstance(result["sources"], list)
        assert result["model"] == "stub-model"


class TestChatEndpoint:
    """Tests for chat endpoint integration."""
    
    def test_chat_endpoint_integration(self):
        """Test full chat endpoint flow."""
        from App.backend.app.models import ChatRequest, ChatResponse
        from App.backend.app.service import ChatModel
        
        # Create request
        request = ChatRequest(message="How do I register for TIN?")
        
        # Process with model
        model = ChatModel()
        result = model.generate(request.message, top_k=request.top_k)
        
        # Create response
        response = ChatResponse(**result)
        
        assert isinstance(response.reply, str)
        assert len(response.reply) > 0


class TestAPIModels:
    """Tests for API models validation."""
    
    def test_models_importable(self):
        """Test all models can be imported."""
        from App.backend.app.models import ChatRequest, ChatResponse
        
        assert ChatRequest is not None
        assert ChatResponse is not None
    
    def test_service_importable(self):
        """Test service can be imported."""
        from App.backend.app.service import ChatModel
        
        assert ChatModel is not None


# FastAPI TestClient tests (require httpx)
class TestFastAPIApp:
    """Tests using FastAPI TestClient."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        try:
            from fastapi.testclient import TestClient
            from App.backend.app.main import app, startup_event
            
            # Initialize model
            startup_event()
            
            return TestClient(app)
        except ImportError:
            pytest.skip("httpx not installed for TestClient")
    
    def test_health_endpoint(self, client):
        """Test /health endpoint."""
        if client is None:
            pytest.skip("TestClient not available")
        
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_chat_endpoint(self, client):
        """Test /v1/chat endpoint."""
        if client is None:
            pytest.skip("TestClient not available")
        
        response = client.post(
            "/v1/chat",
            json={"message": "What is VAT?"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert "sources" in data
        assert "model" in data
    
    def test_chat_endpoint_with_options(self, client):
        """Test /v1/chat with all options."""
        if client is None:
            pytest.skip("TestClient not available")
        
        response = client.post(
            "/v1/chat",
            json={
                "message": "How do I pay taxes?",
                "conversation_id": "test-conv-123",
                "top_k": 5
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
    
    def test_chat_endpoint_validation(self, client):
        """Test /v1/chat validates input."""
        if client is None:
            pytest.skip("TestClient not available")
        
        # Empty message should fail
        response = client.post(
            "/v1/chat",
            json={"message": ""}
        )
        
        assert response.status_code == 422  # Validation error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
