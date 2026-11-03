"""补全某股票某一天的 K 线（从 eltdx 拉，追加到 quote）。

用法：
    python scripts/backfill_single_stock_day.py 002693 2026-05-25
    python scripts/backfill_single_stock_day.py 002693 2026-05-25 2026-05-26
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd

from config import QUOTE_DIR


def _to_tdx_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "51", "58", "11", "50")):
        return f"sh{c}"
    if c.startswith(("00", "30", "12", "15", "16", "18", "39")):
        return f"sz{c}"
    if c.startswith(("8", "4", "92")):
        return f"bj{c}"
    return f"sz{c}"


def backfill_one_day(code: str, trade_date: str):
    p = QUOTE_DIR / f"trade_date={trade_date}" / "part.parquet"
    if not p.exists():
        print(f"❌ quote 分区不存在: {p}")
        return False

    df = pd.read_parquet(p)
    existing = df[df["code"] == code]
    if not existing.empty:
        r = existing.iloc[0]
        if r["close"] > 0:
            print(f"ℹ {code} 在 {trade_date} 已有数据 (close={r['close']})，无需补全")
            return True
        else:
            print(f"⚠ {code} 在 {trade_date} 有脏行 (close={r['close']})，将替换")
            df = df[df["code"] != code]

    # eltdx 拉
    from eltdx import TdxClient
    tdx_code = _to_tdx_code(code)
    with TdxClient(timeout=15) as cli:
        s = cli.bars.get(tdx_code, period="day", adjust="qfq", all_pages=True)
        bar = next((b for b in s.bars if b.time.strftime("%Y-%m-%d") == trade_date), None)

    if bar is None:
        print(f"❌ eltdx 无 {code} 在 {trade_date} 的数据（可能停牌）")
        return False

    # 取 name（从已有行或其它日期拿）
    name = ""
    if not existing.empty:
        name = existing.iloc[0].get("name", "")
    else:
        # 从其他日期找 name
        import duckdb
        con = duckdb.connect()
        r = con.execute(f"""
            SELECT name FROM read_parquet('data/quote/trade_date=*/*.parquet',
                                          hive_partitioning=true)
            WHERE code = '{code}' AND name IS NOT NULL LIMIT 1
        """).fetchone()
        if r:
            name = r[0]

    new_row = {
        "code": code,
        "name": name,
        "pct_change": None,
        "industry": None,
        "turnover_rate": None,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume_lots),
        "amount": float(bar.amount),
        "scrplus_90": None, "scrplus_70": None, "hot_rank": None,
        "huge_order_net": None, "big_order_net": None,
        "medium_order_net": None, "small_order_net": None,
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_parquet(p, index=False)
    print(f"✓ 补全 {code} {trade_date}: open={bar.open}, close={bar.close}, "
          f"volume={bar.volume_lots}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python scripts/backfill_single_stock_day.py <code> <date1> [<date2> ...]")
        sys.exit(1)

    code = sys.argv[1]
    dates = sys.argv[2:]
    for d in dates:
        backfill_one_day(code, d)