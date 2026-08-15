# ORM Models Layer

## Responsibility
SQLAlchemy 2.0 声明式 ORM 数据模型，映射 SQLite 数据库表结构。所有模型继承 `app.core.database.Base`；建表由 Alembic 迁移负责（启动时 `app/core/migrations.py::run_migrations`，失败回退 create_all），改模型须生成迁移，见 `doc/alembic-migrations.md`。

## Dependencies
- **SQLAlchemy 2.0+**: `Mapped[]` / `mapped_column()` 声明式映射、`AsyncSession`、`aiosqlite` 驱动

## Consumers
- **services/**: 服务函数通过 `select(Model).where(...)` 查询
- **tasks/**: 后台任务直接操作 `async_session_factory()` 会话
- **migrations/env.py**: `from app.models import Base` 供 autogenerate 发现全量模型；**main.py** 经 `run_migrations()` 执行迁移

## Module Structure
```
models/
  visitor.py          # Visitor — 访客实体（String UUID 主键）
  dialogue.py         # Dialogue — 对话消息（自增 int PK，FK→visitors）
  evaluation.py       # Evaluation — AI 评估结果（FK→dialogues, UNIQUE）
  stat.py             # Stat — 聚合统计（独立表，无外键）
  knowledge.py        # KnowledgeBase — 知识库条目（id/title/category/content/tags）
  user.py             # User — 注册用户（username 唯一；登录用户才有长期记忆；2026-08-13 软禁用字段 is_disabled/disabled_at）
  usage.py            # LLMUsage — token 用量记账（C5）
  admin_audit.py      # AdminAudit — 管理员操作审计日志（operator/action/target/detail，账号管理 2026-08-13）
  app_setting.py      # AppSetting — 通用 key-value 配置表（key PK/value/updated_at）+ 数字人形象常量（DIGITAL_HUMAN_CHARACTERS 13 个，2026-08-14，见 AGENTS.md「数字人形象管理」专节）
  analysis_report.py  # AnalysisReport — AI 运营分析概览报告持久化（days 唯一/response_json 快照/analysis_timestamp/created_at/updated_at，2026-08-15 落库持久化，见 AGENTS.md「AI 运营分析」专节）
  __init__.py         # 重导出 Base + 所有模型（使 autogenerate/Base.metadata 发现所有表）
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
- 新增模型文件后必须在 `__init__.py` 注册，否则 autogenerate 检测不到新表；随后按 `doc/alembic-migrations.md` 生成迁移

<important if="you are adding a new database model to this layer">
## Adding a New Model
1. 创建 `models/new_entity.py`，继承 `Base`，定义 `__tablename__`
2. 在 `models/__init__.py` 中添加 `from app.models.new_entity import NewEntity` 并加入 `__all__`
3. 创建对应的 Pydantic schema（`schemas/new_entity.py`）
4. 创建服务函数（`services/new_entity_service.py`）
5. 创建路由端点（`routers/new_entity.py`，在 `main.py` 中注册）
</important>
