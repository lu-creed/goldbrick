"""perf: index instrument_meta.asset_type

Revision ID: 8a4c1e0b3f72
Revises: c2fa9427bcf2
Create Date: 2026-04-29

动机：
    数据中心和个股列表两条查询都带 WHERE m.asset_type = 'stock' 这一过滤条件
    （见 backend/app/api/sync.py::data_center 以及
     backend/app/services/daily_universe.py::list_daily_universe）。
    asset_type 在 InstrumentMeta 上原本没有索引 → 每次都全表扫描整张
    instrument_meta。虽然元数据表行数不多（4000+），但查询计划器因此无法
    有效利用与其它大表的 JOIN 顺序优化，造成不必要的开销。

    bars_daily / adj_factors_daily / fundamental_daily 上的 (symbol_id, trade_date)
    复合索引已由 UniqueConstraint 隐式创建，不在本次添加。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "8a4c1e0b3f72"
down_revision: Union[str, Sequence[str], None] = "c2fa9427bcf2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """在 instrument_meta.asset_type 上建一个非唯一索引。"""
    op.create_index(
        "ix_instrument_meta_asset_type",
        "instrument_meta",
        ["asset_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_instrument_meta_asset_type", table_name="instrument_meta")
