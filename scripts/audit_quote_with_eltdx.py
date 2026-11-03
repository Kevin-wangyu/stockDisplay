"""用 eltdx 审查 quote 数据，修复脏行（close=0）或异常数据。

用法：
    python scripts/audit_quote_with_eltdx.py --dry-run         # 只看报告
    python scripts/audit_quote_with_eltdx.py                   # 交互确认
    python scripts/audit_quote_with_eltdx.py --yes             # 直接执行
    python scripts/audit_quote_with_eltdx.py --code 002693     # 只审单只
    python scripts/audit_quote_with_eltdx.py --replace-suspect # 差异>5%也替换
    python scripts/audit_quote_with_eltdx.py --workers 12      # 并发数
    python scripts/audit_quote_with_eltdx.py --start 2024-01-01 --end 2024-12-31

策略：
    1. 扫描 quote 全分区，找出 close=0/NULL 的 (code, trade_date)
    2. 按 code 分组，对涉及到的股票用 eltdx 拉全历史（并发）
    3. 逐行对比：
         - eltdx 有该日 → 用 eltdx OHLCV 替换（保留其它字段）
         - eltdx 无该日 → 确认停牌，删除该行
    4. 额外（--replace-suspect 开启）：close≠0 但差异 > 5% 的也替换
"""
import argparse
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd

from config import QUOTE_DIR
from core.loaders import _list_partition_dates


# ══════════════════════════════════════════════════════════════
# eltdx 拉取
# ══════════════════════════════════════════════════════════════

def _to_tdx_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "51", "58", "11", "50")):
        return f"sh{c}"
    if c.startswith(("00", "30", "12", "15", "16", "18", "39")):
        return f"sz{c}"
    if c.startswith(("8", "4", "92")):
        return f"bj{c}"
    return f"sz{c}"


def _fetch_one(code: str) -> dict:
    """拉一只股票全历史 qfq 日 K，返回 {date_str: bar_dict}。

    失败时返回 {"_error": "..."}。
    """
    from eltdx import TdxClient

    tdx_code = _to_tdx_code(code)
    try:
        with TdxClient(timeout=30) as client:
            s = client.bars.get(
                tdx_code, period="day", adjust="qfq", all_pages=True,
            )
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}

    out = {}
    for b in s.bars:
        out[b.time.strftime("%Y-%m-%d")] = {
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume_lots),
            "amount": float(b.amount),
        }
    return out


# ══════════════════════════════════════════════════════════════
# 扫描脏行
# ══════════════════════════════════════════════════════════════

def scan_dirty_rows(start: str | None, end: str | None, single_code: str | None):
    """扫描 quote 分区，返回 {(trade_date, code): row_dict}。"""
    dates = _list_partition_dates(QUOTE_DIR)
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]

    dirty = {}
    print(f"[scan] 扫描 {len(dates)} 个分区…", flush=True)

    for i, d in enumerate(dates, 1):
        p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)

        mask = (df["close"] == 0) | df["close"].isna()
        if single_code:
            mask &= (df["code"] == single_code)

        bad = df[mask]
        for _, r in bad.iterrows():
            dirty[(d, r["code"])] = r.to_dict()

        if (i % 100 == 0):
            print(f"  {i}/{len(dates)}，已找到 {len(dirty)} 条脏行", flush=True)

    print(f"[scan] 找到 {len(dirty)} 条脏行", flush=True)
    return dirty


# ══════════════════════════════════════════════════════════════
# 分类处理
# ══════════════════════════════════════════════════════════════

def audit_and_fix(dirty: dict, workers: int, replace_suspect: bool,
                  dry_run: bool):
    """对脏行做审查 + 修复。返回 (to_replace, to_delete, to_review) 三个 dict。

    to_replace: {(d, code): {new_row}}
    to_delete:  set of (d, code)
    to_review:  [(d, code, reason)]
    """
    if not dirty:
        print("[audit] 无脏行，退出", flush=True)
        return {}, set(), []

    # 按 code 分组
    by_code = defaultdict(set)
    for (d, code) in dirty:
        by_code[code].add(d)

    print(f"[audit] 涉及 {len(by_code)} 只股票，用 eltdx 拉数据…", flush=True)

    # 并发拉取
    code_bars = {}
    t0 = time.time()
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in by_code}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                bars = fut.result()
            except Exception as e:
                bars = {"_error": str(e)}
            code_bars[c] = bars
            done += 1
            if done % 20 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (len(by_code) - done)
                print(f"  [{done}/{len(by_code)}] 已用 {elapsed:.0f}s | "
                      f"预计还需 {eta:.0f}s", flush=True)

    print(f"[audit] eltdx 拉取完成 {time.time()-t0:.0f}s", flush=True)

    # 逐行分类
    to_replace = {}
    to_delete = set()
    to_review = []

    for (d, code), row in dirty.items():
        bars = code_bars.get(code, {})

        if "_error" in bars:
            to_review.append((d, code, f"eltdx 失败: {bars['_error']}"))
            continue

        if d in bars:
            # eltdx 有数据 → 用 eltdx 替换
            bar = bars[d]
            new_row = dict(row)   # 保留非 OHLCV 字段
            new_row["open"] = bar["open"]
            new_row["high"] = bar["high"]
            new_row["low"] = bar["low"]
            new_row["close"] = bar["close"]
            new_row["volume"] = bar["volume"]
            new_row["amount"] = bar["amount"]
            to_replace[(d, code)] = new_row
        else:
            # eltdx 也没有 → 停牌，删掉
            to_delete.add((d, code))

    # 额外：找出差异 > 5% 的正常行（可选）
    if replace_suspect:
        print("[audit] 扫描正常行差异（--replace-suspect 开启）…", flush=True)
        dates_all = set(d for d, _ in dirty)
        for d in dates_all:
            p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
            if not p.exists():
                continue
            df = pd.read_parquet(p)
            for _, r in df.iterrows():
                code = r["code"]
                if code not in code_bars:
                    continue
                bars = code_bars[code]
                if "_error" in bars or d not in bars:
                    continue
                close = r["close"]
                if close == 0 or pd.isna(close):
                    continue
                eltdx_close = bars[d]["close"]
                pct = abs(eltdx_close - close) / close * 100
                if pct > 5:
                    to_review.append((d, code,
                                     f"差异 {pct:.1f}%: quote={close:.2f} "
                                     f"eltdx={eltdx_close:.2f}"))
                    new_row = dict(r)
                    new_row["open"] = bars[d]["open"]
                    new_row["high"] = bars[d]["high"]
                    new_row["low"] = bars[d]["low"]
                    new_row["close"] = bars[d]["close"]
                    new_row["volume"] = bars[d]["volume"]
                    new_row["amount"] = bars[d]["amount"]
                    to_replace[(d, code)] = new_row

    return to_replace, to_delete, to_review


