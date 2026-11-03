"""重建 _tops_cache（三榜 + concept_scores）。

用法：
    python scripts/rebuild_tops_cache.py                  # 增量补缺
    python scripts/rebuild_tops_cache.py --force          # 全量重建
    python scripts/rebuild_tops_cache.py --since 2025-01-01
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

# ⭐ 关键：配置日志级别，让 info 可见
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from config import CONCEPT_DIR
from core.loaders import _list_partition_dates
from core.tops_cache import rebuild_tops_cache, cache_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="全量重建")
    ap.add_argument("--since", default=None)
    args = ap.parse_args()

    all_dates = _list_partition_dates(CONCEPT_DIR)
    if args.since:
        all_dates = [d for d in all_dates if d >= args.since]

    print(f"[rebuild_tops] 目标 {len(all_dates)} 天，force={args.force}", flush=True)

    t0 = time.time()
    n = rebuild_tops_cache(all_dates, force=args.force)
    elapsed = time.time() - t0

    stats = cache_stats()
    print(f"\n[rebuild_tops] 完成：{n} 天，耗时 {elapsed:.0f}s", flush=True)
    print(f"[rebuild_tops] 缓存状态: {stats}", flush=True)


if __name__ == "__main__":
    main()