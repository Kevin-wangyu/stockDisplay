"""从 data_compact 的月合并文件反向拆分，补齐 data/ 里缺失的日分区。

用法：
    python scripts/split_compact_to_daily.py                     # 补齐缺失
    python scripts/split_compact_to_daily.py --dry-run           # 只统计
    python scripts/split_compact_to_daily.py --check-consistency # 检查内容一致性
    python scripts/split_compact_to_daily.py --kind quote        # 单个 kind
    python scripts/split_compact_to_daily.py --overwrite         # 覆盖已存在
    python scripts/split_compact_to_daily.py --skip-calendar     # 跳过交易日校验
    python scripts/split_compact_to_daily.py --strict            # 遇到不一致立即中止

输出原则：无动作时不刷屏；只在补数据/发现异常时打印。
"""
import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import duckdb

from config import DATA_DIR

COMPACT_ROOT = DATA_DIR.parent / "data_compact"

KINDS = {
    "quote":          DATA_DIR / "quote",
    "industry_flow":  DATA_DIR / "industry_flow",
    "concept_flow":   DATA_DIR / "concept_flow",
    "concept_date":   DATA_DIR / "concept_date",
    "daily_pct":      DATA_DIR / "daily_pct",
    "concept_picks":  DATA_DIR / "concept_picks",
    "amo_components": DATA_DIR / "amo_components",
    "limit_up_streak": DATA_DIR / "limit_up_streak",
}


# ══════════════════════════════════════════════════════════════
# 交易日历
# ══════════════════════════════════════════════════════════════

_TRADING_CACHE: dict[str, bool] = {}


def _is_trading_day(date_str: str) -> bool | None:
    if date_str in _TRADING_CACHE:
        return _TRADING_CACHE[date_str]
    try:
        from trading_calendar import trading_days
        d = date.fromisoformat(date_str)
        r = bool(trading_days(d, d))
        _TRADING_CACHE[date_str] = r
        return r
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# compact 扫描
# ══════════════════════════════════════════════════════════════

def _list_compact_months(kind: str) -> list[Path]:
    d = COMPACT_ROOT / kind
    return sorted(d.glob("*.parquet")) if d.exists() else []


def _list_month_dates(con, month_file: Path) -> list[str]:
    f = str(month_file).replace("\\", "/")
    rows = con.execute(f"""
        SELECT DISTINCT CAST(trade_date AS VARCHAR) AS d
        FROM read_parquet('{f}')
        WHERE trade_date IS NOT NULL ORDER BY d
    """).fetchall()
    return [str(r[0])[:10] for r in rows if r[0] is not None]


def _list_data_dates(kind_dir: Path) -> set[str]:
    if not kind_dir.exists():
        return set()
    out = set()
    for p in kind_dir.iterdir():
        if p.is_dir() and p.name.startswith("trade_date="):
            pq = p / "part.parquet"
            if pq.exists() and pq.stat().st_size > 0:
                out.add(p.name.split("=", 1)[1])
    return out


# ══════════════════════════════════════════════════════════════
# 内容一致性检查
# ══════════════════════════════════════════════════════════════

def _check_daily_matches(con, month_file: Path, daily_pq: Path,
                         date_str: str) -> tuple[bool, str]:
    if not daily_pq.exists():
        return False, "missing"
    try:
        daily_cols = set(con.execute(
            f"SELECT * FROM read_parquet('{daily_pq}') LIMIT 0"
        ).df().columns)
        mf = str(month_file).replace("\\", "/")
        compact_df = con.execute(f"""
            SELECT * FROM read_parquet('{mf}')
            WHERE CAST(trade_date AS VARCHAR) = '{date_str}'
        """).df()
        compact_cols = set(compact_df.columns)
        if daily_cols != compact_cols:
            missing = compact_cols - daily_cols
            extra = daily_cols - compact_cols
            parts = []
            if missing: parts.append(f"缺 {sorted(missing)[:3]}")
            if extra:   parts.append(f"多 {sorted(extra)[:3]}")
            return False, "列差异: " + "; ".join(parts)
        daily_count = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{daily_pq}')"
        ).fetchone()[0]
        if daily_count != len(compact_df):
            return False, f"行数: data={daily_count} vs compact={len(compact_df)}"
        return True, "match"
    except Exception as e:
        return False, f"读取失败: {type(e).__name__}: {e}"