# ══════════════════════════════════════════════════════════════
# 写盘
# ══════════════════════════════════════════════════════════════

def apply_changes(to_replace: dict, to_delete: set):
    """按日期分组，逐分区更新。"""
    # 按日期分组
    replace_by_date = defaultdict(list)
    for (d, code), new_row in to_replace.items():
        replace_by_date[d].append(new_row)

    delete_by_date = defaultdict(set)
    for (d, code) in to_delete:
        delete_by_date[d].add(code)

    all_dates = set(replace_by_date) | set(delete_by_date)
    print(f"[apply] 更新 {len(all_dates)} 个分区…", flush=True)

    n_replace_total = 0
    n_delete_total = 0

    for d in sorted(all_dates):
        p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)

        # 删除
        codes_del = delete_by_date.get(d, set())
        if codes_del:
            df = df[~df["code"].isin(codes_del)]
            n_delete_total += len(codes_del)

        # 替换
        rows_new = replace_by_date.get(d, [])
        if rows_new:
            codes_repl = [r["code"] for r in rows_new]
            df = df[~df["code"].isin(codes_repl)]
            df = pd.concat([df, pd.DataFrame(rows_new)], ignore_index=True)
            n_replace_total += len(rows_new)

        if len(df) == 0:
            p.unlink()
            print(f"  {d}: 全空，删分区文件")
        else:
            df.to_parquet(p, index=False)

    print(f"[apply] 替换 {n_replace_total} 行，删除 {n_delete_total} 行", flush=True)


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--code", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--replace-suspect", action="store_true",
                    help="差异>5% 的也替换（默认只报告不改）")
    args = ap.parse_args()

    print("=" * 70)
    print("eltdx 审查 + 修复 quote 脏行")
    print("=" * 70)

    # 1. 扫描
    dirty = scan_dirty_rows(args.start, args.end, args.code)
    if not dirty:
        print("无脏行，退出")
        return

    # 2. 审查
    to_replace, to_delete, to_review = audit_and_fix(
        dirty, args.workers, args.replace_suspect, args.dry_run)

    # 3. 报告
    print()
    print("=" * 70)
    print("审查结果")
    print("=" * 70)
    print(f"待替换（eltdx 有数据）：{len(to_replace)} 行")
    print(f"待删除（eltdx 也无，确认停牌）：{len(to_delete)} 行")
    print(f"需人工复核：{len(to_review)} 行")

    # 抽样展示
    if to_replace:
        print("\n=== 待替换 前 10 条 ===")
        for (d, c), row in list(to_replace.items())[:10]:
            old = dirty[(d, c)]
            print(f"  {d} {c}: close {old['close']} → {row['close']}, "
                  f"volume {old['volume']} → {row['volume']}")

    if to_delete:
        print("\n=== 待删除 前 20 条 ===")
        for (d, c) in list(to_delete)[:20]:
            print(f"  {d} {c}（eltdx 无数据，确认停牌）")

    if to_review:
        print("\n=== 需人工复核 前 20 条 ===")
        for (d, c, reason) in to_review[:20]:
            print(f"  {d} {c}: {reason}")

    # 4. 执行
    if args.dry_run:
        print("\n[dry-run] 未实际修改")
        return

    if not to_replace and not to_delete:
        print("\n无可执行变更")
        return

    if not args.yes:
        ans = input(f"\n执行替换 {len(to_replace)} 行 + 删除 {len(to_delete)} 行？(yes/no): ").strip().lower()
        if ans != "yes":
            print("已取消")
            return

    apply_changes(to_replace, to_delete)

    print()
    print("=" * 70)
    print("完成")
    print("=" * 70)
    print("下一步：")
    print("  1. 重算 daily_pct：")
    print("     python -c \"from core.rebuild import rebuild_daily_pct; rebuild_daily_pct(force_recent_days=9999)\"")
    print("  2. 重算 streak：")
    print("     python -c \"from core.rebuild import rebuild_limit_up_streak; rebuild_limit_up_streak(force=True, limit_days=9999)\"")
    print("  3. 重建三榜缓存：")
    print("     python scripts/rebuild_tops_cache.py --force")
    print("  4. 重算 picks：")
    print("     python scripts/recompute_picks.py")


if __name__ == "__main__":
    main()