"""添加用户禁用字段（账号管理，2026-08-13）

Revision ID: b4c3d2e1f0a9
Revises: c5f1a2b3d4e5
Create Date: 2026-08-13 10:00:00.000000

为 users 表新增软禁用字段：
- is_disabled：是否禁用（默认 0=启用；server_default 保证遗留库加列时
  历史行自动置为启用，不因 NOT NULL 加列失败）
- disabled_at：禁用时间（可空）

实现说明：
- 用 SQLite 原生 ADD COLUMN（不触发 batch 表重建——batch 经临时表 + RENAME
  会改写 sqlite_master 的 CREATE TABLE 语句（带引号），破坏
  tests/test_migrations.py 的 DDL 等价性验收）；ADD COLUMN 按执行顺序追加列，
  先加 is_disabled 再加 disabled_at，最终列序与 ORM metadata
  （app/models/user.py）逐字节一致。
- 防御性列存在性检查（幂等）：遗留库路径（app/core/migrations.py 的
  create_all + stamp 上一版本 + upgrade）中，新形状遗留库（create_all
  产物已含新列）跳过加列、旧形状遗留库（历史 create_all 产物）正常加列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c3d2e1f0a9'
down_revision: Union[str, None] = 'c5f1a2b3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("users")}
    # 先加 is_disabled（NOT NULL + DEFAULT 0，历史行自动置为启用），
    # 再加 disabled_at（可空）——列序与 ORM metadata 一致
    if "is_disabled" not in existing:
        op.add_column(
            'users',
            sa.Column(
                'is_disabled',
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )
    if "disabled_at" not in existing:
        op.add_column(
            'users', sa.Column('disabled_at', sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    # SQLite 删列必须 batch 重建（downgrade 产物不做 DDL 等价性校验，
    # 回退语义正确即可）；防御性：列不存在（如历史半迁移状态）时跳过
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("users")}
    with op.batch_alter_table('users', schema=None) as batch_op:
        if "is_disabled" in existing:
            batch_op.drop_column('is_disabled')
        if "disabled_at" in existing:
            batch_op.drop_column('disabled_at')
