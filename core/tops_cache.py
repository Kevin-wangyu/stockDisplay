"""_tops_cache：三榜 + 概念分的持久化缓存。

目的：
  - 把 _get_tops_and_scores 的昂贵计算（0.6s/天）落盘
  - picks 公式改动时只重算 picks（0.3s/天），不动缓存
  - 数据源更新时增量补缓存

存储：
  data/_tops_cache/v1/bars.parquet   —— 三榜
  data/_tops_cache/v1/meta.parquet   —— concept_scores + market summary

版本控制：改榜单公式（归成比/涨停数/涨跌幅榜逻辑）→ 改 TOPS_CACHE_VERSION
        改 picks 公式（gld 加权/importance）→ 不用改版本，直接 recompute_picks
"""
from __future__ import annotations

import logging
from pathlib import Path
import time
import pandas as pd
from functools import lru_cache

from config import DATA_DIR

logger = logging.getLogger(__name__)

TOPS_CACHE_VERSION = "v1"
TOPS_CACHE_DIR = DATA_DIR / "_tops_cache" / TOPS_CACHE_VERSION
BARS_PATH = TOPS_CACHE_DIR / "bars.parquet"
META_PATH = TOPS_CACHE_DIR / "meta.parquet"


# ══════════════════════════════════════════════════════════════
# 序列化
# ══════════════════════════════════════════════════════════════

def _serialize_bars(d: str, top_amount, top_pct, top_limit_up) -> list[dict]:
    """三榜 → parquet 行。"""
    rows = []
    for list_type, items in [
        ("top_amount", top_amount or []),
        ("top_pct", top_pct or []),
        ("top_limit_up", top_limit_up or []),
    ]:
        for rank, it in enumerate(items, 1):
            ts = it.get("top_stock") or {}
            rows.append({
                "trade_date": d,
                "list_type": list_type,
                "rank": rank,
                "code": it.get("code"),
                "name": it.get("name"),
                "amount": it.get("amount"),
                "turnover_rate": it.get("turnover_rate"),
                "total_market_cap": it.get("total_market_cap"),
                "pct_change": it.get("pct_change"),
                "amount_pct": it.get("amount_pct"),
                "cap_pct": it.get("cap_pct"),
                "norm_amount_ratio": it.get("norm_amount_ratio"),
                "limit_up_count": it.get("limit_up_count"),
                "top_stock_code": ts.get("code"),
                "top_stock_name": ts.get("name"),
                "top_stock_pct": ts.get("pct"),
            })
    return rows


def _serialize_meta(d: str, market_total, concept_scores) -> list[dict]:
    """concept_scores + market summary → parquet 行。"""
    rows = []
    # market 行
    rows.append({
        "trade_date": d,
        "row_type": "market",
        "concept_name": None,
        "score": None,
        "ratio_part": None,
        "limitup_part": None,
        "pct_part": None,
        "market_total": market_total,
    })
    # score 行
    for cf, info in (concept_scores or {}).items():
        parts = info.get("parts") or {}
        rows.append({
            "trade_date": d,
            "row_type": "score",
            "concept_name": cf,
            "score": info.get("score"),
            "ratio_part": parts.get("ratio"),
            "limitup_part": parts.get("limitup"),
            "pct_part": parts.get("pct"),
            "market_total": None,
        })
    return rows


