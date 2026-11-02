"""批量预生成所有历史日期的榜单缓存。

用法：
    python precompute.py                              # 补所有缺的
    python precompute.py --date 2026-10-29            # 单日
    python precompute.py --date 2026-10-29 --force    # 单日强制重算
    python precompute.py --start 2026-10-01 --end 2026-10-15   # 区间
    python precompute.py --since 2026-10-01           # 等价于 --start（兼容旧用法）
    python precompute.py --date 2026-10-29 --skip-streak       # 只跑 picks，不重跑 streak
"""
import argparse
import time
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from config import CONCEPT_DIR, CONCEPT_PICKS_DIR
from queries import (
    _list_partition_dates,
    rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
    _get_tops_and_scores, _build_intersection_picks, _save_picks_cache,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="强制重算（含 streak 全量重建）")
    parser.add_argument("--date", default=None,
                        help="只处理单个日期，格式 YYYY-MM-DD")
    parser.add_argument("--start", default=None,
                        help="起始日期（含），格式 YYYY-MM-DD")
    parser.add_argument("--end", default=None,
                        help="结束日期（含），格式 YYYY-MM-DD")
    parser.add_argument("--since", default=None,
                        help="等价于 --start（兼容旧用法）")
    parser.add_argument("--skip-streak", action="store_true",
                        help="跳过 limit_up_streak 重建（只跑 picks）")
    args = parser.parse_args()

    # 参数互斥校验
    if args.date and (args.start or args.end or args.since):
        print("[precompute] --date 不能与 --start/--end/--since 同时使用")
        return

    t0 = time.time()

    # ── 1. daily_pct ──
    print("[precompute] 检查 daily_pct…")
    rebuild_market_amount()
    rebuild_daily_pct(force_recent_days=0)
    print(f"[precompute] daily_pct OK（{time.time()-t0:.1f}s）")

    # ── 2. 确定目标日期 ──
    all_td = _list_partition_dates(CONCEPT_DIR)

    if args.date:
        # 单日模式
        if args.date in all_td:
            all_td = [args.date]
        else:
            print(f"[precompute] {args.date} 不在交易日期列表中（或无概念数据）")
            return
    else:
        # 区间模式
        start = args.start or args.since
        if start:
            all_td = [d for d in all_td if d >= start]
        if args.end:
            all_td = [d for d in all_td if d <= args.end]

    if not all_td:
        print("[precompute] 没有符合条件的日期")
        return

    if len(all_td) == 1:
        print(f"[precompute] 目标日期 {all_td[0]}（单日）")
    else:
        print(f"[precompute] 目标日期 {len(all_td)} 天（{all_td[0]} ~ {all_td[-1]}）")

    # ── 3. streak ──
    if args.skip_streak:
        print("[precompute] 跳过 limit_up_streak")
    else:
        print("[precompute] 生成 limit_up_streak…")
        n = rebuild_limit_up_streak(force=args.force, limit_days=9999)
        print(f"[precompute] streak {n} 天（{time.time()-t0:.1f}s）")

    # ── 4. concept_picks ──
    print("[precompute] 生成 concept_picks…")
    n_picks = 0
    skipped = 0
    failed = 0
    for i, d in enumerate(all_td):
        picks_path = CONCEPT_PICKS_DIR / f"trade_date={d}" / "part.parquet"
        if picks_path.exists() and not args.force:
            skipped += 1
            continue
        try:
            market_total, top_amount, top_pct, top_limit_up, concept_scores = \
                _get_tops_and_scores(d)
            picks = _build_intersection_picks(
                top_amount, top_pct, top_limit_up, d, market_total,
                concept_scores)
            _save_picks_cache(d, picks)
            n_picks += 1
        except Exception as e:
            print(f"  {d} 失败: {e}")
            failed += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(all_td)}，已生成 {n_picks}，"
                  f"失败 {failed}，用时 {time.time()-t0:.0f}s")

    print(f"[precompute] 完成：picks 新生成 {n_picks}，跳过 {skipped}，"
          f"失败 {failed}，总用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()