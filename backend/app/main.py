import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.jobs import store
from app.services import llm

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    store.fail_interrupted()
    warm = asyncio.create_task(llm.warm_up())
    yield
    warm.cancel()


app = FastAPI(title="YouTube Video Creator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "keys": {
            "openrouter": bool(settings.openrouter_api_key),
            "pexels": bool(settings.pexels_api_key),
            "pixabay": bool(settings.pixabay_api_key),
            "ollama": bool(settings.ollama_api_key),
        },
        "text_providers": {
            "order": llm.requested_names(),
            "active": [p.name for p in llm.provider_chain()],
        },
    }
