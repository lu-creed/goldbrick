"""add strategy notes column

Revision ID: 3c7d82f1a9b4
Revises: 8a4c1e0b3f72
Create Date: 2026-04-29

动机:
    给 Strategy 模型加 notes 字段(Markdown 格式的研究笔记),
    与 description 区分:description 是列表页展示用的简短说明,
    notes 是可长篇 Markdown 的用户私有研究记录。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "3c7d82f1a9b4"
down_revision: Union[str, Sequence[str], None] = "8a4c1e0b3f72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """给 strategies 表加 notes 列(可空,Text 类型)。使用 batch 模式兼容 SQLite。"""
    with op.batch_alter_table("strategies") as batch:
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("strategies") as batch:
        batch.drop_column("notes")
