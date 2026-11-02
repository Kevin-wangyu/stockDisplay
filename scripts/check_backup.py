"""数据完整性检查工具。

功能：
  1. 各表日期覆盖统计
  2. concept_date 非交易日目录检测
  3. daily_pct 内部一致性验证（手算 close vs 存储 pct）
  4. daily_pct 异常值检测（inf/nan/超范围）
  5. quote vs daily_pct 口径差异（信息性，不算错误）

用法：
    python scripts/check_backup.py                       # 基础检查（快）
    python scripts/check_backup.py --verify-pct          # 深查 pct 一致性（抽样 10 天）
    python scripts/check_backup.py --fix-calendar        # dry-run 列出非交易日目录
    python scripts/check_backup.py --fix-calendar --yes  # 直接删除，不询问

注意：
    - quote 表 pct_change 是东财口径
    - daily_pct 是前复权 close 自算，两者不一致属正常
    - daily_pct 只应和自己手算的 close 涨跌幅一致
"""
import argparse
import math
import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import duckdb
import pandas as pd

from config import (
    QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR,
    DAILY_PCT_DIR, LIMIT_UP_STREAK_DIR, CONCEPT_PICKS_DIR,
    AMO_DIR,
)
from trading_calendar import _load_calendar


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════

def _list_trade_date_dirs(base_dir: Path) -> list[str]:
    """列出 base_dir 下所有 trade_date=YYYY-MM-DD/part.parquet 的日期。"""
    if not base_dir.exists():
        return []
    out = []
    for p in base_dir.iterdir():
        if not p.is_dir() or not p.name.startswith("trade_date="):
            continue
        d = p.name.split("=", 1)[1]
        if len(d) == 10 and d[4] == "-" and (p / "part.parquet").exists():
            out.append(d)
    return sorted(out)


def _hr(title: str = ""):
    print("\n" + "=" * 70)
    if title:
        print(title)
        print("=" * 70)


# ═══════════════════════════════════════════════════════════
# 1. 各表日期覆盖
# ═══════════════════════════════════════════════════════════

def check_coverage():
    """各表日期覆盖统计。"""
    _hr("各表日期覆盖")
    tables = [
        ("quote", QUOTE_DIR),
        ("industry_flow", INDUSTRY_DIR),
        ("concept_flow", CONCEPT_DIR),
        ("concept_date", CONCEPT_DATE_DIR),
        ("daily_pct", DAILY_PCT_DIR),
        ("limit_up_streak", LIMIT_UP_STREAK_DIR),
        ("concept_picks", CONCEPT_PICKS_DIR),
        ("amo_components", AMO_DIR),
    ]
    for name, d in tables:
        dates = _list_trade_date_dirs(d)
        if not dates:
            print(f"[{name:16s}] ❌ 无数据")
            continue
        print(f"[{name:16s}] {len(dates):>4d} 天  {dates[0]} ~ {dates[-1]}")


# ═══════════════════════════════════════════════════════════
# 2. concept_date 非交易日检测
# ═══════════════════════════════════════════════════════════

def check_concept_date_calendar(fix: bool = False, auto_yes: bool = False):
    """检测（可选删除）concept_date 里的非交易日目录。"""
    _hr("concept_date 交易日历检查")

    cal = set(d.strftime("%Y-%m-%d") for d in _load_calendar())
    dates = _list_trade_date_dirs(CONCEPT_DATE_DIR)
    bad = [d for d in dates if d not in cal]

    if not bad:
        print(f"✅ concept_date 全部合法（{len(dates)} 天）")
        return

    print(f"⚠ 非交易日目录 {len(bad)} 个（总 {len(dates)} 天）")
    print(f"  前 20: {bad[:20]}")

    if not fix:
        print("\n执行 --fix-calendar 来查看删除清单，--fix-calendar --yes 直接删除")
        return

    if not auto_yes:
        print(f"\n将删除 {len(bad)} 个目录，永久丢失（不可恢复）")
        ans = input("输入 yes 确认: ").strip().lower()
        if ans != "yes":
            print("已取消")
            return

    for d in bad:
        try:
            shutil.rmtree(CONCEPT_DATE_DIR / f"trade_date={d}")
        except Exception as e:
            print(f"  ❌ {d}: {e}")

    remain = len(_list_trade_date_dirs(CONCEPT_DATE_DIR))
    print(f"✅ 已删除 {len(bad)} 个目录，剩余 {remain} 天")


# ═══════════════════════════════════════════════════════════
# 3. daily_pct 内部一致性验证
# ═══════════════════════════════════════════════════════════

