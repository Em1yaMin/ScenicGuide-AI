# FastAPI Application Layer

## Responsibility
应用入口层。`main.py` 负责 FastAPI 应用创建、CORS 中间件、路由注册、后台任务生命周期管理、全局异常处理。

## Dependencies
- **FastAPI**: 应用框架
- **uvicorn**: ASGI 服务器
- **SQLAlchemy 2.0 async**: 数据库引擎与会话

## Consumers
- **uvicorn**: `uvicorn app.main:app` 启动生产服务
- **tests/conftest.py**: 导入 `app` 创建测试客户端

## Lifespan Lifecycle

```
startup:  logging.basicConfig() → Base.metadata.create_all → spawn tasks
runtime:  HTTP serving + background loops
shutdown: cancel tasks → await cleanup → engine.dispose()
```

## Sub-Layer Dependency Flow

```
main.py ──注册路由──→ routers/ ──委托──→ services/ ──查询──→ models/
   │                     │                   │                │
   │                     │                   └──→ dify_client ──→ httpx → Dify API
   │                     │
   ├──启动任务──→ tasks/ ──直接会话──→ models/
   │                                  └──→ dify_client
   │
   └──配置──→ core/ (config.py + database.py)
```

## Global Error Handler (关键设计决策)

```python
@app.exception_handler(Exception)
async def _global_exception_handler(request, exc):
    return JSONResponse(
        status_code=200,  # 始终返回 200，错误信息在 JSON body
        content=ErrorResponse(code=500, message="服务器内部错误").model_dump(),
    )
```

## Architectural Boundaries
- **NO 业务逻辑在 main.py** — 仅做装配（wiring）
- `Base.metadata.create_all` 使用 `IF NOT EXISTS` 语义，可安全重复执行，无 Alembic 迁移
- 后台任务在 `lifespan` 中通过 `asyncio.create_task()` 启动，`done_callback` 记录异常但不重启

## Sub-Layer Guides
- 数据模型 → `models/CLAUDE.md`
- 业务服务 → `services/CLAUDE.md`
- HTTP 路由 → `routers/CLAUDE.md`
- Pydantic Schema → `schemas/CLAUDE.md`
