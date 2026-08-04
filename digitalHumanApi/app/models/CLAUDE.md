# ORM Models Layer

## Responsibility
SQLAlchemy 2.0 声明式 ORM 数据模型，映射 SQLite 数据库表结构。所有模型继承 `app.core.database.Base`，通过 `Base.metadata.create_all` 在应用启动时自动建表。

## Dependencies
- **SQLAlchemy 2.0+**: `Mapped[]` / `mapped_column()` 声明式映射、`AsyncSession`、`aiosqlite` 驱动

## Consumers
- **services/**: 服务函数通过 `select(Model).where(...)` 查询
- **tasks/**: 后台任务直接操作 `async_session_factory()` 会话
- **main.py**: `from app.models import Base` 用于 `create_all` 建表

## Module Structure
```
models/
  visitor.py          # Visitor — 访客实体（String UUID 主键）
  dialogue.py         # Dialogue — 对话消息（自增 int PK，FK→visitors）
  evaluation.py       # Evaluation — AI 评估结果（FK→dialogues, UNIQUE）
  stat.py             # Stat — 聚合统计（独立表，无外键）
  knowledge.py        # KnowledgeBase — 知识库条目（id/title/category/content/tags）
  user.py             # User — 注册用户（username 唯一；登录用户才有长期记忆）
  __init__.py         # 重导出 Base + 所有模型（使 create_all 发现所有表）
```

## Declarative Model Pattern

```python
from __future__ import annotations  # PEP 563 延迟求值，支持字符串前向引用

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:  # 仅类型检查时导入，避免循环依赖
    from app.models.related import RelatedModel


class Entity(Base):
    __tablename__ = "entities"  # 表名：小写复数

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增 PK

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)

    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("parents.id"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)  # lambda 延迟求值
    )

    # 1:N 双向关系
    children: Mapped[list["Child"]] = relationship(back_populates="parent")

    # 1:1 关系（uselist=False + FK 上 unique=True）
    profile: Mapped[Optional["Profile"]] = relationship(
        back_populates="entity", uselist=False
    )
```

## PK 选择约定
- **自增 int**: 服务端生成的实体（Dialogue、Evaluation、Stat）
- **String(36) UUID**: 外部系统提供的自然键（Visitor）

## Architectural Boundaries
- **NO `commit()` in models** — 模型文件仅定义结构，不包含任何数据库操作
- **NO 跨模型业务逻辑** — 模型之间仅通过 `relationship` 连接
- 新增模型文件后必须在 `__init__.py` 注册，否则 `create_all` 不会建表

<important if="you are adding a new database model to this layer">
## Adding a New Model
1. 创建 `models/new_entity.py`，继承 `Base`，定义 `__tablename__`
2. 在 `models/__init__.py` 中添加 `from app.models.new_entity import NewEntity` 并加入 `__all__`
3. 创建对应的 Pydantic schema（`schemas/new_entity.py`）
4. 创建服务函数（`services/new_entity_service.py`）
5. 创建路由端点（`routers/new_entity.py`，在 `main.py` 中注册）
</important>
