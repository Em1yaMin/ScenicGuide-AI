from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.core import memory_store


@pytest.fixture(autouse=True)
def _reset_memory_store():
    """每个测试重置短期记忆存储为内存模式，避免跨测试污染。

    持久化/重启恢复专项测试自行 reset_store 到临时文件路径（在本 fixture
    之后执行，覆盖内存模式）。
    """
    memory_store.reset_store(db_path=":memory:")


@pytest.fixture(autouse=True)
def _reset_auth_rate_limiter():
    """每个测试重置认证限流器，避免跨测试污染。

    批次6 C3 安全加固：登录/注册限流为进程内全局单例（auth 模块 _limiter），
    既有测试含多次错误密码/重复注册用例，若不重置会触发 429 误伤。
    """
    from app.routers import auth as auth_router

    auth_router._limiter.reset()


@pytest.fixture(autouse=True)
def _pin_embedding_api_key_off(monkeypatch):
    """测试环境无真实 Embedding Key：固定为空，保证向量化走本地 TF-IDF。

    向量化/命名空间隔离/降级等由测试内的 fake embedding（确定性向量）
    与降级模拟覆盖，真实 Minimax API 冒烟留到部署阶段。
    """
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")


@pytest.fixture(autouse=True)
def _reset_ai_overview_cache():
    """每个测试重置 AI 运营概览结果缓存，避免跨测试污染。

    overview 提速：analytics_service 模块级 TTL 缓存 + in-flight 合并，
    若不重置，前一个测试缓存的同 days 结果会泄漏到后一个测试（断言脏数据）。
    """
    from app.services import analytics_service

    analytics_service.reset_ai_overview_cache()


@pytest.fixture(autouse=True)
def _reset_usage_tracker():
    """每个测试重置 LLM 用量记账队列/统计，避免跨测试污染。

    批次7 C5：llm.py 工厂默认包装 UsageTrackingChatOpenAI（测试中大量
    mock LLM 调用会入队），不重置会跨测试累计；关闭状态（shutdown 后）
    由专项测试自行管理。
    """
    from app.services.usage_tracker import usage_tracker

    usage_tracker.reset()
    yield
    usage_tracker.reset()


@pytest.fixture(autouse=True)
def _pin_openai_api_key_off(monkeypatch):
    """测试环境固定关闭运营分析 LLM key（与 CI 一致，不依赖本地 .env）。

    本地 worktree 的 .env 配置了真实 opencode-go key，若不固定关闭，
    test_analysis 的 overview 用例会触发真实 LLM 调用（实测 20-90s+，慢且不稳定）。
    LLM 成功/失败路径由测试显式 monkeypatch _get_analysis_chain 模拟，
    真实 opencode-go 冒烟留到 doc/phase3-analytics-verification.md 验证。
    """
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session


@pytest.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
