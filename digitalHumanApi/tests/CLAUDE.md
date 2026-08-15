# Test Suite

## Responsibility
pytest + pytest-asyncio 测试套件。覆盖 FastAPI HTTP 端点、服务层逻辑、工具函数和重试机制。

## Dependencies
- **pytest + pytest-asyncio**: `asyncio_mode = auto`（`pytest.ini`）
- **httpx**: `AsyncClient` + `ASGITransport`（替代已弃用的 `TestClient`）
- **SQLAlchemy**: 内存 SQLite（`sqlite+aiosqlite:///:memory:`）

## Test Fixtures (conftest.py)

```
_reset_memory_store (autouse, 重置短期记忆存储为 :memory:，防跨测试污染；持久化/重启恢复专项测试自行 reset_store 到临时文件)
_reset_auth_rate_limiter (autouse, 重置认证限流器 auth._limiter，防既有登录/注册测试被 429 误伤；批次6 C3)
_pin_embedding_api_key_off (autouse, 固定 EMBEDDING_API_KEY 为空 → 走本地 TF-IDF)
_pin_openai_api_key_off (autouse, 固定 OPENAI_API_KEY 为空 → 运营分析不触发真实 LLM 调用；LLM 成功/失败路径由测试显式 monkeypatch `_get_analysis_chain` 模拟，见 test_analysis.py)
_reset_ai_overview_cache (autouse, 依赖 db_session：重置 AI 运营概览进程内缓存 + 清空 analysis_reports 落库记录，防跨测试污染；2026-08-15 落库持久化扩展，见 test_analysis.py)
_pin_ai_overview_background_refresh (autouse, 把 analytics_service._background_refresh 固定为 noop，防测试触发后台刷新写真实文件库；需真实落库链路的测试显式 monkeypatch 覆盖并注入测试 session 工厂)
db_engine → db_session → client
  (3 个函数级 fixture，链式依赖，每个测试完全隔离)
```

Embedding 测试另用 `rag_persist_dir` fixture（test_embedding.py 内）把 `RAG_DATA_DIR` 重定向到 tmp_path，避免污染工作区 `rag_data/`。

## HTTP Endpoint Test Pattern

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_register_visitor(client: AsyncClient):
    response = await client.post(
        "/api/v1/visitor/register",
        json={"visitor_id": "test-001"},
    )
    assert response.status_code in (200, 201)
```

## Service-Layer DB Test Pattern

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.admin_service import get_report_data

@pytest.mark.asyncio
async def test_report(db_session: AsyncSession):
    # 种子数据
    visitor = Visitor(id="v1")
    db_session.add(visitor)
    await db_session.commit()

    # 调用服务
    result = await get_report_data(db_session)
    assert result.sentiment_stats == []
```

## Pure Function Unit Test Pattern

```python
def test_normalize_strips_and_dedupes():
    assert _normalize_string_list([" 故宫 ", "故宫", ""]) == ["故宫"]
```

## Retry Logic Test Pattern (无 mock 框架)

```python
@pytest.mark.asyncio
async def test_retry_returns_default():
    async def _fn():
        raise httpx.ConnectError("boom")
    result = await _retry_silent("test", _fn, "fallback", max_retries=2)
    assert result == "fallback"
```

## Architectural Boundaries
- **无第三方 mock 框架** — 不使用 `unittest.mock`、`pytest-mock`；允许 pytest 内置 `monkeypatch`（固定环境变量/路径，见 conftest.py）与 `httpx.MockTransport`（模拟 Embedding/LLM API 网络层）
- 数据库测试用真实 SQLite 内存引擎，HTTP 测试用 FastAPI `dependency_overrides` 替换 `get_db`
- 函数级隔离 — 每个测试独立 engine/session/client，测试间数据不互相影响
- 不测试外部 API（Dify）— 通过内联 stub 函数模拟

<important if="you are writing or modifying tests">
## Adding a New Test
1. 纯函数 → `def test_*():` 直接调用
2. 服务+DB → `@pytest.mark.asyncio` + `db_session: AsyncSession` 参数
3. HTTP 端点 → `@pytest.mark.asyncio` + `client: AsyncClient` 参数
4. 重试逻辑 → 内联 `async def` stub 函数，不 mock
</important>