# ══════════════════════════════════════════════════════════════
# 单日提取
# ══════════════════════════════════════════════════════════════

def _extract_one_day(con, month_file: Path, out_dir: Path, date_str: str,
                     overwrite: bool) -> tuple[int, str]:
    target_dir = out_dir / f"trade_date={date_str}"
    target_pq = target_dir / "part.parquet"
    if target_pq.exists() and not overwrite:
        return 0, "skip"
    target_dir.mkdir(parents=True, exist_ok=True)
    f = str(month_file).replace("\\", "/")
    t = str(target_pq).replace("\\", "/")
    t_tmp = t + ".tmp"
    try:
        con.execute(f"""
            COPY (
                SELECT * FROM read_parquet('{f}')
                WHERE CAST(trade_date AS VARCHAR) = '{date_str}'
            ) TO '{t_tmp}' (FORMAT PARQUET, COMPRESSION 'zstd')
        """)
        n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{t_tmp}')"
        ).fetchone()[0]
        if n == 0:
            Path(t_tmp).unlink(missing_ok=True)
            return 0, "empty"
        os.replace(t_tmp, t)
        return n, "ok"
    except Exception as e:
        Path(t_tmp).unlink(missing_ok=True)
        return 0, f"error: {e}"


# ══════════════════════════════════════════════════════════════
# 单个 kind 处理
# ══════════════════════════════════════════════════════════════

