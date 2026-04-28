#!/usr/bin/env python3
"""
SQLite → PostgreSQL 数据迁移脚本骨架（0.0.4-dev 预案，实际切换前请在 staging 跑通）。

使用前提：
  1. 目标 Postgres 已创建空库，账号有 CREATE/INSERT 权限。
  2. 本机安装 psycopg2 或 psycopg：`pip install psycopg2-binary`。
  3. backend/ 目录下已设置两个环境变量：
       SQLITE_URL=sqlite:////absolute/path/to/backend/data/app.db
       POSTGRES_URL=postgresql+psycopg://user:pass@host:5432/goldbrick

运行：
    cd backend
    .venv/bin/python ../scripts/migrate_sqlite_to_postgres.py --dry-run    # 看要复制多少行
    .venv/bin/python ../scripts/migrate_sqlite_to_postgres.py              # 真实复制

步骤概览：
  1. 用 SQLAlchemy 反射读 SQLite 的表结构
  2. 在 Postgres 建相同 schema（通过 ORM Base.metadata.create_all）
  3. 按表顺序（父表→子表）批量 INSERT 数据
  4. 最后把 Postgres 的序列重置到 max(id) + 1（SQLAlchemy 不会替我们做）

已知局限：
  - 不处理循环外键（当前 schema 没有）
  - 不把 SQLite 的 BOOLEAN（0/1）强转为 PG 的 true/false（SQLAlchemy 的 Boolean 列类型会帮我们转）
  - 大表（如 bars_daily 百万行）建议加 --batch-size 分批 commit，避免事务过大
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本能从 backend/ 目录导入 app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base  # noqa: E402
# 关键：导入 models 模块让所有 ORM 类注册到 Base.metadata
from app import models  # noqa: F401,E402


# 迁移顺序：父表在前，子表在后（按外键依赖）
TABLE_ORDER = [
    "users",
    "symbols",
    "instrument_meta",
    "indicators",
    "indicator_params",
    "indicator_sub_indicators",
    "user_indicators",
    "bars_daily",
    "adj_factors_daily",
    "indicator_pre_daily",
    "sync_jobs",
    "sync_runs",
    "app_settings",
    "fundamental_daily",
    "screening_history",
    "backtest_records",
    "dav_stock_watch",
    "watchlist",
    "auto_update_config",
    "auto_update_logs",
]


def copy_table(src: Session, dst: Session, table_name: str, batch: int) -> int:
    """从 src 表读所有行并 INSERT 到 dst 表。返回复制行数。"""
    rows = src.execute(text(f"SELECT * FROM {table_name}")).mappings().all()
    if not rows:
        return 0
    # 按 batch 分段 executemany 插入
    keys = list(rows[0].keys())
    col_list = ", ".join(keys)
    placeholder = ", ".join(f":{k}" for k in keys)
    sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholder})")
    n = 0
    for i in range(0, len(rows), batch):
        chunk = [dict(r) for r in rows[i : i + batch]]
        dst.execute(sql, chunk)
        n += len(chunk)
        dst.commit()
    return n


def reset_pg_sequences(dst_engine) -> None:
    """把 Postgres 每张表的主键序列重置到 max(id) + 1。

    create_all 建表时 SERIAL/IDENTITY 会从 1 开始，但我们已手工插入带 id 的历史数据，
    不重置序列会导致下一次 INSERT 主键冲突。
    """
    insp = inspect(dst_engine)
    with dst_engine.begin() as conn:
        for t in insp.get_table_names():
            # 只处理有 id 自增主键的表
            pks = insp.get_pk_constraint(t).get("constrained_columns", [])
            if pks != ["id"]:
                continue
            seq = f"{t}_id_seq"
            try:
                conn.execute(
                    text(
                        f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {t}), 1))"
                    )
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] reset sequence {seq} failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只打印每表行数，不写入目标库")
    ap.add_argument("--batch-size", type=int, default=1000, help="每批 INSERT 行数（默认 1000）")
    args = ap.parse_args()

    sqlite_url = os.environ.get("SQLITE_URL")
    pg_url = os.environ.get("POSTGRES_URL")
    if not sqlite_url or not pg_url:
        sys.exit("请设置环境变量 SQLITE_URL 和 POSTGRES_URL")

    src_engine = create_engine(sqlite_url)
    dst_engine = create_engine(pg_url)

    # 1. 在 Postgres 创建 schema（表结构按 models.py 定义）
    if not args.dry_run:
        print(">> creating schema on postgres")
        Base.metadata.create_all(bind=dst_engine)

    # 2. 逐表复制
    with Session(src_engine) as src, Session(dst_engine) as dst:
        total = 0
        for t in TABLE_ORDER:
            try:
                cnt = src.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
            except Exception:
                print(f"  [skip] {t} not in source")
                continue
            print(f"  {t}: {cnt} rows", end="")
            if args.dry_run:
                print(" (dry-run)")
                total += cnt
                continue
            n = copy_table(src, dst, t, args.batch_size)
            print(f" → copied {n}")
            total += n
        print(f"== total rows: {total}")

    # 3. 重置 Postgres 主键序列
    if not args.dry_run:
        print(">> resetting postgres sequences")
        reset_pg_sequences(dst_engine)
        print(">> done. 切换 backend/.env 的 DATABASE_URL 即可启用 Postgres。")


if __name__ == "__main__":
    main()
