from typing import Any, Dict


class ChatModel:
    """Placeholder model loader; replace with real pipeline initialization."""

    def __init__(self) -> None:
        # Load vector index / LLM / retriever here.
        self.name = "stub-model"

    def generate(self, message: str, top_k: int = 4) -> Dict[str, Any]:
        # Replace with real RAG call.
        return {
            "reply": f"Stub response for: {message}",
            "sources": ["doc-1", "doc-2"],
            "model": self.name,
        }
