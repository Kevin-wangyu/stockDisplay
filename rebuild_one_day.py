# 新建 rebuild_one_day.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import numpy as np
import pandas as pd
import duckdb
from config import setup_logging, QUOTE_DIR
setup_logging()

TARGET = sys.argv[1] if len(sys.argv) > 1 else "2026-10-23"

from queries import (
    DAILY_PCT_DIR, QUOTE_GLOB, _list_partition_dates, _q_df,
)

all_td = _list_partition_dates(QUOTE_DIR)
if TARGET not in all_td:
    print(f"✗ {TARGET} 不在 quote 分区里")
    sys.exit(1)

i = all_td.index(TARGET)
if i == 0:
    print(f"✗ {TARGET} 是第一个交易日，无法计算 prev_close")
    sys.exit(1)
prev_d = all_td[i - 1]
print(f"TARGET={TARGET}  prev={prev_d}")

raw = _q_df(
    f"""
    SELECT code, trade_date, close
    FROM read_parquet('{QUOTE_GLOB}', hive_partitioning=true, union_by_name=true)
    WHERE trade_date IN (?, ?)
    """, [TARGET, prev_d],
)
raw["trade_date"] = raw["trade_date"].astype(str).str.slice(0, 10)
raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
print(f"读到 {len(raw)} 行, 日期分布:\n{raw['trade_date'].value_counts()}")

cur  = raw[raw["trade_date"] == TARGET][["code", "close"]].rename(columns={"close": "cur_close"})
prev = raw[raw["trade_date"] == prev_d][["code", "close"]].rename(columns={"close": "prev_close"})
print(f"cur: {len(cur)} 行   prev: {len(prev)} 行")

m = cur.merge(prev, on="code", how="left")
m["pct_change"] = (m["cur_close"] - m["prev_close"]) / m["prev_close"] * 100.0
print(f"pct_change NaN: {m['pct_change'].isna().sum()} / {len(m)}")

# 抽样验证
sample = m.sample(min(5, len(m)), random_state=0)
print(sample[["code", "cur_close", "prev_close", "pct_change"]].to_string(index=False))

out = m[["code", "pct_change"]].copy()
out["trade_date"] = TARGET
part = DAILY_PCT_DIR / f"trade_date={TARGET}"
part.mkdir(parents=True, exist_ok=True)
out.to_parquet(part / "part.parquet", index=False)
print(f"\n✓ 已写入 {part / 'part.parquet'}  ({len(out)} 行)")