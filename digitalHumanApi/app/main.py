import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine
from app.core.logging_config import RequestLoggingMiddleware, configure_logging
from app.models import Base
from app.routers import (
    admin,
    admin_agent,
    analysis,
    announcement,
    auth,
    dialogue,
    home,
    knowledge,
    memory_admin,
    rag,
    recommend,
    usage,
    visitor,
)
from app.schemas.common import ErrorResponse
from app.schemas.health import HealthResponse
from app.tasks.analysis import analysis_loop
from app.tasks.memory_cleanup import memory_cleanup_loop
from app.tasks.polling import polling_loop
from app.tasks.recommend_questions import recommend_loop
from app.services.usage_tracker import usage_tracker

logger = logging.getLogger(__name__)

_polling_task: Optional[asyncio.Task] = None
_analysis_task: Optional[asyncio.Task] = None
_recommend_task: Optional[asyncio.Task] = None
_memory_cleanup_task: Optional[asyncio.Task] = None
_usage_flush_task: Optional[asyncio.Task] = None


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Background task failed unexpectedly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task, _analysis_task, _recommend_task, _memory_cleanup_task
    global _usage_flush_task

    # Phase 0: 统一日志配置（结构化格式，见 app/core/logging_config.py）——在任务启动前完成
    configure_logging()

    # Phase 1: 数据库迁移（Alembic，见 app/core/migrations.py 与 doc/alembic-migrations.md）
    # 平滑过渡：空库 upgrade 建齐 / 遗留库（无版本表）自动 stamp 基线 / 版本化库 upgrade
    # 迁移失败时回退 create_all（IF NOT EXISTS 幂等建表，不丢数据、不阻断启动）
    try:
        from app.core.migrations import run_migrations

        await run_migrations()
    except Exception:
        logger.exception("Alembic 迁移失败，回退 Base.metadata.create_all 幂等建表")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Phase 2: background tasks (sync retries on its own interval)
    _polling_task = asyncio.create_task(polling_loop())
    _polling_task.add_done_callback(_handle_task_result)
    _analysis_task = asyncio.create_task(analysis_loop())
    _analysis_task.add_done_callback(_handle_task_result)
    _recommend_task = asyncio.create_task(recommend_loop())
    _recommend_task.add_done_callback(_handle_task_result)
    # 短期记忆过期清理（启动立即执行一次 + 定期，见 app/tasks/memory_cleanup.py）
    _memory_cleanup_task = asyncio.create_task(memory_cleanup_loop())
    _memory_cleanup_task.add_done_callback(_handle_task_result)
    # 批次7 C5：LLM 用量记账后台 flush（定时批量落库，关闭时兜底 flush）
    _usage_flush_task = asyncio.create_task(usage_tracker.flush_loop())
    _usage_flush_task.add_done_callback(_handle_task_result)

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
    _memory_cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await _memory_cleanup_task
    # 用量记账关闭：先 cancel flush 循环，再兜底 flush 剩余队列（防丢账）
    if _usage_flush_task is not None:
        _usage_flush_task.cancel()
        with suppress(asyncio.CancelledError):
            await _usage_flush_task
        _usage_flush_task = None
    await usage_tracker.shutdown()
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
    # 批次6 C3：白名单模式下 credentials 不能与 * 混用（Starlette 会把 * 退化
    # 为回显任意 origin = 全放开），config validator 已启动拦截；此处按配置透传
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 请求日志中间件（最后添加 = 最外层，覆盖 CORS/预检等全部请求；
# 见 app/core/logging_config.py 与 doc/logging-request-logs.md）
app.add_middleware(RequestLoggingMiddleware)

app.include_router(visitor.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(dialogue.router)
app.include_router(analysis.router)
app.include_router(recommend.router)
app.include_router(knowledge.router)
app.include_router(announcement.admin_router)
app.include_router(announcement.public_router)
app.include_router(memory_admin.router)
app.include_router(admin_agent.router)
app.include_router(home.router)
app.include_router(rag.router)
app.include_router(usage.router)


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
