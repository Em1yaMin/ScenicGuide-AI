import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine
from app.models import Base
from app.routers import admin, analysis, dialogue, knowledge, rag, recommend, visitor
from app.schemas.common import ErrorResponse
from app.schemas.health import HealthResponse
from app.tasks.analysis import analysis_loop
from app.tasks.recommend_questions import recommend_loop
from app.tasks.polling import polling_loop

logger = logging.getLogger(__name__)

_polling_task: Optional[asyncio.Task] = None
_analysis_task: Optional[asyncio.Task] = None
_recommend_task: Optional[asyncio.Task] = None


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Background task failed unexpectedly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task, _analysis_task, _recommend_task

    # Phase 0: configure logging early — before any tasks are spawned
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        force=True,
    )

    # Phase 1: create_all (IF NOT EXISTS, no destructive DROP)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Phase 2: background tasks (sync retries on its own interval)
    _polling_task = asyncio.create_task(polling_loop())
    _polling_task.add_done_callback(_handle_task_result)
    _analysis_task = asyncio.create_task(analysis_loop())
    _analysis_task.add_done_callback(_handle_task_result)
    _recommend_task = asyncio.create_task(recommend_loop())
    _recommend_task.add_done_callback(_handle_task_result)

    yield

    _polling_task.cancel()
    with suppress(asyncio.CancelledError):
        await _polling_task
    _analysis_task.cancel()
    with suppress(asyncio.CancelledError):
        await _analysis_task
    _recommend_task.cancel()
    with suppress(asyncio.CancelledError):
        await _recommend_task
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
app.include_router(recommend.router)
app.include_router(knowledge.router)
app.include_router(rag.router)


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler: returns HTTP 200 with unified JSON error format."""
    logger.error("Unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=200,
        content=ErrorResponse(code=500, message="服务器内部错误").model_dump(),
    )
