# Services Layer

## Responsibility
业务逻辑层。包含数据库查询/写入、外部 API 调用（Dify）、聚合统计。使用模块级 `async` 函数，无基类或依赖注入框架。

## Dependencies
- **SQLAlchemy 2.0**: `AsyncSession`、`select()`、`func`
- **httpx**: 外部 HTTP API（Dify 平台）
- **app.models**: ORM 实体
- **app.schemas**: Pydantic 请求/响应模型
- **app.core.config**: 环境配置（settings 单例）

## Consumers
- **routers/**: 路由端点通过 `Depends(get_db)` 注入会话后调用服务
- **tasks/**: 后台任务直接导入 `dify_client` 单例，自行管理 `async_session_factory()`

## Module Structure
```
services/
  visitor_service.py  # register_visitor() — 访客注册（upsert）
  admin_service.py    # get_dashboard_stats()、get_report_data() — 聚合统计
  knowledge_service.py # CRUD + import — 知识库条目管理
  dify_client.py      # DifyClient 类 + dify_client 单例 — Dify API 封装
  recommend_agent.py  # LangChain Tool Calling 个性化路线推荐 Agent
  auth_service.py     # register_user/authenticate_user/签发 user+guest token — 用户体系
  chat_agent.py       # chat()/chat_stream() — LangChain 对话 agent 主链路（结构化工具 + 知识检索 + 多轮会话短期记忆持久化：进程内缓存 + SQLite 双写，重启可恢复；stream 模式产 SSE 事件：step/token/done；对话后挂长期记忆提取钩子）
  memory_service.py   # 长期记忆（阶段2 T2）— save_user_memory/search_user_memory/count_user_memory/extract_facts/extract_and_store，user_memory 命名空间按 user 隔离（T3 契约见 doc/long-term-memory.md）
  dify_stream.py      # stream_dify_chat() — Dify Chat Agent 流式客户端（response_mode=streaming，SSE 解析 + ThinkFilter 过滤 think 块，双轨流式的兜底链路）
```

## Service Function Pattern (聚合/统计 — 推荐)

```python
import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.domain import DomainResponse

logger = logging.getLogger(__name__)

async def get_domain_data(db: AsyncSession) -> DomainResponse:
    """服务直接返回 Pydantic Response — router 层零转换。"""
    result = await db.execute(
        select(Model.field, func.count(Model.id)).group_by(Model.field)
    )
    items = [ItemType(name=row.field, count=row.count) for row in result.all()]
    return DomainResponse(items=items, total=len(items))
```

## External API Client Pattern (DifyClient)

```python
import asyncio
import httpx
from typing import Any, Callable, Awaitable, TypeVar

T = TypeVar("T")

async def _retry_silent(desc: str, fn: Callable[[], Awaitable[T]], default: T):
    """指数退避重试，失败返回 default（不抛异常）。"""
    for attempt in range(3):
        try: return await fn()
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError):
            await asyncio.sleep(0.5 * (2 ** attempt))
    return default

class ExternalClient:
    def __init__(self):
        self.base_url = settings.API_URL
        self.headers = {"Authorization": f"Bearer {settings.API_KEY}"}

    async def get_items(self, user: str) -> list[dict]:
        async def _fetch():
            async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers,
                                          timeout=httpx.Timeout(30.0)) as client:
                resp = await client.get("/items", params={"user": user})
                resp.raise_for_status()
                return resp.json().get("data", [])
        return await _retry_silent("get_items", _fetch, [])

# 模块级单例
client = ExternalClient()
```

## Architectural Boundaries
- **NO `commit()` in service functions** — `flush()` 仅发送 SQL，提交由 `get_db()` 或后台任务负责
- **NO 类包装器** — 除非是有状态的 API 客户端（如 DifyClient），否则使用模块级函数
- **NO 在服务中创建 DB 会话** — 会话由调用方注入
- **NO 抛异常** — 外部 API 客户端静默失败，返回安全默认值（`[]`、`None`）
- 后台任务自行管理 `async_session_factory()` + `commit()`/`rollback()`

<important if="you are adding a new service">
## Adding a New Service
1. 选择模式：聚合统计（返回 Pydantic Response）| 外部 API 客户端（类+单例）| CRUD（模块级函数）
2. 新服务函数签名为 `async def fn(db: AsyncSession, ...) -> XxxResponse`
3. 服务函数直接返回 Pydantic Response 模型（不返回 ORM 对象让 router 转换）
4. 在 `routers/<domain>.py` 中导入并使用
</important>
