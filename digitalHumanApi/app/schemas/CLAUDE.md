# Pydantic Schemas Layer

## Responsibility
Pydantic v2 请求/响应数据模型，定义 API 输入验证和输出序列化契约。纯数据形状定义，不包含业务逻辑。

## Dependencies
- **Pydantic v2.9+**: `BaseModel`、`model_config`、`from_attributes`
- 不依赖 ORM 层 — schema 独立于数据库模型

## Consumers
- **routers/**: 用作 `response_model=` 参数和请求体类型注解
- **services/**: admin_service 直接构建返回 Pydantic 响应模型
- **main.py**: `ErrorResponse` 用于全局异常处理器

## Module Structure
```
schemas/
  common.py     # ErrorResponse — 统一错误信封（跨域共享）
  health.py     # HealthResponse — 健康检查
  visitor.py    # VisitorRegisterRequest / VisitorRegisterResponse
  admin.py      # DashboardResponse、ReportResponse + 7 个嵌套 Item 类
  knowledge.py  # KnowledgeCreateRequest/UpdateRequest/Item/ListResponse/ImportResponse
  dialogue.py   # DialogueItem — 预留未来对话列表 API
  analysis.py   # AnalysisResultItem — 预留未来分析结果 API
  recommend.py  # RecommendRequest / RecommendResponse + RouteRecommendation
  auth.py       # RegisterRequest / LoginRequest / UserResponse / AuthResponse / GuestResponse
```

## Request/Response Pair Pattern

```python
from pydantic import BaseModel
from typing import Optional

# 请求 schema — 无 from_attributes
class EntityCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None

# 响应 schema — 带 from_attributes 支持 ORM 映射
class EntityCreateResponse(BaseModel):
    id: int
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}
```

## Composed Response Pattern (推荐用于聚合端点)

服务层直接构建并返回 Pydantic Response 模型，router 层零转换：

```python
# Item 子 schema — 无 from_attributes（手动构建）
class TrendItem(BaseModel):
    date: str
    positive: int

# 顶层 Response — 组合 Item 列表
class DashboardResponse(BaseModel):
    trend: list[TrendItem]
    summary: dict[str, int]

    model_config = {"from_attributes": True}
```

## Architectural Boundaries
- **NO 自定义 validators** — 仅依赖类型注解验证，业务验证在 service 层
- **NO `Field()` 约束** — 不使用 `Field(ge=, min_length=)` 等
- **NO ORM 依赖** — schema 不导入 models
- 响应 schema 统一加 `from_attributes = True`
- `Optional[X] = None` 用于可空字段，不用 `Field(None)`

<important if="you are adding a new schema">
## Adding a New Schema
1. 创建或扩展 `schemas/<domain>.py`
2. 命名约定：`XxxRequest`（输入）、`XxxResponse`（输出）、`XxxItem`（嵌套列表项）
3. 新服务应直接返回 Pydantic Response 模型（见 services CLAUDE.md）
4. router 中通过 `response_model=XxxResponse` 声明
</important>
