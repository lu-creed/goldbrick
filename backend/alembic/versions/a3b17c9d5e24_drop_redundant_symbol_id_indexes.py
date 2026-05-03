"""drop redundant symbol_id single-column indexes

Revision ID: a3b17c9d5e24
Revises: c2fa9427bcf2
Create Date: 2026-05-03 12:00:00.000000

背景：
    bars_daily / adj_factors_daily / indicator_pre_daily 三张大表在定义时
    同时有：
        symbol_id: ...index=True             → 生成 ix_*_symbol_id
        UniqueConstraint("symbol_id", ...)   → 生成 uq_* 复合唯一索引
    复合索引按 (symbol_id, trade_date[, adj_mode]) 排序，其"左前缀"
    天然覆盖所有 `WHERE symbol_id = ?` 的查询，因此 ix_*_symbol_id 是
    100% 冗余的额外排序副本。

本迁移：
    删除三个冗余单列索引：
        ix_bars_daily_symbol_id
        ix_adj_factors_daily_symbol_id
        ix_indicator_pre_daily_symbol_id
    保留三个 ix_*_trade_date —— 这些负责"按日期反查全市场"，
    复合唯一索引的左前缀是 symbol_id 而不是 trade_date，不能替代。

预期效果：
    - 写路径：少维护一份索引，INSERT/UPDATE 略快
    - 读路径：按 symbol_id 过滤的查询会改用 UniqueConstraint 的复合索引，
      性能不降反升（覆盖列更多）
    - 磁盘：三个索引每个约 0.4-0.8 GB，合计 1.5-2.5 GB 页进入 freelist。
      文件**不会立刻变小**，SQLite 会在后续 INSERT 时复用这些页。

风险：极低。UniqueConstraint 不受影响，唯一性校验仍由 uq_* 兜底。

回滚：downgrade 重建三个索引即可。无数据损失风险。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a3b17c9d5e24"
down_revision: Union[str, Sequence[str], None] = "c2fa9427bcf2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除三个冗余单列 symbol_id 索引。SQLite 和 PostgreSQL 都支持 DROP INDEX，无需 batch 模式。"""
    op.drop_index("ix_bars_daily_symbol_id", table_name="bars_daily")
    op.drop_index("ix_adj_factors_daily_symbol_id", table_name="adj_factors_daily")
    op.drop_index("ix_indicator_pre_daily_symbol_id", table_name="indicator_pre_daily")


def downgrade() -> None:
    """重建三个索引。反向顺序只是整洁，与功能无关。"""
    op.create_index("ix_indicator_pre_daily_symbol_id", "indicator_pre_daily", ["symbol_id"])
    op.create_index("ix_adj_factors_daily_symbol_id", "adj_factors_daily", ["symbol_id"])
    op.create_index("ix_bars_daily_symbol_id", "bars_daily", ["symbol_id"])
