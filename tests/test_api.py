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


class TestFeedbackModels:
    """Tests for feedback models."""

    def test_feedback_request_valid(self):
        """Test valid feedback request."""
        from App.backend.app.models import FeedbackRequest

        fb = FeedbackRequest(message_id="msg-123", rating="up")
        assert fb.rating == "up"
        assert fb.comment == ""

    def test_feedback_request_with_comment(self):
        """Test feedback request with comment."""
        from App.backend.app.models import FeedbackRequest

        fb = FeedbackRequest(
            message_id="msg-123",
            rating="down",
            comment="Answer was not helpful",
            user_query="How do I pay VAT?",
            bot_reply="Some reply",
        )
        assert fb.rating == "down"
        assert fb.comment == "Answer was not helpful"

    def test_feedback_request_invalid_rating(self):
        """Test feedback rejects invalid rating."""
        from App.backend.app.models import FeedbackRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FeedbackRequest(message_id="msg-123", rating="maybe")

    def test_feedback_response_format(self):
        """Test feedback response format."""
        from App.backend.app.models import FeedbackResponse

        resp = FeedbackResponse(
            id="fb-1", message_id="msg-123", rating="up", created_at=1709000000.0
        )
        assert resp.id == "fb-1"
        assert resp.rating == "up"


class TestAnalyticsEvent:
    """Tests for analytics event model."""

    def test_analytics_event_valid(self):
        """Test valid analytics event."""
        from App.backend.app.models import AnalyticsEvent

        event = AnalyticsEvent(event_type="chat_sent", event_data={"length": 42})
        assert event.event_type == "chat_sent"
        assert event.event_data == {"length": 42}

    def test_analytics_event_defaults(self):
        """Test analytics event defaults."""
        from App.backend.app.models import AnalyticsEvent

        event = AnalyticsEvent(event_type="page_view")
        assert event.event_data == {}
        assert event.session_id is None