def verify_daily_pct(n_dates: int = 10, n_codes: int = 5):
    """手算 close 涨跌幅，对比 daily_pct 存储值。

    只验证 daily_pct 和 quote 前复权 close 的一致性，
    不比较 quote.pct_change（那是另一口径）。
    """
    _hr(f"daily_pct 内部一致性（抽样 {n_dates} 天 × {n_codes} 只）")

    con = duckdb.connect()

    quote_dates = _list_trade_date_dirs(QUOTE_DIR)
    pct_dates = set(_list_trade_date_dirs(DAILY_PCT_DIR))
    check_dates = [d for d in quote_dates if d in pct_dates]

    if len(check_dates) < 2:
        print("❌ 交易日不足 2 天，无法验证")
        return

    # 取最近 n_dates 天（需要连续）
    check_dates = check_dates[-n_dates - 1:]
    if len(check_dates) < 2:
        print("❌ 交易日不足")
        return

    # 抽样股票
    row = con.execute(f"""
        SELECT code FROM read_parquet('{QUOTE_DIR}/trade_date={check_dates[-1]}/*.parquet')
        LIMIT {n_codes}
    """).fetchall()
    codes = [r[0] for r in row]

    if not codes:
        print("❌ 无法抽样股票")
        return

    print(f"抽样股票: {codes}")
    print(f"抽样日期: {check_dates[0]} ~ {check_dates[-1]}\n")

    total_checks = 0
    ok_checks = 0
    bad_samples = []

    for i in range(1, len(check_dates)):
        d_prev = check_dates[i - 1]
        d_cur = check_dates[i]

        # 读两天的 close
        quote_df = con.execute(f"""
            SELECT code, CAST(trade_date AS VARCHAR) AS d, close
            FROM read_parquet([
                '{QUOTE_DIR}/trade_date={d_prev}/*.parquet',
                '{QUOTE_DIR}/trade_date={d_cur}/*.parquet'
            ], hive_partitioning=true)
            WHERE code IN ({','.join(f"'{c}'" for c in codes)})
        """).df()

        # 读 daily_pct
        pct_df = con.execute(f"""
            SELECT code, CAST(trade_date AS VARCHAR) AS d, pct_change
            FROM read_parquet('{DAILY_PCT_DIR}/trade_date={d_cur}/*.parquet')
            WHERE code IN ({','.join(f"'{c}'" for c in codes)})
        """).df()

        # 汇总
        closes = {}
        for _, r in quote_df.iterrows():
            closes[(r["code"], str(r["d"])[:10])] = float(r["close"])
        stored_pcts = {(r["code"], str(r["d"])[:10]): r["pct_change"]
                       for _, r in pct_df.iterrows()}

        for code in codes:
            c_prev = closes.get((code, d_prev))
            c_cur = closes.get((code, d_cur))
            stored = stored_pcts.get((code, d_cur))

            if c_prev is None or c_cur is None:
                continue
            if c_prev == 0:
                continue

            total_checks += 1
            manual = (c_cur - c_prev) / c_prev * 100

            if stored is None or (isinstance(stored, float) and math.isnan(stored)):
                bad_samples.append(f"  {code} {d_cur}: 手算 {manual:+.4f}%，存储 NaN")
                continue

            if abs(manual - float(stored)) < 1e-6:
                ok_checks += 1
            else:
                bad_samples.append(
                    f"  {code} {d_cur}: 手算 {manual:+.4f}% vs 存储 {float(stored):+.4f}%"
                )

    print(f"验证完成: {ok_checks}/{total_checks} 一致")

    if bad_samples:
        print(f"\n⚠ 不一致样例（前 10 条）:")
        for s in bad_samples[:10]:
            print(s)
        print(f"\n  → 可能原因：quote 的 close 被重新生成（前复权调整），")
        print(f"    daily_pct 是旧的。修复：")
        print(f"    python -c \"from queries import rebuild_daily_pct; "
              f"rebuild_daily_pct(force_recent_days=30)\"")
    else:
        print("✅ daily_pct 全部内部一致")


# ═══════════════════════════════════════════════════════════
# 4. daily_pct 异常值检测
# ═══════════════════════════════════════════════════════════

