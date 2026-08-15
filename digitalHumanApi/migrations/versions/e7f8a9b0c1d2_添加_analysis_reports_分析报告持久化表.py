"""添加 analysis_reports 分析报告持久化表

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3a4b5c6
Create Date: 2026-08-15 09:00:00.000000

**防御性（幂等）写法**（2026-08-13 账号管理任务约定）：遗留库路径
（create_all + stamp 基线 + upgrade head）会重放本迁移，create_all 已按
当前 metadata 建好的表须跳过，否则重复 DDL 报错。空库/已版本化库路径
行为不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 防御性建表（幂等）：遗留库路径重放本迁移时跳过已存在的对象
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "analysis_reports" not in tables:
        op.create_table('analysis_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('response_json', sa.Text(), nullable=False),
        sa.Column('analysis_timestamp', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('days')
        )


def downgrade() -> None:
    op.drop_table('analysis_reports')
