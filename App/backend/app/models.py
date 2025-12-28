from pydantic import BaseModel, Field
from typing import List, Optional


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message text")
    conversation_id: Optional[str] = Field(None, description="Optional conversation/session id")
    top_k: int = Field(4, ge=1, le=10, description="Number of passages to retrieve")


class ChatResponse(BaseModel):
    reply: str
    sources: List[str] = Field(default_factory=list)
    model: str = "stub-model"