# ══════════════════════════════════════════════════════════════
# 加载（进程内缓存）
# ══════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_tops_cache():
    """加载全量缓存到内存。

    返回: (bars_map, scores_map, market_map)

    bars_map:    {trade_date: {"top_amount": [{...}], "top_pct": [...], "top_limit_up": [...]}}
    scores_map:  {trade_date: {concept: {"score": float, "parts": {...}}}}
    market_map:  {trade_date: {"market_total": float}}
    """
    bars_map, scores_map, market_map = {}, {}, {}

    if BARS_PATH.exists():
        df = pd.read_parquet(BARS_PATH)
        for _, r in df.iterrows():
            d = str(r["trade_date"])[:10]
            list_type = r["list_type"]
            item = {
                "code": r["code"],
                "name": r["name"],
                "amount": _num(r.get("amount")),
                "turnover_rate": _num(r.get("turnover_rate")),
                "total_market_cap": _num(r.get("total_market_cap")),
                "pct_change": _num(r.get("pct_change")),
                "amount_pct": _num(r.get("amount_pct")),
                "cap_pct": _num(r.get("cap_pct")),
                "norm_amount_ratio": _num(r.get("norm_amount_ratio")),
                "limit_up_count": int(r["limit_up_count"]) if pd.notna(r.get("limit_up_count")) else 0,
                "top_stock": None,
                "_rank": int(r["rank"]) if pd.notna(r.get("rank")) else 999,
            }
            if pd.notna(r.get("top_stock_code")):
                item["top_stock"] = {
                    "code": r["top_stock_code"],
                    "name": r["top_stock_name"],
                    "pct": _num(r.get("top_stock_pct")),
                }
            bars_map.setdefault(d, {}).setdefault(list_type, []).append(item)

        # ⭐ 按 rank 排序（保持榜单顺序），然后移除内部字段
        for d in bars_map:
            for lt in bars_map[d]:
                bars_map[d][lt].sort(key=lambda x: x.get("_rank", 999))
                for item in bars_map[d][lt]:
                    item.pop("_rank", None)

    if META_PATH.exists():
        df = pd.read_parquet(META_PATH)
        for _, r in df.iterrows():
            d = str(r["trade_date"])[:10]
            row_type = r["row_type"]
            if row_type == "score":
                cf = r["concept_name"]
                if not cf:
                    continue
                scores_map.setdefault(d, {})[cf] = {
                    "score": _num(r.get("score")) or 0.0,
                    "parts": {
                        "ratio": _num(r.get("ratio_part")) or 0.0,
                        "limitup": _num(r.get("limitup_part")) or 0.0,
                        "pct": _num(r.get("pct_part")) or 0.0,
                    },
                }
            elif row_type == "market":
                market_map[d] = {
                    "market_total": _num(r.get("market_total")) or 0.0,
                }

    return bars_map, scores_map, market_map

def _num(v):
    if v is None or not pd.notna(v):
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════
# 写入（增量）
# ══════════════════════════════════════════════════════════════

def _append_tops_cache(trade_date: str, bars_rows: list[dict],
                       meta_rows: list[dict]) -> None:
    """增量追加某天的缓存（删同日旧数据 → 追加）。"""
    TOPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # bars
    new_bars = pd.DataFrame(bars_rows)
    if BARS_PATH.exists():
        old = pd.read_parquet(BARS_PATH)
        old = old[old["trade_date"] != trade_date]
        merged = pd.concat([old, new_bars], ignore_index=True)
    else:
        merged = new_bars
    merged.to_parquet(BARS_PATH, index=False)

    # meta
    new_meta = pd.DataFrame(meta_rows)
    if META_PATH.exists():
        old = pd.read_parquet(META_PATH)
        old = old[old["trade_date"] != trade_date]
        merged = pd.concat([old, new_meta], ignore_index=True)
    else:
        merged = new_meta
    merged.to_parquet(META_PATH, index=False)

    # 清内存缓存
    _load_tops_cache.cache_clear()


# ══════════════════════════════════════════════════════════════
# 批量重建
# ══════════════════════════════════════════════════════════════

