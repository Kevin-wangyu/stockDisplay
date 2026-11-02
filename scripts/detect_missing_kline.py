"""检测 quote 分区里缺失的 K 线。

对比交易日历，找出：
  - 整天缺失（某交易日一个 quote 分区都没有）
  - 个股缺失（某交易日有分区，但某些股票不在里面）

用法：
    python scripts/detect_missing_kline.py                    # 全量
    python scripts/detect_missing_kline.py --date 2025-02-05  # 单日
    python scripts/detect_missing_kline.py --start 2025-01-01 --end 2025-03-01
    python scripts/detect_missing_kline.py --min-stocks 10    # 只看缺>=10只的日期
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd

from config import QUOTE_DIR, CONCEPT_DATE_DIR
from core.loaders import _list_partition_dates
from trading_calendar import trading_days


def _load_expected_codes(trade_date: str) -> set[str]:
    """用 concept_date 的 code 作为"应有股票"基准（比 quote 更全）。"""
    p = CONCEPT_DATE_DIR / f"trade_date={trade_date}" / "part.parquet"
    if not p.exists():
        return set()
    df = pd.read_parquet(p, columns=["code"])
    return set(df["code"].astype(str))


def detect(start: str | None, end: str | None, single_date: str | None,
           min_stocks: int):
    all_quote_dates = set(_list_partition_dates(QUOTE_DIR))

    if single_date:
        dates = [single_date]
    else:
        from datetime import date
        s = date.fromisoformat(start) if start else date.fromisoformat(min(all_quote_dates))
        e = date.fromisoformat(end) if end else date.fromisoformat(max(all_quote_dates))
        dates = [d.isoformat() for d in trading_days(s, e)]

    total_missing_stocks = 0
    missing_days = []
    detail = defaultdict(list)

    for d in dates:
        # 整天缺失
        if d not in all_quote_dates:
            missing_days.append((d, "whole_day"))
            continue

        expected = _load_expected_codes(d)
        if not expected:
            continue

        p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        df = pd.read_parquet(p, columns=["code"])
        actual = set(df["code"].astype(str))

        missing = expected - actual
        if len(missing) >= min_stocks:
            missing_days.append((d, f"{len(missing)}_stocks"))
            detail[d] = sorted(missing)
            total_missing_stocks += len(missing)

    print("=" * 70)
    print(f"扫描 {len(dates)} 个交易日")
    print(f"缺失日期 {len(missing_days)} 个")
    print(f"缺失股票累计 {total_missing_stocks} 只次")
    print("=" * 70)

    for d, reason in missing_days[:50]:
        codes = detail.get(d, [])
        sample = ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
        print(f"  {d}  {reason}  {sample}")

    if len(missing_days) > 50:
        print(f"  … 还有 {len(missing_days) - 50} 个日期")

    # 导出清单
    out = Path("data/_missing_kline.csv")
    rows = []
    for d, _ in missing_days:
        for c in detail.get(d, []) or ["__whole_day__"]:
            rows.append({"trade_date": d, "code": c})
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\n清单已导出: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--min-stocks", type=int, default=1)
    args = ap.parse_args()
    detect(args.start, args.end, args.date, args.min_stocks)