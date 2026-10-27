"""把按日分区的 parquet 合并成按年分区的 parquet。

用法
----
    python -m etl.compact                  # 合并所有类
    python -m etl.compact --type stock     # 只合并个股

行为
----
    输入:  data/quote/trade_date=YYYY-MM-DD/part.parquet (每天一个)
    输出:  data_compact/quote/trade_date=YYYY/part.parquet (每年一个)

    原分区目录不动，合并结果放在新的 data_compact/ 下。
    查询层只需把 glob 从 data/ 改成 data_compact/。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

sys.path.append(str(Path(__file__).parent.parent))
from config import DATA_DIR, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, setup_logging

logger = logging.getLogger("compact")

COMPACT_DIR = DATA_DIR.parent / "data_compact"

TYPE_DIRS = {
    "stock":    QUOTE_DIR,
    "industry": INDUSTRY_DIR,
    "concept":  CONCEPT_DIR,
}


def compact_one(kind: str) -> None:
    src_dir = TYPE_DIRS[kind]
    out_dir = COMPACT_DIR / kind
    out_dir.mkdir(parents=True, exist_ok=True)

    src_glob = str(src_dir / "**" / "*.parquet")
    logger.info("[%s] 扫描 %s", kind, src_glob)

    con = duckdb.connect()

    years = con.execute(f"""
        SELECT DISTINCT EXTRACT(YEAR FROM trade_date) AS y
        FROM read_parquet('{src_glob}', hive_partitioning=true)
        ORDER BY y
    """).fetchall()

    if not years:
        logger.warning("[%s] 无数据", kind)
        return

    for (year,) in years:
        year_dir = out_dir / f"trade_date={int(year)}"
        year_dir.mkdir(parents=True, exist_ok=True)
        out_file = year_dir / "part.parquet"

        con.execute(f"""
            COPY (
                SELECT *
                FROM read_parquet('{src_glob}', hive_partitioning=true)
                WHERE EXTRACT(YEAR FROM trade_date) = {int(year)}
                ORDER BY trade_date, code
            ) TO '{out_file}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_file}')").fetchone()[0]
        size_mb = out_file.stat().st_size / 1e6
        logger.info("[%s] %s 年: %d 行, %.1f MB -> %s", kind, int(year), n, size_mb, out_file)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--type", choices=["stock", "industry", "concept", "all"], default="all")
    args = p.parse_args()

    setup_logging()
    kinds = list(TYPE_DIRS) if args.type == "all" else [args.type]
    for kind in kinds:
        compact_one(kind)


if __name__ == "__main__":
    main()