"""把按日分区的 parquet 合并成按年分区的 parquet（使用 Pandas 极简安全版）。"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    DATA_DIR, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR,
    AMO_DIR, CONCEPT_PICKS_DIR, LIMIT_UP_STREAK_DIR, setup_logging
)

logger = logging.getLogger("compact")
COMPACT_DIR = DATA_DIR.parent / "data_compact"

TYPE_DIRS = {
    "quote":            QUOTE_DIR,
    "industry_flow":    INDUSTRY_DIR,
    "concept_flow":     CONCEPT_DIR,
    "concept_date":     CONCEPT_DATE_DIR,
    "amo_components":   AMO_DIR,
    "daily_pct":        DATA_DIR / "daily_pct",
    "limit_up_streak":  LIMIT_UP_STREAK_DIR,
    "concept_picks":    CONCEPT_PICKS_DIR,
}

def compact_one(kind: str, keep_days: int = 30) -> None:
    src_dir = TYPE_DIRS.get(kind)
    if not src_dir or not src_dir.exists():
        return

    out_dir = COMPACT_DIR / kind
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 获取所有真正的按日目录
    dates = []
    for p in src_dir.iterdir():
        if p.is_dir() and p.name.startswith("trade_date="):
            d_str = p.name.split("=")[1]
            if len(d_str) == 10 and "-" in d_str:
                if (p / "part.parquet").exists():
                    dates.append(d_str)
    dates = sorted(dates)

    if not dates:
        return

    keep_dates = set(dates[-keep_days:]) if keep_days > 0 else set()
    target_dates = [d for d in dates if d not in keep_dates]

    if not target_dates:
        logger.info("[%s] 所有数据都在保留期内，无需合并", kind)
        return

    # 按年分组
    dates_by_year = {}
    for d in target_dates:
        year = d[:4]
        dates_by_year.setdefault(year, []).append(d)

    for year, year_dates in sorted(dates_by_year.items()):
        year_file = out_dir / f"trade_date={year}" / "part.parquet"
        year_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info("[%s] 合并 %s 年 (%d 个交易日) -> %s", kind, year, len(year_dates), year_file)
        
        # 2. Pandas 读取 + 暴力修复 trade_date
        dfs = []
        for d in year_dates:
            p = src_dir / f"trade_date={d}" / "part.parquet"
            try:
                df = pd.read_parquet(p)
                # 核心修复：直接覆盖整列的值为标准的日期
                df["trade_date"] = pd.to_datetime(d).date()
                dfs.append(df)
            except Exception as e:
                logger.error("读取 %s 失败: %s", p, e)
        
        if not dfs:
            continue
            
        merged = pd.concat(dfs, ignore_index=True)
        
        # 3. 覆盖写入（如果旧文件有错，直接覆盖）
        merged.to_parquet(year_file, index=False, compression="zstd")
        
        # 4. 验证一下写入文件的大小和行数
        logger.info("[%s] %s 年文件生成成功，行数: %d", kind, year, len(merged))

        # 5. 删除热数据
        for d in year_dates:
            shutil.rmtree(src_dir / f"trade_date={d}", ignore_errors=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--type", choices=list(TYPE_DIRS.keys()) + ["all"], default="all")
    p.add_argument("--keep-days", type=int, default=30)
    args = p.parse_args()

    setup_logging()
    kinds = list(TYPE_DIRS) if args.type == "all" else [args.type]
    for kind in kinds:
        compact_one(kind, keep_days=args.keep_days)

if __name__ == "__main__":
    main()