def rebuild_tops_cache(trade_dates: list[str], force: bool = False) -> int:
    """批量重建缓存。返回实际生成的天数。"""
    from queries.concept import _get_tops_and_scores

    if not force:
        bars_map, _, _ = _load_tops_cache()
        trade_dates = [d for d in trade_dates if d not in bars_map]

    if not trade_dates:
        print("[tops_cache] 已是最新，跳过", flush=True)
        return 0

    total = len(trade_dates)
    print(f"[tops_cache] 待生成 {total} 天", flush=True)

    all_bars = []
    all_meta = []
    ok = 0
    fail = 0
    t_start = time.time()

    for i, d in enumerate(trade_dates, 1):
        t_day = time.time()
        try:
            result = _get_tops_and_scores(d)
            market_total, top_amount, top_pct, top_limit_up, scores = result
            all_bars.extend(_serialize_bars(d, top_amount, top_pct, top_limit_up))
            all_meta.extend(_serialize_meta(d, market_total, scores))
            ok += 1
            day_time = time.time() - t_day
            if day_time > 2:
                print(f"  [慢] {d} 耗时 {day_time:.1f}s", flush=True)
        except Exception as e:
            fail += 1
            print(f"  [失败] {d}: {type(e).__name__}: {e}", flush=True)

        # ⭐ 每 20 天打进度
        if i % 20 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / i * (total - i)
            print(f"  [{i}/{total}] 成功 {ok} 失败 {fail} | "
                  f"已用 {elapsed:.0f}s | 预计还需 {eta:.0f}s", flush=True)

    if not all_bars:
        print("[tops_cache] 无数据可写", flush=True)
        return 0

    # 批量写（一次 IO）
    print(f"[tops_cache] 写入 parquet（{len(all_bars)} 行）…", flush=True)
    t_write = time.time()

    TOPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    new_bars = pd.DataFrame(all_bars)
    new_meta = pd.DataFrame(all_meta)
    date_set = set(trade_dates)

    if BARS_PATH.exists() and not force:
        old = pd.read_parquet(BARS_PATH)
        old = old[~old["trade_date"].isin(date_set)]
        merged = pd.concat([old, new_bars], ignore_index=True)
    else:
        merged = new_bars
    merged.to_parquet(BARS_PATH, index=False)

    if META_PATH.exists() and not force:
        old = pd.read_parquet(META_PATH)
        old = old[~old["trade_date"].isin(date_set)]
        merged = pd.concat([old, new_meta], ignore_index=True)
    else:
        merged = new_meta
    merged.to_parquet(META_PATH, index=False)

    _load_tops_cache.cache_clear()
    print(f"[tops_cache] 写盘完成 {time.time() - t_write:.1f}s", flush=True)
    print(f"[tops_cache] 完成：{ok} 天（失败 {fail}），总耗时 {time.time() - t_start:.0f}s", flush=True)
    return ok


# ══════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════

def get_tops_and_scores_cached(trade_date: str):
    """带缓存的 _get_tops_and_scores。

    返回: (market_total, top_amount, top_pct, top_limit_up, concept_scores)

    - 缓存命中：< 1ms
    - 缓存 miss：调 _get_tops_and_scores (~0.6s)，自动写缓存
    """
    bars_map, scores_map, market_map = _load_tops_cache()

    if trade_date in bars_map:
        bars = bars_map[trade_date]
        scores = scores_map.get(trade_date, {})
        market = market_map.get(trade_date, {})
        return (
            market.get("market_total", 0.0),
            bars.get("top_amount", []),
            bars.get("top_pct", []),
            bars.get("top_limit_up", []),
            scores,
        )

    # 缓存 miss → 实时算
    from queries.concept import _get_tops_and_scores
    logger.info("[tops_cache] %s 缓存缺失，实时计算", trade_date)
    result = _get_tops_and_scores(trade_date)

    # 写缓存
    market_total, top_amount, top_pct, top_limit_up, scores = result
    try:
        _append_tops_cache(
            trade_date,
            _serialize_bars(trade_date, top_amount, top_pct, top_limit_up),
            _serialize_meta(trade_date, market_total, scores),
        )
    except Exception:
        logger.exception("[tops_cache] 写缓存失败 %s", trade_date)

    return result


def invalidate_tops_cache(trade_date: str) -> None:
    """删除某天缓存（数据源变了时调用）。"""
    for p in (BARS_PATH, META_PATH):
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df = df[df["trade_date"] != trade_date]
        df.to_parquet(p, index=False)
    _load_tops_cache.cache_clear()


def cache_stats() -> dict:
    """返回缓存状态（调试用）。"""
    bars_map, scores_map, market_map = _load_tops_cache()
    return {
        "days": len(bars_map),
        "bars_rows": sum(len(v) for d in bars_map.values() for v in d.values()),
        "scores_days": len(scores_map),
        "market_days": len(market_map),
        "bars_size_mb": BARS_PATH.stat().st_size / 1e6 if BARS_PATH.exists() else 0,
        "meta_size_mb": META_PATH.stat().st_size / 1e6 if META_PATH.exists() else 0,
    }