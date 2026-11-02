"""强制重算指定日期的所有榜单相关数据（调试用）。

流程：
  1. market_amount（增量补缺）
  2. daily_pct（最近 10 天强制重算）
  3. limit_up_streak（最近 30 天强制重算）
  4. 清进程内 lru_cache
  5. 重算 picks（含 importance）
  6. 验证返回

用法：
    python scripts/recompute_target.py 2026-10-30
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import logging

from config import setup_logging, CONCEPT_DIR, CONCEPT_PICKS_DIR

from core.rebuild import (
    rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
)
from core.loaders import (
    _load_stock_index_by_date, _load_pct_today,
    _load_alias_map, _load_alias_reverse,
)
from core.picks_cache import _save_picks_cache

from queries import (
    _get_tops_and_scores,
    _build_intersection_picks,
    get_concept_date_top10,
    get_limit_up_ladder,
)

setup_logging()
logger = logging.getLogger("recompute")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        print("用法: python scripts/recompute_target.py YYYY-MM-DD")
        sys.exit(1)

    # 前置：concept_flow 必须存在
    cf_p = CONCEPT_DIR / f"trade_date={target}" / "part.parquet"
    if not cf_p.exists():
        print(f"✗ concept_flow/{target} 不存在，请先跑 ETL：")
        print(f"    python -m etl.daily_update --date {target.replace('-', '')}")
        sys.exit(2)

    print("=" * 70)
    print(f"重算 {target}")
    print("=" * 70)

    # ── Step 1 ──
    print("\n[1/6] market_amount")
    print(f"    新增 {rebuild_market_amount()} 天")

    # ── Step 2 ──
    print("\n[2/6] daily_pct（recent 10 天强制重算）")
    print(f"    写入 {rebuild_daily_pct(force_recent_days=10)} 天")

    # ── Step 3 ──
    print("\n[3/6] limit_up_streak（recent 30 天强制重算）")
    print(f"    写入 {rebuild_limit_up_streak(force=True, limit_days=60, force_recent_days=30)} 天")

    # ── Step 4 ──
    print("\n[4/6] 清 lru_cache")
    for fn in [_get_tops_and_scores, _load_stock_index_by_date,
               _load_pct_today, _load_alias_map, _load_alias_reverse,
               get_concept_date_top10, get_limit_up_ladder]:
        fn.cache_clear()
    print("    OK")

    # ── Step 5 ──
    print("\n[5/6] concept_picks")
    picks_p = CONCEPT_PICKS_DIR / f"trade_date={target}" / "part.parquet"
    if picks_p.exists():
        picks_p.unlink()
        print(f"    删除旧缓存 {picks_p}")

    mt, ta, tp, tl, cs = _get_tops_and_scores(target)               # ← 5 个返回值
    picks = _build_intersection_picks(ta, tp, tl, target, mt, cs)   # ← 6 个参数
    _save_picks_cache(target, picks)
    print(f"    生成 {len(picks)} 条 → {picks_p}")

    # ── Step 6 ──
    print("\n[6/6] 验证")
    result = get_concept_date_top10(target)
    print(f"    成交额榜   {len(result['top_amount'])} 条")
    print(f"    涨跌幅榜   {len(result['top_pct'])} 条")
    print(f"    涨停数榜   {len(result['top_limit_up'])} 条")
    print(f"    交集选股   {len(result['intersection_picks'])} 条  cached={result['picks_cached']}")
    print(f"    市场总额   {result['market_total_amount']:.4e}")

    lad = get_limit_up_ladder(target, min_days=2)
    print(f"    涨停天梯   {len(lad['stocks'])} 只  cached={lad.get('cached', True)}")

    if result["intersection_picks"]:
        p = result["intersection_picks"][0]
        imp = p.get("importance_index")
        print(f"    TOP1 {p['code']} {p['name']}: importance={imp}")
        if imp is None:
            print("    ⚠ importance 为空！请检查 concept_scores 计算")

    print("\n完成。若前端仍是旧数据 → 重启 python serve.py 清服务端 lru_cache。")


if __name__ == "__main__":
    main()