def process_kind(con, kind: str, start, end, overwrite, dry_run,
                 skip_calendar, strict, check_consistency=False) -> dict:
    out_dir = KINDS[kind]
    months = _list_compact_months(kind)
    stats = {
        "kind": kind, "compact_months": len(months),
        "days_checked": 0, "days_ok": 0, "days_skip": 0,
        "days_mismatch": 0, "days_non_trading": 0, "days_fail": 0,
        "rows_written": 0,
        "data_only_days": [],
        "mismatch_details": [], "non_trading_details": [],
        "errors": [],
    }
    if not months:
        return stats

    compact_all_dates = set()
    t0 = time.time()

    # 只打印有动作的变化
    prev_snapshot = (0, 0, 0, 0)

    for i, mf in enumerate(months, 1):
        try:
            dates = _list_month_dates(con, mf)
        except Exception as e:
            stats["errors"].append(f"{mf.name} 读日期失败: {e}")
            continue
        compact_all_dates.update(dates)
        if start: dates = [d for d in dates if d >= start]
        if end:   dates = [d for d in dates if d <= end]

        for d in dates:
            stats["days_checked"] += 1

            if not skip_calendar:
                is_trading = _is_trading_day(d)
                if is_trading is False:
                    stats["days_non_trading"] += 1
                    stats["non_trading_details"].append(d)
                    continue

            daily_pq = out_dir / f"trade_date={d}" / "part.parquet"

            # 不存在 → 补
            if not daily_pq.exists():
                if dry_run:
                    stats["days_ok"] += 1
                    continue
                n, status = _extract_one_day(con, mf, out_dir, d, overwrite=False)
                if status == "ok":
                    stats["days_ok"] += 1
                    stats["rows_written"] += n
                elif status == "empty":
                    stats["errors"].append(f"{d}: 空数据")
                else:
                    stats["days_fail"] += 1
                    stats["errors"].append(f"{d}: {status}")
                continue

            # 存在 → 默认跳过；--check-consistency 才检查
            if not check_consistency:
                stats["days_skip"] += 1
                continue

            match, diff = _check_daily_matches(con, mf, daily_pq, d)
            if match:
                stats["days_skip"] += 1
                continue

            stats["days_mismatch"] += 1
            stats["mismatch_details"].append({"date": d, "diff": diff})
            if strict:
                stats["errors"].append(f"{d}: {diff}（--strict 中止）")
                return stats
            if overwrite and not dry_run:
                n, status = _extract_one_day(con, mf, out_dir, d, overwrite=True)
                if status == "ok":
                    stats["days_ok"] += 1
                    stats["rows_written"] += n

        # ⭐ 只在有动作时打进度
        cur = (stats["days_ok"], stats["days_mismatch"],
               stats["days_non_trading"], stats["days_fail"])
        if cur != prev_snapshot:
            elapsed = time.time() - t0
            print(f"    {i}/{len(months)} 月 · 补 {stats['days_ok']} · "
                  f"不一致 {stats['days_mismatch']} · "
                  f"非交易 {stats['days_non_trading']} · "
                  f"失败 {stats['days_fail']} · {elapsed:.0f}s")
            prev_snapshot = cur

    # ⭐ 单 kind 总结（一行）
    has_action = (stats["days_ok"] or stats["days_mismatch"]
                  or stats["days_non_trading"] or stats["days_fail"])
    if not has_action:
        print(f"  [{kind}] {stats['days_checked']} 天全部一致 ✓")
    else:
        parts = [f"{stats['days_checked']} 天"]
        if stats["days_ok"]:         parts.append(f"补 {stats['days_ok']}")
        if stats["days_skip"]:       parts.append(f"跳 {stats['days_skip']}")
        if stats["days_mismatch"]:   parts.append(f"不一致 {stats['days_mismatch']}")
        if stats["days_non_trading"]: parts.append(f"非交易 {stats['days_non_trading']}")
        if stats["days_fail"]:       parts.append(f"失败 {stats['days_fail']}")
        print(f"  [{kind}] " + " · ".join(parts))

    # 反向检查：data 有但 compact 没
    data_dates = _list_data_dates(out_dir)
    if start: data_dates = {d for d in data_dates if d >= start}
    if end:   data_dates = {d for d in data_dates if d <= end}
    stats["data_only_days"] = sorted(data_dates - compact_all_dates)
    return stats


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="all",
                    choices=["all"] + list(KINDS.keys()))
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-calendar", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--check-consistency", action="store_true",
                    help="检查日分区内容一致性（行数 + 列名，慢）。默认跳过")
    args = ap.parse_args()

    kinds = list(KINDS.keys()) if args.kind == "all" else [args.kind]

    print("=" * 76)
    print("从 data_compact 反解日分区")
    print("=" * 76)
    print(f"  kinds: {kinds}")
    flags = []
    if args.dry_run:         flags.append("dry-run")
    if args.overwrite:       flags.append("overwrite")
    if args.check_consistency: flags.append("check-consistency")
    if args.skip_calendar:   flags.append("skip-calendar")
    if args.strict:          flags.append("strict")
    print(f"  flags: {flags or ['default']}")
    print()

    con = duckdb.connect()
    con.execute("SET threads = 4")
    con.execute("SET memory_limit = '4GB'")

    t_all = time.time()
    all_stats = []

    for kind in kinds:
        print(f"处理 {kind}")
        s = process_kind(con, kind, args.start, args.end,
                         args.overwrite, args.dry_run,
                         args.skip_calendar, args.strict,
                         check_consistency=args.check_consistency)
        all_stats.append(s)
        if args.strict and s["errors"]:
            print(f"  ⛔ --strict 中止")
            break
        print()

    con.close()

    # ── 问题明细（只列非空） ──
    has_issue = False
    for s in all_stats:
        if s["mismatch_details"]:
            has_issue = True
            print(f"⚠ [{s['kind']}] 内容不一致 {len(s['mismatch_details'])} 天:")
            for m in s["mismatch_details"][:20]:
                print(f"    {m['date']}: {m['diff']}")
            print()
        if s["non_trading_details"]:
            has_issue = True
            print(f"⚠ [{s['kind']}] 非交易日误入 {len(s['non_trading_details'])} 天:")
            print(f"    {s['non_trading_details'][:20]}")
            print()
        if s["data_only_days"]:
            has_issue = True
            print(f"⚠ [{s['kind']}] data 有但 compact 没有 {len(s['data_only_days'])} 天:")
            print(f"    {s['data_only_days'][:20]}")
            print(f"    → 建议跑 archive_month.py 归档该 kind")
            print()
        if s["errors"]:
            has_issue = True
            print(f"❌ [{s['kind']}] 错误 {len(s['errors'])} 条:")
            for e in s["errors"][:10]:
                print(f"    {e}")
            print()

    elapsed = time.time() - t_all
    if has_issue:
        print("=" * 76)
        print(f"总耗时: {elapsed:.1f}s")
        if args.dry_run:
            print("（dry-run：未实际写盘）")
        print("=" * 76)
    else:
        print("=" * 76)
        print(f"✅ 所有 kind 数据完整一致（{elapsed:.1f}s）")
        if args.dry_run:
            print("（dry-run：未实际写盘）")
        print("=" * 76)


if __name__ == "__main__":
    main()