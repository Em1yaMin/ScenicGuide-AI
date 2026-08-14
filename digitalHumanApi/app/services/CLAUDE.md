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
  chat_agent.py       # **弃用（A3）** — 自研 LangChain 对话 agent（会话存储层/记忆钩子被装配层复用：_get_or_create_conversation/_append_history/has_conversation/_schedule_memory_extraction；**对话落库 record_dialogue（dialogues 表，本地链路直接写入，路由层注入 db 调用）**；双模式对话能力保留为参考实现，A4 再评估删除）
  intent_classifier.py # classify_intent() — 双模式意图分类（简单/复杂，轻量规则词表，失败默认简单安全降级）
  planner_agent.py    # **弃用（A3）** — 复杂任务 LangGraph 规划图（规划→多步执行→总结，SSE step/token/done 契约，异常抛 PlannerError 由 chat_agent 降级快速路径；主链路规划能力已由 deepagents 装配层 prompt 级 _PLAN_INSTRUCTION 承载）
  memory_service.py   # 长期记忆（阶段2 T2）— save_user_memory/search_user_memory/count_user_memory/extract_facts/extract_and_store，user_memory 命名空间按 user 隔离（T3 契约见 doc/long-term-memory.md）；A2 新增 list_user_memories/delete_user_memory（记忆工具化按 id 删除，Document metadata 增 memory_id 内部扩展）；B5 新增 memory_id_of/find_memory_by_id/count_memories_per_user（管理端记忆管理复用，见 doc/memory-admin-api.md）；2026-08-13 新增 delete_all_user_memories（账号删除级联清理，见 doc/account-admin-api.md）
  deepagents/         # deepagents 0.7.5 装配层（A2 试点 → **A3 对话主链路**）— 路由 /api/v1/dialogue/chat 默认引擎走本层：harness.py（HarnessProfile 排除内置 9 工具 + 禁用通用子代理 + 装配常量）、memory_tools.py（记忆四工具 recall/save/list/delete_user_memory，InjectedState('user') 注入实现 user 隔离）、agent.py（get_agent 缓存/chat/chat_stream，会话管理复用 chat_agent 存储层 + 意图分流注入 _PLAN_INSTRUCTION + 轻量摘要注入，SSE step/token/done 契约与 ADH 前端一致）
  admin_agent/        # 管理端 agent（批次6 B4，deepagents 装配模式变体，X-Admin-Token 鉴权由路由层保证）— agent.py（get_agent 缓存/chat/chat_stream，复用 harness 装配常量，SSE 契约与用户侧一致）、tools.py（管理工具集：知识库/记忆/公告/统计/RAG 索引/首页数据/账号管理/数字人形象，复用既有 service；删除/禁用/重置密码类两阶段二次确认 + 写操作审计，工具清单见 AGENTS.md「管理员 agent」专节）、confirm.py（待确认项存储，短 TTL）；无会话持久化
  user_admin_service.py # 账号管理（2026-08-13）：用户列表/详情/创建/禁用启用/重置密码/删除（单目标级联 evaluations→dialogues→visitors→长期记忆→users），敏感操作 RBAC 由路由层把关，契约见 doc/account-admin-api.md
  admin_audit_service.py # 管理员操作审计落库（2026-08-13）：record_audit 写 admin_audit_logs（与业务同一事务），供查询接口与管理页展示
  dh_character_service.py # 数字人形象配置（2026-08-14）：get_character/set_character（app_settings 表 upsert 单行，key=digital_human_character；无记录/非法值回退默认 Hiyori 不写库，见 AGENTS.md「数字人形象管理」专节）
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
