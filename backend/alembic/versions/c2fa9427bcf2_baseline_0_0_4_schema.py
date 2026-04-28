"""baseline 0.0.4 schema

Revision ID: c2fa9427bcf2
Revises:
Create Date: 2026-04-29 01:22:14.543687

设计说明：
    这是引入 Alembic 后的第一份迁移，作用是把**现有 SQLite 数据库**声明为
    "已在 Alembic 管理下"。本身不产生任何 schema 变更。

    用法：
        # 首次引入 Alembic 的现有部署：
        cd backend && .venv/bin/alembic stamp head

        # 新部署（空库）启动：
        # - 现行方案仍由 app.database.Base.metadata.create_all 建表；
        # - 启动后可再 `alembic stamp head` 同步 Alembic 版本；
        # - 或今后改为 `alembic upgrade head`（后续迁移接管）。

    注：autogenerate 时检测到的老表（adj_factor_daily / bars_adj_factor / adj_factors）
    与少量列类型不一致，已作为**未来单独的迁移**处理，不在 baseline 中删除，
    避免首次引入时误 drop 数据。后续用
        alembic revision --autogenerate -m "cleanup legacy adj tables"
    生成专门的清理 revision，review 后再 apply。
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c2fa9427bcf2"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline：现状即 head，不做任何变更。"""
    pass


def downgrade() -> None:
    """Baseline 没有可回退的操作。"""
    pass
