"""添加 app_settings 通用配置表

Revision ID: d1e2f3a4b5c6
Revises: a9f0e1d2c3b4
Create Date: 2026-08-14 10:00:00.000000

**防御性（幂等）写法**（2026-08-13 账号管理任务约定）：遗留库路径
（create_all + stamp 基线 + upgrade head）会重放本迁移，create_all 已按
当前 metadata 建好的表须跳过，否则重复 DDL 报错。空库/已版本化库路径
行为不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'a9f0e1d2c3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 防御性建表（幂等）：遗留库路径重放本迁移时跳过已存在的对象
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "app_settings" not in tables:
        op.create_table('app_settings',
        sa.Column('key', sa.String(length=128), nullable=False),
        sa.Column('value', sa.String(length=512), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key')
        )


def downgrade() -> None:
    op.drop_table('app_settings')
