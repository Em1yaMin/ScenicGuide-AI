import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine
from app.models import Base
from app.routers import admin, analysis, dialogue, visitor
from app.schemas.health import HealthResponse
from app.tasks.analysis import analysis_loop
from app.tasks.polling import polling_loop

logger = logging.getLogger(__name__)

_polling_task: Optional[asyncio.Task] = None
_analysis_task: Optional[asyncio.Task] = None


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Background task failed unexpectedly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task, _analysis_task
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _polling_task = asyncio.create_task(polling_loop())
    _polling_task.add_done_callback(_handle_task_result)
    _analysis_task = asyncio.create_task(analysis_loop())
    _analysis_task.add_done_callback(_handle_task_result)

    yield

    _polling_task.cancel()
    with suppress(asyncio.CancelledError):
        await _polling_task
    _analysis_task.cancel()
    with suppress(asyncio.CancelledError):
        await _analysis_task
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(visitor.router)
app.include_router(admin.router)
app.include_router(dialogue.router)
app.include_router(analysis.router)


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