def check_daily_pct_anomalies(n_dates: int = 30):
    """检测 daily_pct 的 inf/nan/超范围。"""
    _hr(f"daily_pct 异常值检测（最近 {n_dates} 天）")

    con = duckdb.connect()
    dates = _list_trade_date_dirs(DAILY_PCT_DIR)[-n_dates:]

    total_rows = 0
    inf_count = 0
    nan_count = 0
    out_of_range = 0

    for d in dates:
        row = con.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN pct_change = 'inf'::DOUBLE
                          OR pct_change = '-inf'::DOUBLE THEN 1 ELSE 0 END) AS inf_c,
                SUM(CASE WHEN pct_change IS NULL THEN 1 ELSE 0 END) AS nan_c,
                SUM(CASE WHEN ABS(pct_change) > 25 THEN 1 ELSE 0 END) AS oor_c
            FROM read_parquet('{DAILY_PCT_DIR}/trade_date={d}/*.parquet')
        """).fetchone()
        total_rows += row[0] or 0
        inf_count += row[1] or 0
        nan_count += row[2] or 0
        out_of_range += row[3] or 0

    print(f"总计 {total_rows} 行")
    print(f"  inf:     {inf_count}")
    print(f"  null:    {nan_count}（停牌/新股正常）")
    print(f"  |pct|>25: {out_of_range}（新股首日/ST摘帽，通常 < 100）")

    if inf_count > 0:
        print(f"\n⚠ 发现 {inf_count} 个 inf（prev_close=0 导致）")
        print(f"  修复：python -c \"from queries import rebuild_daily_pct; "
              f"rebuild_daily_pct(force_recent_days=30)\"")
    else:
        print("✅ 无 inf")


# ═══════════════════════════════════════════════════════════
# 5. quote vs daily_pct 口径差异（信息性）
# ═══════════════════════════════════════════════════════════

def check_pct_consistency_info():
    """信息性检查：quote.pct_change 和 daily_pct.pct_change 的差异。

    两者本就是不同口径（东财 vs 前复权），不一致属正常。
    """
    _hr("quote vs daily_pct 口径差异（信息性）")

    con = duckdb.connect()
    dates = _list_trade_date_dirs(DAILY_PCT_DIR)
    if not dates:
        print("❌ 无数据")
        return

    d = dates[-1]
    row = con.execute(f"""
        WITH q AS (
            SELECT code, pct_change FROM read_parquet(
                '{QUOTE_DIR}/trade_date={d}/*.parquet')
        ),
        p AS (
            SELECT code, pct_change FROM read_parquet(
                '{DAILY_PCT_DIR}/trade_date={d}/*.parquet')
        )
        SELECT
            COUNT(*) AS n,
            AVG(ABS(q.pct_change - p.pct_change)) AS avg_diff,
            MAX(ABS(q.pct_change - p.pct_change)) AS max_diff,
            SUM(CASE WHEN ABS(q.pct_change - p.pct_change) > 0.5 THEN 1 ELSE 0 END) AS big_diff
        FROM q JOIN p ON q.code = p.code
        WHERE q.pct_change IS NOT NULL AND p.pct_change IS NOT NULL
    """).fetchone()

    if row and row[0]:
        print(f"日期 {d}，{row[0]} 只股票")
        print(f"  平均差异: {row[1]:.4f}%")
        print(f"  最大差异: {row[2]:.4f}%")
        print(f"  差异>0.5%: {row[3]} 只")
        print(f"\n  ℹ️ 差异属正常：quote 是东财口径，daily_pct 是前复权口径")
        print(f"     除权日附近两者必然不同，前端统一用 daily_pct")


# ═══════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="数据完整性检查工具")
    ap.add_argument("--verify-pct", action="store_true",
                    help="深查 daily_pct 内部一致性（抽样 10 天）")
    ap.add_argument("--fix-calendar", action="store_true",
                    help="检测（可选删除）concept_date 非交易日目录")
    ap.add_argument("--yes", action="store_true",
                    help="配合 --fix-calendar 使用，跳过确认")
    ap.add_argument("--check-pct-anomalies", action="store_true",
                    help="检测 daily_pct 的 inf/nan/超范围")
    args = ap.parse_args()

    # 基础检查
    check_coverage()
    check_concept_date_calendar(fix=args.fix_calendar, auto_yes=args.yes)
    check_pct_consistency_info()

    # 深查
    if args.verify_pct:
        verify_daily_pct()
    if args.check_pct_anomalies:
        check_daily_pct_anomalies()

    _hr("完成")
    print("提示：")
    print("  - 深查 pct:       python scripts/check_backup.py --verify-pct")
    print("  - 清理非交易日:   python scripts/check_backup.py --fix-calendar")
    print("  - 检测 inf/nan:   python scripts/check_backup.py --check-pct-anomalies")


if __name__ == "__main__":
    main()