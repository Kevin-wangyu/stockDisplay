"""只重算 picks（不动 _tops_cache / streak / daily_pct）。

用途：
  - 改 importance 公式（gld 加权、n^0.4 等）后，只重算 picks
  - 从 _tops_cache 读三榜 + scores，跑 _build_intersection_picks

用法：
    python scripts/recompute_picks.py                    # 全量
    python scripts/recompute_picks.py --since 2025-01-01 # 只算 2025 年后
    python scripts/recompute_picks.py --date 2024-10-09  # 单日
    python scripts/recompute_picks.py --last 30          # 最近 30 天
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import CONCEPT_DIR
from core.loaders import _list_partition_dates
from core.tops_cache import get_tops_and_scores_cached
from core.picks_cache import _save_picks_cache
from queries import _build_intersection_picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--last", type=int, default=None)
    args = ap.parse_args()

    if args.date:
        dates = [args.date]
    else:
        dates = _list_partition_dates(CONCEPT_DIR)
        if args.since:
            dates = [d for d in dates if d >= args.since]
        if args.last:
            dates = dates[-args.last:]

    print(f"[recompute_picks] {len(dates)} 天待重算")

    t0 = time.time()
    ok = 0
    fail = 0

    for i, d in enumerate(dates, 1):
        try:
            mt, ta, tp, tl, cs = get_tops_and_scores_cached(d)
            picks = _build_intersection_picks(ta, tp, tl, d, mt, cs)
            _save_picks_cache(d, picks)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  {d} 失败: {type(e).__name__}: {e}")

        if i % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(dates) - i)
            print(f"  [{i}/{len(dates)}] 成功 {ok} 失败 {fail} | "
                  f"已用 {elapsed:.0f}s | 预计还需 {eta:.0f}s")

    elapsed = time.time() - t0
    print(f"\n[recompute_picks] 完成: {ok} 成功, {fail} 失败, 耗时 {elapsed:.0f}s")


if __name__ == "__main__":
    main()