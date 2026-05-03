"""columnar indicator_pre_daily schema

Revision ID: 7f0d3e8a9c41
Revises: a3b17c9d5e24
Create Date: 2026-05-03 12:30:00.000000

背景：
    旧 indicator_pre_daily 用单列 payload TEXT 存 JSON，17.4M 行每行 ~500 字节，
    总约 8-10 GB，是全库最大单表消耗。JSON 编码把指标名（"MA5"/"KDJ_K"/...）
    在每一行重复一遍，字段冗余，占空间又慢。

    本迁移把 payload 拆成 ~45 个独立 REAL 列（SQLite = 8 字节 IEEE754）。
    单行从 ~500 字节降到 ~180-300 字节（未填的列 SQLite 记为 1 字节 null），
    预计省 5-7 GB。所有读写速度都变快（省 json.dumps / json.loads）。

策略：
    因为 SQLite 不支持 ALTER COLUMN 改类型，且要从 1 列变 45 列，
    最干净的做法是 **DROP TABLE + CREATE TABLE**。

    迁移后 indicator_pre_daily 是空表 —— 需要**手动**跑 rebuild：
        scripts/rebuild_indicator_pre_cache.py --mode both
    或调用 admin API：
        POST /api/admin/indicator-pre/rebuild  (body: {"adj_modes": ["qfq","hfq"]})

    重建耗时约 20-40 分钟（5500 股 × 2 口径）。期间服务可起着，
    读路径 load_indicator_map_from_pre 返回 None 会自动 fallback 现算。

磁盘峰值：
    DROP 旧表 → 约 10-11 GB 页进入 SQLite freelist（.db 文件物理大小不变）
    CREATE 空表 → 0 新增
    rebuild 写数据 → 自动复用 freelist 页，文件不增长
    峰值 = 当前 29 GB，**不需要额外磁盘空间**（对比 VACUUM 需要 47 GB）。

回滚：
    downgrade 重建旧 payload TEXT schema 但**数据无法恢复**（旧 payload 已丢）。
    若上线后出问题，回滚完要重新跑 rebuild，但用的是旧 JSON 写入路径（服务器上
    需要先回滚到上一个 git commit 才能拿到旧 indicator_precompute.py）。
    实际上线出问题走 git revert 而不是 alembic downgrade 更稳。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7f0d3e8a9c41"
down_revision: Union[str, Sequence[str], None] = "a3b17c9d5e24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 所有指标列名（与 models.IndicatorPreDaily 和 indicator_precompute.INDICATOR_COLUMN_MAP
# 必须严格一致，以本文件为准复核）
_INDICATOR_COLUMNS: tuple[str, ...] = (
    # OHLCV
    "close", "open", "high", "low", "volume", "turnover_rate",
    # MA
    "ma5", "ma10", "ma20", "ma30", "ma60",
    # EXPMA
    "expma12", "expma26",
    # MACD
    "dif", "dea", "macd_bar",
    # KDJ
    "kdj_k", "kdj_d", "kdj_j",
    # BOLL
    "boll_mid", "boll_upper", "boll_lower",
    # RSI
    "rsi6", "rsi12", "rsi24",
    # ATR
    "atr14", "atr14_pct",
    # WR
    "wr6", "wr10",
    # CCI
    "cci14",
    # BIAS
    "bias6", "bias12", "bias24",
    # ROC
    "roc6", "roc12",
    # PSY
    "psy12",
    # VMA
    "vma5", "vma10", "vma20",
    # OBV
    "obv",
    # DMA
    "dma", "ddma",
    # TRIX
    "trix12", "trma",
    # DMI
    "pdi", "mdi", "adx",
    # STDDEV
    "stddev10", "stddev20",
    # ARBR
    "ar", "br",
)


def upgrade() -> None:
    """DROP 旧 indicator_pre_daily，CREATE 列式新表。

    SQLite 上 DROP TABLE 会自动删除所有关联索引（ix_*、uq_*）和外键约束。
    PostgreSQL 上 DROP TABLE ... CASCADE 也一样，但这里没用 CASCADE 因为
    我们确认没有 view/FK 指向这张表（只有 IndicatorPreDaily.symbol_id 指向 symbols）。
    """
    # ---- 1. 删除旧表（及其所有索引/约束）----
    op.drop_table("indicator_pre_daily")

    # ---- 2. 建新列式表 ----
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adj_mode", sa.String(length=8), nullable=False, server_default="none"),
    ]
    # 所有指标列统一为 nullable Float
    for col_name in _INDICATOR_COLUMNS:
        columns.append(sa.Column(col_name, sa.Float(), nullable=True))

    op.create_table(
        "indicator_pre_daily",
        *columns,
        sa.UniqueConstraint("symbol_id", "trade_date", "adj_mode", name="uq_indicator_pre_symbol_date_adj"),
    )

    # ---- 3. 建 trade_date 索引（按日期反查用；symbol_id 故意不建，由 unique 前缀覆盖）----
    op.create_index("ix_indicator_pre_daily_trade_date", "indicator_pre_daily", ["trade_date"])


def downgrade() -> None:
    """反向：重建旧 payload JSON schema。数据不恢复（需重跑 rebuild）。"""
    op.drop_table("indicator_pre_daily")
    op.create_table(
        "indicator_pre_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adj_mode", sa.String(length=8), nullable=False, server_default="none"),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("symbol_id", "trade_date", "adj_mode", name="uq_indicator_pre_symbol_date_adj"),
    )
    op.create_index("ix_indicator_pre_daily_trade_date", "indicator_pre_daily", ["trade_date"])
