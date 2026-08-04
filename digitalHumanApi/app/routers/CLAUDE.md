# Routers Layer

## Responsibility
FastAPI HTTP 端点定义层。纯接线层——验证请求体、注入 DB 会话、委托服务函数。**不包含业务逻辑。**

## Dependencies
- **FastAPI**: `APIRouter`、`Depends`
- **app.core.database**: `get_db` 依赖（异步会话生成器，自动 commit）
- **app.schemas**: Pydantic 请求/响应模型
- **app.services**: 业务逻辑服务函数
- **app.tasks**: 后台任务函数（仅 manual trigger 端点直接调用）

## Consumers
- **main.py**: `app.include_router(module.router)` 注册所有路由

## Module Structure
```
routers/
  visitor.py   # POST /api/v1/visitor/register — DB 注入 CRUD
  auth.py      # /api/v1/auth/register|login|me|guest — 注册/登录/JWT/游客 token
  admin.py     # GET /api/v1/admin/dashboard、/report — 纯聚合委托
  knowledge.py # /api/admin/knowledge CRUD + import — X-Admin-Token 认证
  dialogue.py  # POST /api/v1/dialogue/sync-now — 任务触发器（绕过服务层）
  analysis.py  # POST /api/v1/analysis/run-now — 任务触发器
  recommend.py # POST /api/v1/recommend — 个性化路线推荐
```

## DB-Backed Endpoint Pattern

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.domain import EntityCreateRequest, EntityCreateResponse
from app.services.domain_service import create_entity

router = APIRouter(prefix="/api/v1/domain", tags=["中文标签"])

@router.post("/", response_model=EntityCreateResponse, status_code=201)
async def create(
    request: EntityCreateRequest,
    db: AsyncSession = Depends(get_db),  # 会话自动 commit
) -> EntityCreateResponse:
    return await create_entity(db, request)  # 服务直接返回 Pydantic Response
```

## Task-Trigger Endpoint Pattern (有意绕过服务层)

```python
from fastapi import APIRouter
from app.tasks.polling import poll_once

router = APIRouter(prefix="/api/v1/tasks", tags=["运维触发"])

@router.post("/sync-now")
async def sync_now() -> dict[str, int]:
    """手动触发后台任务——无 DB 依赖，无 schema。"""
    count = await poll_once()
    return {"synced_count": count}
```

## Architectural Boundaries
- **NO 业务逻辑** — router 仅做验证+委托，所有逻辑在 service 层
- **NO `commit()` / `rollback()`** — `get_db()` 生成器自动管理事务
- dialogue 和 analysis router 绕过 service 层是**有意设计**——它们是手动触发后台任务的运维端点
- `response_model=` 声明必须与返回类型匹配

<important if="you are adding a new API endpoint">
## Adding a New Endpoint
1. 确保对应的 schema（`schemas/`）和 service（`services/`）已就位
2. 在 `routers/<domain>.py` 中添加端点函数
3. 使用 `APIRouter(prefix="/api/v1/<domain>", tags=["中文标签"])`
4. 在 `main.py` 中 `app.include_router(module.router)` 注册
</important>
