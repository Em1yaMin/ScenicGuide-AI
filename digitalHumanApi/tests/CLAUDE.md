# Test Suite

## Responsibility
pytest + pytest-asyncio 测试套件。覆盖 FastAPI HTTP 端点、服务层逻辑、工具函数和重试机制。

## Dependencies
- **pytest + pytest-asyncio**: `asyncio_mode = auto`（`pytest.ini`）
- **httpx**: `AsyncClient` + `ASGITransport`（替代已弃用的 `TestClient`）
- **SQLAlchemy**: 内存 SQLite（`sqlite+aiosqlite:///:memory:`）

## Test Fixtures (conftest.py)

```
db_engine → db_session → client
  (3 个函数级 fixture，链式依赖，每个测试完全隔离)
```

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
- **ZERO mock 库** — 不使用 `unittest.mock`、`pytest-mock`、`monkeypatch`
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
