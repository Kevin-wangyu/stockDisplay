"""批量预生成所有历史日期的榜单缓存。

用法：
    python precompute.py                 # 补齐所有缺的
    python precompute.py --force         # 全部重算
    python precompute.py --since 2025-01-01
"""
import argparse
import time
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from config import CONCEPT_DIR, CONCEPT_PICKS_DIR, LIMIT_UP_STREAK_DIR
from queries import (
    _list_partition_dates,
    rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
    _get_tops_and_scores, _build_intersection_picks, _save_picks_cache,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--since", default=None)
    args = parser.parse_args()

    t0 = time.time()

    # ── 1. 先保证 daily_pct 全量 ──
    print("[precompute] 检查 daily_pct…")
    rebuild_market_amount()
    rebuild_daily_pct(force_recent_days=0)
    print(f"[precompute] daily_pct OK（{time.time()-t0:.1f}s）")

    all_td = _list_partition_dates(CONCEPT_DIR)
    if args.since:
        all_td = [d for d in all_td if d >= args.since]
    print(f"[precompute] 目标日期 {len(all_td)} 天")

    # ── 2. limit_up_streak 批量（一次全量，~3s）──
    print("[precompute] 生成 limit_up_streak…")
    n = rebuild_limit_up_streak(force=args.force, limit_days=9999)
    print(f"[precompute] streak {n} 天（{time.time()-t0:.1f}s）")

    # ── 3. concept_picks 批量 ──
    print("[precompute] 生成 concept_picks…")
    n_picks = 0
    skipped = 0
    for i, d in enumerate(all_td):
        picks_path = CONCEPT_PICKS_DIR / f"trade_date={d}" / "part.parquet"
        if picks_path.exists() and not args.force:
            skipped += 1
            continue
        try:
            market_total, top_amount, top_pct, top_limit_up, _ = \
                _get_tops_and_scores(d)
            picks = _build_intersection_picks(
                top_amount, top_pct, top_limit_up, d, market_total)
            _save_picks_cache(d, picks)
            n_picks += 1
        except Exception as e:
            print(f"  {d} 失败: {e}")
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(all_td)}，已生成 {n_picks}，"
                  f"用时 {time.time()-t0:.0f}s")

    print(f"[precompute] 完成：picks 新生成 {n_picks}，跳过 {skipped}，"
          f"总用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()