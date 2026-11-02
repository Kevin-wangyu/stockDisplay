"""用 eltdx 补全 quote 表里缺失的前复权日 K 线。

策略：
  - 按 code 分组（每只股票拉一次全部历史，不是按日期重复拉）
  - 目标日期在 eltdx 返回的 bars 里才写（自动跳过新股上市前/退市后）
  - 只写 open/high/low/close/volume/amount，其它字段留 None
  - 分批写盘（每 100 只 code 写一次），避免内存爆
  - 8 线程并发

用法：
    python scripts/backfill_kline.py                    # 全量补
    python scripts/backfill_kline.py --dry-run          # 只统计数量
    python scripts/backfill_kline.py --only-main        # 只补非北交所（先验证）
    python scripts/backfill_kline.py --only-bj          # 只补北交所
    python scripts/backfill_kline.py --workers 16       # 并发数
"""
import argparse
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd

from config import QUOTE_DIR

MISSING_CSV = Path("data/_missing_kline.csv")


def _to_tdx_code(code: str) -> str:
    """000001 -> sz000001；600000 -> sh600000；8/4/92 -> bj。"""
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "51", "58", "11", "50")):
        return f"sh{c}"
    if c.startswith(("00", "30", "12", "15", "16", "18", "39")):
        return f"sz{c}"
    if c.startswith(("8", "4", "92")):
        return f"bj{c}"
    return f"sz{c}"


def _get_bar_field(bar, *names, default=None):
    """安全获取 KlineBar 字段（兼容不同 eltdx 版本的命名）。"""
    for n in names:
        v = getattr(bar, n, None)
        if v is not None:
            return v
    return default


def _fetch_one(client, code: str, need_dates: set[str]) -> tuple[str, list[dict], str]:
    """拉一只股票的全历史 qfq 日 K，返回目标日期的 rows。

    返回 (code, rows, msg)
    """
    tdx_code = _to_tdx_code(code)
    try:
        series = client.bars.get(
            tdx_code, period="day", adjust="qfq", all_pages=True,
        )
    except Exception as e:
        return code, [], f"❌ {type(e).__name__}: {e}"

    if not series.bars:
        return code, [], "无历史数据"

    rows = []
    for bar in series.bars:
        d = bar.time.strftime("%Y-%m-%d")
        if d not in need_dates:
            continue

        # ⭐ 兼容不同字段名
        # volume_lots 优先（单位=手，与 quote.volume 一致）
        vol = _get_bar_field(
            bar,
            "volume_lots", "volume", "vol", "trade_volume",
            default=0,
        )
        amt = _get_bar_field(bar, "amount", "trade_amount", "amt", default=0)

        rows.append({
            "trade_date": d,
            "code": code,
            "name": None,
            "pct_change": None,
            "industry": None,
            "turnover_rate": None,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(vol),
            "amount": float(amt),
            "scrplus_90": None, "scrplus_70": None, "hot_rank": None,
            "huge_order_net": None, "big_order_net": None,
            "medium_order_net": None, "small_order_net": None,
        })

    if not rows:
        return code, [], f"eltdx 返回 {len(series.bars)} 根，目标日期无匹配（新股/退市）"

    return code, rows, f"✓ {len(rows)}/{len(need_dates)} 天"

def _append_to_partitions(by_date: dict[str, list[dict]]) -> int:
    """按日期分组追加到对应 part.parquet（去重 code，只写新 code）。"""
    total = 0
    for d, rows in by_date.items():
        if not rows:
            continue
        p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)

        new_df = pd.DataFrame(rows)
        if p.exists():
            old = pd.read_parquet(p)
            existing = set(old["code"].astype(str))
            new_df = new_df[~new_df["code"].astype(str).isin(existing)]
            if new_df.empty:
                continue
            merged = pd.concat([old, new_df], ignore_index=True)
        else:
            merged = new_df

        merged.to_parquet(p, index=False)
        total += len(new_df)
    return total


def backfill(dry_run: bool, only_main: bool, only_bj: bool, workers: int):
    if not MISSING_CSV.exists():
        print(f"❌ 请先跑 scripts/detect_missing_kline.py 生成 {MISSING_CSV}")
        return

    df = pd.read_csv(MISSING_CSV)
    df = df[df["code"] != "__whole_day__"]

    # 筛选
    if only_main:
        df = df[~df["code"].astype(str).str[:2].isin(["92", "8", "4"])]
    elif only_bj:
        df = df[df["code"].astype(str).str[:2].isin(["92", "8", "4"])]

    print(f"待补 {len(df)} 条，{df['code'].nunique()} 只股票")

    # 按 code 分组
    by_code: dict[str, set[str]] = defaultdict(set)
    for _, r in df.iterrows():
        by_code[str(r["code"])].add(str(r["trade_date"]))

    print(f"股票数: {len(by_code)}，需要请求 {len(by_code)} 次")

    if dry_run:
        print("[dry-run] 不实际请求")
        return

    from eltdx import TdxClient

    # 用线程池共享 client（如果 eltdx 客户端线程安全）
    # 如果不安全，改成每线程一个 client
    t0 = time.time()
    all_rows_by_date: dict[str, list[dict]] = defaultdict(list)
    success = 0
    skip = 0
    fail = 0
    done = 0

    def _worker(code, need_dates):
        # 每线程独立 client（确保线程安全）
        with TdxClient(timeout=15) as cli:
            return _fetch_one(cli, code, need_dates)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_worker, code, dates): code
            for code, dates in by_code.items()
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                _, rows, msg = fut.result()
            except Exception as e:
                fail += 1
                import traceback
                print(f"  {code}: ❌ 线程异常 {type(e).__name__}: {e}")
                if fail <= 3:   # 只打前 3 个完整 traceback，避免刷屏
                    traceback.print_exc()
                continue

            done += 1
            if rows:
                success += 1
                for r in rows:
                    all_rows_by_date[r["trade_date"]].append(r)
            elif "无历史数据" in msg:
                fail += 1
            else:
                skip += 1

            if done % 50 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (len(by_code) - done)
                print(f"  进度 {done}/{len(by_code)}，成功 {success}，跳过 {skip}，"
                      f"失败 {fail}，已用 {elapsed:.0f}s，预计还需 {eta:.0f}s")

            # 每 100 只 code 写一次盘（防止内存爆）
            if done % 100 == 0 and all_rows_by_date:
                n = _append_to_partitions(all_rows_by_date)
                all_rows_by_date.clear()
                if n:
                    print(f"  → 写入 {n} 行")

    # 最后一批写盘
    if all_rows_by_date:
        n = _append_to_partitions(all_rows_by_date)
        print(f"  → 最后写入 {n} 行")

    elapsed = time.time() - t0
    print()
    print("=" * 70)
    print(f"完成：成功 {success}，跳过 {skip}，失败 {fail}，耗时 {elapsed:.0f}s")
    print("⚠ 未自动合并月：")
    print("  1. 先跑阶段 3 重算 daily_pct/streak/picks")
    print("  2. 再跑 python scripts/archive_month.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-main", action="store_true", help="只补非北交所")
    ap.add_argument("--only-bj", action="store_true", help="只补北交所")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    backfill(args.dry_run, args.only_main, args.only_bj, args.workers)