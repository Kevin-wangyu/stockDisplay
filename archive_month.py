"""按月归档行情数据到 data_compact/。

用法：
    python archive_month.py                    # 全量归档（所有月份）
    python archive_month.py --dry-run          # 只打印
    python archive_month.py --kind quote       # 只归档 quote
    python archive_month.py --rebuild-current  # 只重跑当月+上月（增量）

日分区 data/{kind}/trade_date=*/ 全部保留，月合并只是额外的查询加速副本。
"""
import argparse
import sys
import time
from pathlib import Path
from datetime import date

sys.path.append(str(Path(__file__).parent))

import duckdb
from config import (
    DATA_DIR, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR, DAILY_PCT_DIR,
)
import logging
logger = logging.getLogger(__name__)
COMPACT_ROOT = DATA_DIR.parent / "data_compact"

KINDS = {
    "quote":         QUOTE_DIR,
    "industry_flow": INDUSTRY_DIR,
    "concept_flow":  CONCEPT_DIR,
    "concept_date":  CONCEPT_DATE_DIR,
    "daily_pct":     DAILY_PCT_DIR,
}


def _list_days(kind_dir: Path) -> list[str]:
    if not kind_dir.exists():
        return []
    out = []
    for p in kind_dir.iterdir():
        if p.is_dir() and p.name.startswith("trade_date="):
            d = p.name.split("=", 1)[1]
            if len(d) == 10 and d[4] == "-" and (p / "part.parquet").exists():
                out.append(d)
    return sorted(out)


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        return f"{y + 1}-01"
    return f"{y}-{m + 1:02d}"


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def rebuild_month(kind: str, kind_dir: Path, month: str, con, dry_run=False) -> bool:
    """重建某个月份的合并文件（覆盖）。"""
    out_root = COMPACT_ROOT / kind
    out_root.mkdir(parents=True, exist_ok=True)
    out_file = out_root / f"{month}.parquet"

    if dry_run:
        print(f"[{kind}] {month} → {out_file}")
        return True

    src_glob = str(kind_dir / "trade_date=*" / "part.parquet").replace("\\", "/")
    next_m = _next_month(month)
    out_path = str(out_file).replace("\\", "/")

    try:
        con.execute(f"""
            COPY (
                SELECT * EXCLUDE (trade_date),
                       CAST(trade_date AS VARCHAR) AS trade_date
                FROM read_parquet(
                    '{src_glob}',
                    hive_partitioning=true,
                    union_by_name=true
                )
                WHERE CAST(trade_date AS VARCHAR) >= '{month}-01'
                  AND CAST(trade_date AS VARCHAR) <  '{next_m}-01'
                ORDER BY trade_date, code
            ) TO '{out_path}' (FORMAT PARQUET)
        """)
        logger.info(f"[{kind}] {month} 完成 → {out_file.name}")
        return True
    except Exception as e:
        print(f"[{kind}] {month} 失败: {e}")
        return False


def archive_kind(kind: str, kind_dir: Path, con, rebuild_current=False, dry_run=False) -> int:
    if rebuild_current:
        # 只重跑当月 + 上月（覆盖最新数据）
        today = date.today()
        cur_month = today.strftime("%Y-%m")
        prev_m = _prev_month(cur_month)
        months = [prev_m, cur_month]
    else:
        # 全量：所有月份
        all_days = _list_days(kind_dir)
        if not all_days:
            print(f"[{kind}] 无日分区，跳过")
            return 0
        months = sorted(set(d[:7] for d in all_days))

    n = 0
    for m in months:
        if rebuild_month(kind, kind_dir, m, con, dry_run):
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="all",
                    choices=["all"] + list(KINDS.keys()))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebuild-current", action="store_true",
                    help="只重跑当月+上月（供 serve.py 增量用）")
    args = ap.parse_args()

    kinds = list(KINDS.keys()) if args.kind == "all" else [args.kind]

    con = duckdb.connect()
    t0 = time.time()
    total = 0
    for kind in kinds:
        kind_dir = KINDS[kind]
        if not kind_dir.exists():
            print(f"[{kind}] 目录不存在: {kind_dir}")
            continue
        total += archive_kind(kind, kind_dir, con,
                              rebuild_current=args.rebuild_current,
                              dry_run=args.dry_run)

    print(f"\n完成：{total} 个月，耗时 {time.time() - t0:.1f}s")
    print(f"输出目录：{COMPACT_ROOT}")


if __name__ == "__main__":
    main()