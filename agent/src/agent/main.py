from fastapi import FastAPI
from agent.routers import chat
from python_core_utils import setup_logging, CorrelationIdMiddleware

# Set up logging with correlation ID
setup_logging()

app = FastAPI(title="Text2SQL Agent Service")

# Register middleware
app.add_middleware(CorrelationIdMiddleware)

app.include_router(chat.router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
