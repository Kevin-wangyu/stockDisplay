"""强制重算指定日期的五榜相关数据。

用法：
    python recompute_target.py 2026-10-23
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import logging
import pandas as pd

from config import setup_logging
setup_logging()
logger = logging.getLogger("recompute")

TARGET = sys.argv[1] if len(sys.argv) > 1 else None
if not TARGET:
    print("用法: python recompute_target.py YYYY-MM-DD")
    sys.exit(1)

from queries import (
    rebuild_market_amount,
    rebuild_daily_pct,
    rebuild_limit_up_streak,
    _get_tops_and_scores,
    _build_intersection_picks,
    _save_picks_cache,
    _load_stock_index_by_date,
    _load_pct_today,
    _load_alias_map,
    _load_alias_reverse,
    get_concept_date_top10,
    get_limit_up_ladder,
    CONCEPT_PICKS_DIR,
    CONCEPT_DATE_DIR,
    DAILY_PCT_DIR,
)

# ── 前置：确认 concept_flow 存在，否则 ETL 才能救 ──
from config import CONCEPT_DIR
cf_p = CONCEPT_DIR / f"trade_date={TARGET}" / "part.parquet"
if not cf_p.exists():
    print(f"✗ concept_flow/{TARGET} 不存在，请先跑 ETL：")
    print(f"    python -m etl.daily_update --date {TARGET.replace('-', '')}")
    sys.exit(2)

print("=" * 70)
print(f"重算 {TARGET}")
print("=" * 70)

# ── Step 1: market_amount（增量，只会补缺失）──
print("\n[1/6] market_amount")
n = rebuild_market_amount()
print(f"    新增 {n} 天")

# ── Step 2: daily_pct（强制重算目标日，顺带 buffer）──
print("\n[2/6] daily_pct（recent 10 天强制重算）")
n = rebuild_daily_pct(force_recent_days=10)
print(f"    写入 {n} 天")

# ── Step 3: limit_up_streak（强制重算目标日，含 buffer 30 天）──
print("\n[3/6] limit_up_streak（recent 30 天强制重算）")
n = rebuild_limit_up_streak(force=True, limit_days=60, force_recent_days=30)
print(f"    写入 {n} 天")

# ── Step 4: 清缓存（本进程内）──
print("\n[4/6] 清 lru_cache")
for fn in [_get_tops_and_scores, _load_stock_index_by_date,
           _load_pct_today, _load_alias_map, _load_alias_reverse,
           get_concept_date_top10, get_limit_up_ladder]:
    fn.cache_clear()
print("    OK")

# ── Step 5: 删除并重算 picks ──
print("\n[5/6] concept_picks")
picks_p = CONCEPT_PICKS_DIR / f"trade_date={TARGET}" / "part.parquet"
if picks_p.exists():
    picks_p.unlink()
    print(f"    删除旧缓存 {picks_p}")
mt, ta, tp, tl, cs = _get_tops_and_scores(TARGET)
picks = _build_intersection_picks(ta, tp, tl, TARGET, mt)
_save_picks_cache(TARGET, picks)
print(f"    生成 {len(picks)} 条 → {picks_p}")

# ── Step 6: 验证 ──
print("\n[6/6] 验证")
result = get_concept_date_top10(TARGET)
print(f"    成交额榜   {len(result['top_amount'])} 条")
print(f"    涨跌幅榜   {len(result['top_pct'])} 条")
print(f"    涨停数榜   {len(result['top_limit_up'])} 条")
print(f"    交集选股   {len(result['intersection_picks'])} 条  cached={result['picks_cached']}")
print(f"    市场总额   {result['market_total_amount']:.4e}")
lad = get_limit_up_ladder(TARGET, min_days=2)
print(f"    涨停天梯   {len(lad['stocks'])} 只  cached={lad.get('cached', True)}")

print("\n完成。若前端仍是旧数据 → 重启 python serve.py 清服务端 lru_cache。")