class TestDatabaseLayer:
    """Tests for the analytics database layer."""

    def test_init_db(self, tmp_path):
        """Test database initialization creates tables."""
        import os
        os.environ["ANALYTICS_DB_DIR"] = str(tmp_path)

        # Force reimport to pick up new env
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        # Clear thread-local to force reconnection
        database._local.conn = None

        database.init_db()
        assert (tmp_path / "analytics.db").exists()

    def test_save_and_retrieve_feedback(self, tmp_path):
        """Test feedback save and summary retrieval."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        database.save_feedback("msg-1", "up", session_id="s1")
        database.save_feedback("msg-2", "down", comment="Bad answer", session_id="s1")

        summary = database.get_feedback_summary(days=1)
        assert summary["total"] == 2
        assert summary["thumbs_up"] == 1
        assert summary["thumbs_down"] == 1
        assert summary["satisfaction_pct"] == 50.0

    def test_track_and_count_events(self, tmp_path):
        """Test event tracking and counting."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        database.track_event("chat_sent", '{"len":10}', session_id="s1")
        database.track_event("chat_sent", '{"len":20}', session_id="s1")
        database.track_event("voice_used", '{}', session_id="s1")

        counts = database.get_event_counts(days=1)
        assert counts["chat_sent"] == 2
        assert counts["voice_used"] == 1

    def test_conversation_logging(self, tmp_path):
        """Test conversation logging and stats."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        conv_id = database.log_conversation(
            session_id="s1",
            user_message="What is VAT?",
            bot_reply="VAT is...",
            response_time_ms=45.2,
            confidence=0.85,
            topic_tag="vat",
        )
        assert conv_id  # non-empty string

        stats = database.get_conversation_stats(days=1)
        assert stats["total_conversations"] == 1
        assert stats["avg_response_time_ms"] == 45.2


class TestMetricsStore:
    """Tests for the in-process metrics store."""

    def test_counter_increment(self):
        """Test counter increments correctly."""
        from App.backend.app.analytics import MetricsStore

        store = MetricsStore()
        store.inc("test_counter")
        store.inc("test_counter")

        snap = store.snapshot()
        assert snap["counters"]["test_counter"] == 2

    def test_histogram_observation(self):
        """Test histogram records observations."""
        from App.backend.app.analytics import MetricsStore

        store = MetricsStore()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            store.observe("test_latency", v)

        snap = store.snapshot()
        hist = snap["histograms"]["test_latency"]
        assert hist["count"] == 5
        assert hist["min"] == 10.0
        assert hist["max"] == 50.0
        assert hist["avg"] == 30.0

    def test_histogram_bounded_memory(self):
        """Test histogram uses bounded deque (no memory leak)."""
        from App.backend.app.analytics import MetricsStore, _MAX_HISTOGRAM_SIZE

        store = MetricsStore()
        # Insert more than the max size
        for i in range(6000):
            store.observe("mem_test", float(i))

        snap = store.snapshot()
        assert snap["histograms"]["mem_test"]["count"] == _MAX_HISTOGRAM_SIZE

    def test_prometheus_export(self):
        """Test Prometheus text format export."""
        from App.backend.app.analytics import MetricsStore

        store = MetricsStore()
        store.inc("http_requests_total", labels={"method": "GET", "path": "/health", "status": "200"})

        text = store.to_prometheus()
        assert "http_requests_total" in text
        assert "counter" in text

    def test_prometheus_no_duplicate_type_declarations(self):
        """Test TYPE is declared only once per metric name."""
        from App.backend.app.analytics import MetricsStore

        store = MetricsStore()
        store.inc("req", labels={"method": "GET"})
        store.inc("req", labels={"method": "POST"})

        text = store.to_prometheus()
        assert text.count("# TYPE req counter") == 1

    def test_labeled_metrics(self):
        """Test metrics with labels."""
        from App.backend.app.analytics import MetricsStore

        store = MetricsStore()
        store.inc("req", labels={"method": "GET"})
        store.inc("req", labels={"method": "POST"})
        store.inc("req", labels={"method": "GET"})

        snap = store.snapshot()
        assert snap["counters"]['req{method="GET"}'] == 2
        assert snap["counters"]['req{method="POST"}'] == 1


class TestFeedbackCommentModel:
    """Tests for FeedbackCommentRequest model."""

    def test_comment_request_valid(self):
        """Test valid comment request."""
        from App.backend.app.models import FeedbackCommentRequest

        req = FeedbackCommentRequest(comment="This was unhelpful because...")
        assert req.comment == "This was unhelpful because..."

    def test_comment_request_empty_fails(self):
        """Test empty comment fails validation."""
        from App.backend.app.models import FeedbackCommentRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FeedbackCommentRequest(comment="")


class TestDatabaseFeedbackUpdate:
    """Tests for feedback comment update (PATCH)."""

    def test_update_feedback_comment(self, tmp_path):
        """Test updating feedback comment."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        database.save_feedback("msg-1", "down", session_id="s1")
        result = database.update_feedback_comment("msg-1", "Needs better sources")
        assert result is True

    def test_update_nonexistent_feedback(self, tmp_path):
        """Test updating non-existent feedback returns False."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        result = database.update_feedback_comment("nonexistent", "Comment")
        assert result is False


class TestDatabaseErrorHandling:
    """Tests for database error handling and rollback."""

    def test_save_feedback_returns_data(self, tmp_path):
        """Test save_feedback returns correct data structure."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        result = database.save_feedback("msg-1", "up", session_id="s1")
        assert "id" in result
        assert result["message_id"] == "msg-1"
        assert result["rating"] == "up"
        assert "created_at" in result

    def test_session_upsert_increments_count(self, tmp_path):
        """Test session upsert correctly increments message count."""
        import importlib
        from App.backend.app import database
        importlib.reload(database)
        database._DB_DIR = tmp_path
        database._DB_PATH = tmp_path / "analytics.db"
        database._local.conn = None
        database.init_db()

        database.upsert_session("s1", user_agent="TestAgent")
        database.upsert_session("s1", user_agent="TestAgent")
        database.upsert_session("s1", user_agent="TestAgent")

        stats = database.get_session_stats(days=1)
        assert stats["total_sessions"] == 1
        assert stats["max_messages_in_session"] == 2  # 2 increments after initial 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
