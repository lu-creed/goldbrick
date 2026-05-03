#!/usr/bin/env python3
"""
P1a：列式化 indicator_pre_daily 表后的缓存重建脚本。

背景：
    alembic 7f0d3e8a9c41 把 indicator_pre_daily 从 JSON payload 改为 51 个 REAL 列，
    过程是 DROP TABLE + CREATE TABLE，**旧数据丢失**。
    本脚本用来把 17M 行重新算出来写回新表。

运行：
    cd backend
    .venv/bin/python ../scripts/rebuild_indicator_pre_cache.py --mode both
    # 或指定若干只股票：
    .venv/bin/python ../scripts/rebuild_indicator_pre_cache.py --ts-codes 600000.SH,000001.SZ --mode qfq
    # 干跑（只打印要处理多少只，不实际写入）：
    .venv/bin/python ../scripts/rebuild_indicator_pre_cache.py --dry-run

预计耗时（全市场 × 双口径）：
    ~5500 股 × 2 口径 × 平均 80-150ms/股 = 15-30 分钟（线上服务器）。
    脚本打印 ETA，每处理 50 只刷一行进度。
    期间服务可以起着，因为 load_indicator_map_from_pre 目前未被调用。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 让本脚本能 import 到 app.*（脚本可能从任意目录启动）
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import Symbol  # noqa: E402
from app.services.indicator_precompute import rebuild_indicator_pre_for_symbol  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild indicator_pre_daily after P1a columnar migration")
    p.add_argument(
        "--mode",
        choices=("qfq", "hfq", "both"),
        default="both",
        help="复权口径：qfq / hfq / both（默认 both，与生产 sync_runner 一致）",
    )
    p.add_argument(
        "--ts-codes",
        type=str,
        default=None,
        help="仅处理这些标的（逗号分隔，如 600000.SH,000001.SZ）。不给就处理全市场。",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要处理的标的数和每只的 id/ts_code，不写入数据库",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="每处理 N 只打印一行进度（默认 50）",
    )
    return p.parse_args()


def _fmt_eta(remaining_s: float) -> str:
    if remaining_s < 60:
        return f"{int(remaining_s)}s"
    if remaining_s < 3600:
        return f"{int(remaining_s / 60)}m{int(remaining_s) % 60}s"
    h = int(remaining_s / 3600)
    m = int((remaining_s % 3600) / 60)
    return f"{h}h{m}m"


def main() -> int:
    args = _parse_args()
    modes: tuple[str, ...] = ("qfq", "hfq") if args.mode == "both" else (args.mode,)

    db = SessionLocal()
    try:
        # 选择要处理的 Symbol 列表
        q = db.query(Symbol).order_by(Symbol.ts_code.asc())
        if args.ts_codes:
            codes = [c.strip().upper() for c in args.ts_codes.split(",") if c.strip()]
            q = q.filter(Symbol.ts_code.in_(codes))
        symbols = q.all()
        n_symbols = len(symbols)

        if n_symbols == 0:
            print("无匹配标的，退出")
            return 1

        print(f"目标标的数: {n_symbols}")
        print(f"复权口径  : {modes}")
        print(f"总工作量  : {n_symbols * len(modes)} 次 rebuild（每次 ~80-150ms）")

        if args.dry_run:
            for s in symbols[:20]:
                print(f"  [dry-run] id={s.id} ts_code={s.ts_code} name={s.name or '-'}")
            if n_symbols > 20:
                print(f"  ... 余 {n_symbols - 20} 只未列出")
            return 0

        # 正式重建
        t0 = time.monotonic()
        done = 0
        failed: list[tuple[str, str]] = []
        total_rows = 0
        per_mode_rows: dict[str, int] = {m: 0 for m in modes}

        for idx, sym in enumerate(symbols, start=1):
            try:
                for mode in modes:
                    n = rebuild_indicator_pre_for_symbol(db, sym.id, mode)
                    total_rows += n
                    per_mode_rows[mode] += n
                done += 1
            except Exception as ex:  # noqa: BLE001
                # 单只失败不中止 —— 脚本完成后统一打印失败列表
                failed.append((sym.ts_code, str(ex)))

            if idx % args.progress_every == 0 or idx == n_symbols:
                elapsed = time.monotonic() - t0
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (n_symbols - idx) / rate if rate > 0 else 0
                print(
                    f"[{idx}/{n_symbols}] ok={done} fail={len(failed)} "
                    f"rows_total={total_rows} "
                    f"rate={rate:.1f}股/s eta={_fmt_eta(eta)}"
                )

        # 最终摘要
        total_elapsed = time.monotonic() - t0
        print("-" * 60)
        print(f"完成：耗时 {_fmt_eta(total_elapsed)}")
        print(f"成功 {done} / 失败 {len(failed)} / 共 {n_symbols} 只")
        print(f"各口径写入行数：{per_mode_rows}")
        print(f"合计写入：{total_rows} 行")
        if failed:
            print(f"\n失败列表（前 20）：")
            for ts_code, msg in failed[:20]:
                print(f"  {ts_code}: {msg[:120]}")
            if len(failed) > 20:
                print(f"  ... 余 {len(failed) - 20} 只未列出")
            return 2  # 非零表示部分失败
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
