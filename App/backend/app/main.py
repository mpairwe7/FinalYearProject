from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .models import ChatRequest, ChatResponse
from .service import ChatModel

app = FastAPI(title="URA Chatbot API", version="0.1.0")

model_instance: ChatModel | None = None


def get_model() -> ChatModel:
    assert model_instance is not None, "Model not initialized"
    return model_instance


@app.on_event("startup")
def startup_event() -> None:
    global model_instance
    model_instance = ChatModel()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
def chat(request: ChatRequest, model: ChatModel = Depends(get_model)) -> ChatResponse:
    result = model.generate(request.message, top_k=request.top_k)
    return ChatResponse(**result)
