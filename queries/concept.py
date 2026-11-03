"""概念页：三榜 / 天梯 / 交集 / 涨停股 / 概念分。"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date as _date, timedelta as _td
from functools import lru_cache               # ⭐ 必须加！否则 @lru_cache 崩

import pandas as pd

from config import MAP_FILE, DATA_DIR, CONCEPT_DATE_DIR
from core.db import (
    _q_df, _q_fetchone, _q_fetchall,
    QUOTE_GLOB, CONCEPT_GLOB, DAILY_PCT_GLOB, INDUSTRY_GLOB,
    BASE_DIR, COMPACT_ROOT,
    MARKET_AMOUNT_FILE,        # _get_tops_and_scores 里用（如果直接读了）
    CONCEPT_ALIAS_FILE,        # _resolve_map_name 里用
)
from core.helpers import (
    _limit_up_threshold, _board_label, _json_safe, _market_avg_amount,GLD_MAX
)
from core.loaders import (
    _load_stock_index_by_date, _load_pct_today,
    _list_partition_dates,
    _list_daily_pct_dates,     # ⭐ get_trading_day_neighbor 里用
    _load_alias_reverse,
    get_concept_gld,
)
from core.rebuild import (
    rebuild_limit_up_streak, _rebuild_streak_for_date,
    LIMIT_UP_STREAK_DIR,
)
from core.picks_cache import (
    _load_cached_picks, _save_picks_cache, _is_stable_date,
    CONCEPT_PICKS_DIR,
)
from trading_calendar import trading_days

logger = logging.getLogger(__name__)

# ── 三榜 ──
def _clean_concept_rows(df):
    """转 records，同时把 inf/nan 转 0.0，防止 JSON 序列化崩溃。"""
    def _safe(v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return f if math.isfinite(f) else 0.0

    out = []
    for _, r in df.iterrows():
        out.append({
            "code":  r["code"],
            "name":  r["name"],
            "amount":           _safe(r["amount"]),
            "turnover_rate":    _safe(r["turnover_rate"]),
            "total_market_cap": _safe(r["total_market_cap"]),
            "pct_change":       _safe(r["pct_change"]),
        })
    return out



def _compute_top_limit_up(base_df, trade_date, topn=10):
    """按涨停数降序取 TOP10；不足用成交额补齐。向量化。"""
    code_info, cf2codes = _load_stock_index_by_date(trade_date)
    pct_map = _load_pct_today(trade_date)

    base_map = {r["name"]: r for _, r in base_df.iterrows()}

    # 每个概念的涨停数（一次计算所有）
    cf_lu = {}
    for cf, codes in cf2codes.items():
        if cf not in base_map:
            continue
        lu = 0
        for c in codes:
            p = pct_map.get(c)
            if p is None:
                continue
            prefix = str(c)[:2]
            th = 29.8 if prefix in ("8", "4", "92") else \
                 19.8 if prefix in ("30", "68") else 9.8
            if p >= th:
                lu += 1
        cf_lu[cf] = lu

    if not cf_lu:
        return []

    def _amt(cf):
        v = base_map[cf]["amount"]
        return float(v) if pd.notna(v) else 0.0

    top_names = sorted(cf_lu.keys(),
                       key=lambda cf: (-cf_lu[cf], -_amt(cf)))[:topn]
    name_order = {cf: i for i, cf in enumerate(top_names)}

    sub = base_df[base_df["name"].isin(top_names)].copy()
    if sub.empty:
        return []
    sub = sub.drop_duplicates(subset=["name"], keep="last")   # ← 新增
    sub["__order"] = sub["name"].map(name_order)
    sub = sub.sort_values("__order").drop(columns="__order")
    return _clean_concept_rows(sub)



def _compute_top_amount_by_ratio(base_df, trade_date, market_total, topn=10):
    """成交额榜：按归成比（A/B）降序，仅取 A > 10% 的概念。

    A = 该概念成交额 / 市场总成交额 × 100（%）
    B = 该概念自由流通市值 / 市场总流通市值 × 100（%）
    归成比 = A / B（消除市值占比影响）
    """
    import math

    # ── 市场总流通市值（industry_flow 当日 SUM(total_market_cap)）──
    row = _q_fetchone(
        f"""
        SELECT SUM(total_market_cap) AS total_cap
        FROM read_parquet({INDUSTRY_GLOB}, hive_partitioning=true, union_by_name=true)
        WHERE trade_date = ?
        """, [trade_date])

    market_cap = 0.0
    if row and row[0] is not None:
        try:
            v = float(row[0])
            if math.isfinite(v):
                market_cap = v
        except (TypeError, ValueError):
            pass

    if market_cap <= 0 or market_total <= 0:
        logger.warning(
            "[top_amount] %s 缺少 market_cap=%.0f / market_total=%.0f，"
            "回退成交额排序", trade_date, market_cap, market_total)
        return _clean_concept_rows(base_df.nlargest(topn, "amount"))

    df = base_df.copy()
    df["amount_pct"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0) / market_total * 100.0
    df["cap_pct"]    = pd.to_numeric(df["total_market_cap"], errors="coerce").fillna(0.0) / market_cap * 100.0
    df["norm_amount_ratio"] = df.apply(
        lambda r: r["amount_pct"] / r["cap_pct"] if r["cap_pct"] > 0 else 0.0,
        axis=1,
    )

    # ── 筛选 A > 10% ──
    filtered = df[df["amount_pct"] > 10.0].copy()

    if len(filtered) >= topn:
        top = filtered.nlargest(topn, "norm_amount_ratio")
    elif not filtered.empty:
        head = filtered.sort_values("norm_amount_ratio", ascending=False)
        rest = df[~df.index.isin(filtered.index)].copy()
        rest = rest.sort_values("norm_amount_ratio", ascending=False)
        top = pd.concat(
            [head, rest.head(topn - len(head))], ignore_index=False)
        logger.info(
            "[top_amount] %s A>10%% 只有 %d 个，按归成比补齐至 %d 个",
            trade_date, len(head), len(top))
    else:
        logger.info(
            "[top_amount] %s 无概念 A>10%%（最高 %.2f%%），全市场按归成比取 TOP%d",
            trade_date, float(df["amount_pct"].max()) if len(df) else 0.0, topn)
        top = df.nlargest(topn, "norm_amount_ratio")

    def _safe(v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return f if math.isfinite(f) else 0.0

    rows = []
    for _, r in top.iterrows():
        amount_pct = _safe(r["amount_pct"])
        rows.append({
            "code":  r["code"],
            "name":  r["name"],
            "amount":           _safe(r["amount"]),
            "turnover_rate":    _safe(r["turnover_rate"]),
            "total_market_cap": _safe(r["total_market_cap"]),
            "pct_change":       _safe(r["pct_change"]),
            "amount_pct":        amount_pct,
            "cap_pct":           _safe(r["cap_pct"]),
            "norm_amount_ratio": _safe(r["norm_amount_ratio"]),
            "is_strong": amount_pct > 10.0,   # ⭐ A>10% = 主线
        })
    return rows



def _enrich_with_stocks(lists, trade_date):
    """三榜里每个概念附加 limit_up_count / top_stock（原地修改）。"""
    code_info, cf2codes = _load_stock_index_by_date(trade_date)
    pct_today = _load_pct_today(trade_date)

    for lst in lists:
        for it in lst:
            codes = cf2codes.get(it["name"], [])
            matched = []
            for c in codes:
                if c in pct_today:
                    name = code_info.get(c, ("", frozenset()))[0]
                    matched.append((c, name, pct_today[c]))

            lu = sum(1 for c, _, p in matched
                     if p >= _limit_up_threshold(c))
            top = max(matched, key=lambda x: x[2]) if matched else None
            it["limit_up_count"] = lu
            if top:
                pct_val = float(top[2])
                if not math.isfinite(pct_val):
                    pct_val = 0.0
                it["top_stock"] = {
                    "code": top[0], "name": top[1], "pct": pct_val,
                }
            else:
                it["top_stock"] = None



@lru_cache(maxsize=32)
def _get_tops_and_scores(trade_date: str):
    """一次算齐：市场总成交额 + 三榜 + 概念分。

    返回 (market_total, top_amount, top_pct, top_limit_up, concept_scores)。
    lru_cache 缓存 32 天，两个接口共用，避免重复 SQL。
    """
    row = _q_fetchone(
        f"SELECT total_amount FROM read_parquet('{MARKET_AMOUNT_FILE}') "
        f"WHERE trade_date = ?", [trade_date])
    market_total = float(row[0]) if row and row[0] is not None else 0.0

    base = _q_df(
        f"""
        SELECT DISTINCT code, name, amount, turnover_rate,
                        total_market_cap, pct_change
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true,
                          union_by_name=true)
        WHERE trade_date = ?
        """, [trade_date])
    if base.empty:
        raise ValueError(f"{trade_date} 无概念数据")

    top_amount   = _compute_top_amount_by_ratio(base, trade_date, market_total, topn=10)
    top_pct      = _clean_concept_rows(base.nlargest(10, "pct_change"))
    top_limit_up = _compute_top_limit_up(base, trade_date, topn=10)

    _enrich_with_stocks([top_amount, top_pct, top_limit_up], trade_date)

    concept_scores = _compute_concept_scores(
        top_amount, top_pct, top_limit_up, trade_date, market_total)

    return market_total, top_amount, top_pct, top_limit_up, concept_scores

def _compute_concept_colors(top_amount, top_pct, top_limit_up) -> dict[str, str]:
    """计算概念颜色标签。

    规则：
      - red:    三榜共有 且 在某榜前5（最强主线）
      - yellow: 三榜共有（不在任何榜前5）
      - blue:   在某榜前5（不是三榜共有）
      - white:  其余

    返回 {cf_name: "red"|"yellow"|"blue"|"white"}
    """
    names_amount = {it["name"] for it in top_amount}      # TOP10
    names_pct    = {it["name"] for it in top_pct}
    names_lu     = {it["name"] for it in top_limit_up}

    common3 = names_amount & names_pct & names_lu
    top5 = (
        {it["name"] for it in top_amount[:5]}
        | {it["name"] for it in top_pct[:5]}
        | {it["name"] for it in top_limit_up[:5]}
    )

    all_names = names_amount | names_pct | names_lu
    colors = {}
    for name in all_names:
        if name in common3 and name in top5:
            colors[name] = "red"
        elif name in common3:
            colors[name] = "yellow"
        elif name in top5:
            colors[name] = "blue"
        else:
            colors[name] = "white"
    return colors


# ── 重要性 ──
def _compute_concept_scores(top_amount, top_pct, top_limit_up,
                            trade_date, market_total):
    """
    基于新三榜（归成比 / 涨跌幅 / 涨停数）的概念重要性分。

    每维度 = 0.5 × 排名分 + 0.5 × 数值分
    权重：归成比 0.40 / 涨停数 0.35 / 涨跌幅 0.25
    加成：多榜命中 × (1 + 0.2 × (命中数-1))
    归一化：max_score → 1.0
    幂次：score ^ 0.8（拉开中低部）
    """
    rank_ratio = {it["name"]: i + 1 for i, it in enumerate(top_amount)}
    rank_pct   = {it["name"]: i + 1 for i, it in enumerate(top_pct)}
    rank_lu    = {it["name"]: i + 1 for i, it in enumerate(top_limit_up)}

    ratio_map = {it["name"]: float(it.get("norm_amount_ratio", 0) or 0)
                 for it in top_amount}
    pct_map   = {it["name"]: float(it.get("pct_change", 0) or 0)
                 for it in top_pct}
    lu_map    = {it["name"]: int(it.get("limit_up_count", 0) or 0)
                 for it in top_limit_up}

    max_ratio   = max(ratio_map.values(), default=0.0)
    max_pct_pos = max((v for v in pct_map.values() if v > 0), default=0.0)
    max_lu      = max(lu_map.values(), default=0)

    all_cfs = set(rank_ratio) | set(rank_pct) | set(rank_lu)

    # ── 第一轮：算 base score ──
    raw_scores = {}
    for cf in all_cfs:
        ratio_rank = (11 - rank_ratio[cf]) / 10 if cf in rank_ratio else 0.0
        ratio_raw  = ratio_map.get(cf, 0.0)
        ratio_val  = ratio_raw / max_ratio if max_ratio > 0 else 0.0
        ratio_part = 0.5 * ratio_rank + 0.5 * ratio_val

        lu_rank = (11 - rank_lu[cf]) / 10 if cf in rank_lu else 0.0
        lu_raw  = lu_map.get(cf, 0)
        lu_val  = lu_raw / max_lu if max_lu > 0 else 0.0
        lu_part = 0.5 * lu_rank + 0.5 * lu_val

        pct_rank = 0.0
        pct_val  = 0.0
        pct_raw  = pct_map.get(cf, 0.0)
        if pct_raw > 0:
            pct_rank = (11 - rank_pct[cf]) / 10 if cf in rank_pct else 0.0
            pct_val  = pct_raw / max_pct_pos if max_pct_pos > 0 else 0.0
        pct_part = 0.5 * pct_rank + 0.5 * pct_val

        base = 0.40 * ratio_part + 0.35 * lu_part + 0.25 * pct_part

        # 多榜命中加成：1榜=1.0, 2榜=1.2, 3榜=1.4
        hit = sum(1 for rk in (rank_ratio, rank_lu, rank_pct) if cf in rk)
        multi_bonus = 1.0 + 0.20 * (hit - 1)
        raw = base * multi_bonus

        raw_scores[cf] = {
            "raw": raw,
            "parts": {
                "ratio":   round(ratio_part, 3),
                "limitup": round(lu_part, 3),
                "pct":     round(pct_part, 3),
                "hit":     hit,
            },
        }

    # ── 第二轮：归一化 + 幂次拉开 ──
    max_raw = max(v["raw"] for v in raw_scores.values()) if raw_scores else 0.0
    scores = {}
    for cf, v in raw_scores.items():
        if max_raw > 0:
            normalized = v["raw"] / max_raw      # 0 ~ 1
            stretched  = normalized ** 0.8       # 拉开中低部
        else:
            stretched = 0.0
        scores[cf] = {
            "score": round(stretched, 4),
            "parts": v["parts"],
        }
    return scores



def _build_intersection_picks(top_amount, top_pct, top_limit_up,
                              trade_date, market_total, concept_scores,
                              topn=40):
    """三榜并集 → 每只股票命中概念 → 按重要性排序取 topn。

    importance = mean(matched_concept_scores) × match_count^0.3
    """
    from datetime import date as _date, timedelta as _td

    seen, cf_ordered = set(), []
    for lst in (top_amount, top_pct, top_limit_up):
        for it in lst:
            if it["name"] not in seen:
                seen.add(it["name"])
                cf_ordered.append(it["name"])
    if not cf_ordered:
        return []

    target_set = set(cf_ordered)
    order = {cf: i for i, cf in enumerate(cf_ordered)}

    code_info, _ = _load_stock_index_by_date(trade_date)
    pct_today = _load_pct_today(trade_date)

    hits = {}
    for code, (name, cs) in code_info.items():
        matched = cs & target_set
        if matched:
            hits[code] = list(matched)

    if not hits:
        return []

    picks = []
    for code, matched in hits.items():
        name = code_info[code][0]
        matched_sorted = sorted(matched, key=lambda x: order.get(x, 999))
        m = len(matched_sorted)

        # ── 重要性：gld 加权 mean × n^0.4 + 龙头加成 ──
        if m > 0:
            weighted_scores = []
            gld_detail = []
            for c in matched_sorted:
                cs_score = concept_scores.get(c, {}).get("score", 0.0)
                # gld 缺失时按"满关联"处理（不惩罚数据缺失）
                gld = get_concept_gld(code, c, default=GLD_MAX)
                gld_norm = min(gld / GLD_MAX, 1.0)
                weighted_scores.append(cs_score * gld_norm)
                gld_detail.append({"name": c, "gld": round(float(gld), 2)})

            mean_score = sum(weighted_scores) / m
            base_importance = mean_score * (m ** 0.4)

            has_leader = any(
                concept_scores.get(c, {}).get("score", 0.0) >= 0.8
                for c in matched_sorted
            )
            importance = base_importance * (1.20 if has_leader else 1.0)
        else:
            importance = 0.0
            gld_detail = []

        picks.append({
            "code": code,
            "name": name,
            "match_count": m,
            "matched_concepts": matched_sorted,          # 兼容旧前端
            "matched_concepts_detail": gld_detail,       # ⭐ 新增（含 gld）
            "importance_index": round(importance, 2),
        })

    # ── 按重要性降序（同分按命中数降序） ──
    picks.sort(key=lambda x: (-x["importance_index"],
                              -x["match_count"],
                              x["code"]))
    picks = picks[:topn]
    if not picks:
        return []

    # ── 补充行情（原有逻辑保留） ──
    codes = [p["code"] for p in picks]
    ph = ",".join(["?"] * len(codes))

    quote_df = _q_df(
        f"""
        SELECT code, close, pct_change, turnover_rate, hot_rank, amount,
               huge_order_net, big_order_net, medium_order_net, small_order_net
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true,
                          union_by_name=true)
        WHERE trade_date = ? AND code IN ({ph})
        """, [trade_date, *codes])
    qmap = {r["code"]: r for _, r in quote_df.iterrows()}

    c0 = _date.fromisoformat(trade_date)
    fwd_days = [d.isoformat() for d in trading_days(c0, c0 + _td(days=120))]
    try:
        i0 = fwd_days.index(trade_date)
    except ValueError:
        i0 = -1

    offsets = (5, 10, 20, 30)
    n2date = {n: fwd_days[i0 + n] for n in offsets
              if 0 <= i0 + n < len(fwd_days)}

    fmap = {}
    if n2date:
        date_list = list(set(n2date.values()))
        ph2 = ",".join(["?"] * len(date_list))
        fdf = _q_df(
            f"""
            SELECT code, trade_date, close
            FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true,
                              union_by_name=true)
            WHERE trade_date IN ({ph2}) AND code IN ({ph})
            """, [*date_list, *codes])
        date2n = {d: n for n, d in n2date.items()}
        for _, r in fdf.iterrows():
            n = date2n.get(str(r["trade_date"])[:10])
            if n is not None:
                fmap[(r["code"], n)] = r["close"]

    def _num(x):
        if x is None or not pd.notna(x):
            return None
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _norm(net, amount, trate):
        if net is None or amount is None or trate is None or amount == 0:
            return None
        return net * trate / amount

    for p in picks:
        r = qmap.get(p["code"])
        if r is None:
            p.update({
                "close": None,
                "pct_change": pct_today.get(p["code"]),
                "turnover_rate": None, "hot_rank": None, "amount": None,
                "huge_order_net": None, "big_order_net": None,
                "medium_order_net": None, "small_order_net": None,
                "huge_order_net_norm": None, "big_order_net_norm": None,
                "medium_order_net_norm": None, "small_order_net_norm": None,
                "fwd_5d": None, "fwd_10d": None,
                "fwd_20d": None, "fwd_30d": None,
            })
            continue

        close  = _num(r["close"])
        amount = _num(r["amount"])
        trate  = _num(r["turnover_rate"])
        n_h, n_b = _num(r["huge_order_net"]),   _num(r["big_order_net"])
        n_m, n_s = _num(r["medium_order_net"]), _num(r["small_order_net"])

        def _fwd(n):
            if close is None or close == 0:
                return None
            f = fmap.get((p["code"], n))
            return (f / close - 1) * 100 if f is not None else None

        p.update({
            "close": close,
            "pct_change": pct_today.get(p["code"]),
            "turnover_rate": trate,
            "hot_rank": _num(r["hot_rank"]),
            "amount": amount,
            "huge_order_net": n_h, "big_order_net": n_b,
            "medium_order_net": n_m, "small_order_net": n_s,
            "huge_order_net_norm":   _norm(n_h, amount, trate),
            "big_order_net_norm":    _norm(n_b, amount, trate),
            "medium_order_net_norm": _norm(n_m, amount, trate),
            "small_order_net_norm":  _norm(n_s, amount, trate),
            "fwd_5d": _fwd(5), "fwd_10d": _fwd(10),
            "fwd_20d": _fwd(20), "fwd_30d": _fwd(30),
        })
    # ⭐ 归一化 importance（当日相对强度）
    if picks:
        max_imp = max(p["importance_index"] for p in picks) or 1.0
        total = len(picks)
        for idx, p in enumerate(picks):
            p["importance_pct"] = round(p["importance_index"] / max_imp, 3)
            p["rank_in_picks"] = idx + 1
            p["total_in_picks"] = total

    return picks



# ── 对外 ──
@lru_cache(maxsize=32)
def get_concept_date_top10(trade_date: str) -> dict:
    from core.tops_cache import get_tops_and_scores_cached
    market_total, top_amount, top_pct, top_limit_up, _ = \
        get_tops_and_scores_cached(trade_date)

    picks = _load_cached_picks(trade_date) or []

    concept_colors = _compute_concept_colors(top_amount, top_pct, top_limit_up)

    return _json_safe({                              # ⭐ 兜底 nan / inf
        "trade_date": trade_date,
        "market_total_amount": market_total,
        "market_avg_5d":  _market_avg_amount(trade_date, 5),
        "market_avg_20d": _market_avg_amount(trade_date, 20),
        "top_amount":   top_amount,
        "top_pct":      top_pct,
        "top_limit_up": top_limit_up,
        "intersection_picks": picks,
        "picks_cached": bool(picks),
        "concept_colors": concept_colors,
    })



def _resolve_map_name(cf_name: str) -> str | None:
    """把 concept_flow 的概念名翻译成 stock_sector_map 里的概念名。"""
    if not CONCEPT_ALIAS_FILE.exists():
        return cf_name  # 别名表没建好时降级为原样匹配
    row = _q_fetchone(
        f"SELECT map_name FROM read_parquet('{CONCEPT_ALIAS_FILE}') "
        f"WHERE cf_name = ?", [cf_name]
    )
    return row[0] if row and row[0] else None



def get_concept_limit_up_stocks(
    concept_code: str, trade_date: str, window_days: int = 10
) -> dict:
    """某概念下，近 window_days 个交易日内出现过涨停的所有个股。

    返回按所选日期 pct_change 降序（无当日数据排最后），不截断数量。
    """
    # 1. concept_code → cf_name
    row = _q_fetchone(
        f"SELECT name FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true) "
        f"WHERE code = ? LIMIT 1", [concept_code]
    )
    if not row:
        raise ValueError(f"未找到概念 code={concept_code}")
    cf_name = row[0]
    pct_today = _load_pct_today(trade_date)


    # 3. 交易日窗口
    from datetime import date as _date, timedelta as _td
    end_d = _date.fromisoformat(trade_date)
    cand_days = [d.isoformat() for d in trading_days(end_d - _td(days=30), end_d)]
    if not cand_days:
        raise ValueError(f"交易日历里找不到 {trade_date} 附近的交易日")
    window = cand_days[-window_days:]
    window_start, window_end = window[0], window[-1]

    # 4. 当日该概念下的全部股票（concept_date 里的 key 就是 cf_name）
    code_info, cf2codes = _load_stock_index_by_date(trade_date)
    codes_of_concept = cf2codes.get(cf_name, [])
    stocks_df = pd.DataFrame({
        "code": codes_of_concept,
        "name": [code_info[c][0] for c in codes_of_concept],
    })
    if stocks_df.empty:
        return _json_safe({
            "concept_code": concept_code, "concept_name": cf_name,
            "window_start": window_start, "window_end": window_end,
            "stocks": [],
        })
    codes = stocks_df["code"].tolist()
    ph = ",".join(["?"] * len(codes))

    # 5. 窗口内每天的 pct_change（只查 daily_pct，避免全量扫描 quote）
    pct_rows = _q_df(
        f"""
        SELECT code, trade_date, pct_change
        FROM read_parquet({DAILY_PCT_GLOB}, hive_partitioning=true)
        WHERE code IN ({ph}) AND trade_date BETWEEN ? AND ?
        """, [*codes, window_start, window_end]
    )

    # 6. 逐股判定：是否有涨停 + 最早涨停日
    hit: dict[str, str] = {}
    for _, r in pct_rows.iterrows():
        if pd.isna(r["pct_change"]):
            continue
        code = r["code"]
        if float(r["pct_change"]) >= _limit_up_threshold(code):
            d = str(r["trade_date"])[:10]
            if code not in hit or d < hit[code]:
                hit[code] = d

    if not hit:
        return _json_safe({
            "concept_code": concept_code, "concept_name": cf_name,
            "window_start": window_start, "window_end": window_end,
            "stocks": [],
        })

    # 7. 组装最终列表（带当日行情 + 资金流）
    hit_codes = list(hit.keys())
    ph2 = ",".join(["?"] * len(hit_codes))

    quote_df = _q_df(
        f"""
        SELECT code, close, pct_change, turnover_rate, hot_rank, amount,
               huge_order_net, big_order_net, medium_order_net, small_order_net
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true, union_by_name=true)
        WHERE trade_date = ? AND code IN ({ph2})
        """, [trade_date, *hit_codes]
    )
    qmap = {r["code"]: r for _, r in quote_df.iterrows()}

    name_df = _q_df(
        f"SELECT code, name FROM read_parquet('{MAP_FILE}') WHERE code IN ({ph2})",
        hit_codes,
    )
    name_by_code = dict(zip(name_df["code"], name_df["name"]))

    def _num(x):
        if x is None or not pd.notna(x):
            return None
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _norm(net, amount, trate):
        if net is None or amount is None or trate is None or amount == 0:
            return None
        return net * trate / amount

    stocks = []
    for code in hit_codes:
        r = qmap.get(code)
        nm = name_by_code.get(code, "")
        if r is None:
            stocks.append({
                "code": code, "name": nm,
                "pct_change": None, "limit_up_date": hit[code],
                "close": None, "turnover_rate": None, "hot_rank": None,
                "amount": None,
                "huge_order_net": None, "big_order_net": None,
                "medium_order_net": None, "small_order_net": None,
                "huge_order_net_norm": None, "big_order_net_norm": None,
                "medium_order_net_norm": None, "small_order_net_norm": None,
            })
            continue

        close  = _num(r["close"])
        amount = _num(r["amount"])
        trate  = _num(r["turnover_rate"])
        n_h, n_b = _num(r["huge_order_net"]),   _num(r["big_order_net"])
        n_m, n_s = _num(r["medium_order_net"]), _num(r["small_order_net"])

        # pct_change 统一用 daily_pct（与涨停判定、与 K 线前复权口径一致）
        pct_unified = pct_today.get(code)

        stocks.append({
            "code": code, "name": nm,
            "pct_change": pct_unified,
            "limit_up_date": hit[code],
            "close": close,
            "turnover_rate": trate,
            "hot_rank": _num(r["hot_rank"]),
            "amount": amount,
            "huge_order_net": n_h,
            "big_order_net": n_b,
            "medium_order_net": n_m,
            "small_order_net": n_s,
            "huge_order_net_norm":   _norm(n_h, amount, trate),
            "big_order_net_norm":    _norm(n_b, amount, trate),
            "medium_order_net_norm": _norm(n_m, amount, trate),
            "small_order_net_norm":  _norm(n_s, amount, trate),
        })

    # 按当日 pct_change 降序；为 None 排最后
    stocks.sort(key=lambda x: (-(x["pct_change"] if x["pct_change"] is not None else -1e18),
                               x["code"]))

    return _json_safe({
        "concept_code": concept_code, "concept_name": cf_name,
        "window_start": window_start, "window_end": window_end,
        "stocks": stocks,
    })



@lru_cache(maxsize=32)
def get_limit_up_ladder(trade_date: str, min_days: int = 2) -> dict:
    # 1. 热分区
    cache_path = LIMIT_UP_STREAK_DIR / f"trade_date={trade_date}" / "part.parquet"
    df = pd.DataFrame()
    
    if cache_path.exists():
        df = pd.read_parquet(cache_path)
    else:
        # 2. 冷年文件
        year = trade_date[:4]
        year_file = COMPACT_ROOT / "limit_up_streak" / f"trade_date={year}" / "part.parquet"
        if year_file.exists():
            df = _q_df(f"SELECT code, streak_days FROM read_parquet({year_file}) WHERE trade_date = ?", [trade_date])
            
    if df.empty:
        return {"trade_date": trade_date, "stocks": [], "cached": False,
                "hint": "该日期未预计算，请运行 precompute.py"}

    df = df[df["streak_days"] >= min_days]
    if df.empty:
        return {"trade_date": trade_date, "stocks": []}

    codes = df["code"].tolist()
    streak_map = dict(zip(df["code"], df["streak_days"].astype(int)))

    try:
        from core.tops_cache import get_tops_and_scores_cached
        _, top_amount, top_pct, top_limit_up, concept_scores = \
            get_tops_and_scores_cached(trade_date)
    except ValueError:
        return {"trade_date": trade_date, "stocks": []}

    target_cfs = set()
    for lst in (top_amount, top_pct, top_limit_up):
        for it in lst:
            target_cfs.add(it["name"])

    code_info, _ = _load_stock_index_by_date(trade_date)

    # ── 批量取行情 ──
    from datetime import date as _date, timedelta as _td
    ph = ",".join(["?"] * len(codes))
    quote_df = _q_df(
        f"""
        SELECT code, close, turnover_rate, hot_rank, amount,
               huge_order_net, big_order_net, medium_order_net, small_order_net
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true,
                          union_by_name=true)
        WHERE trade_date = ? AND code IN ({ph})
        """, [trade_date, *codes])
    qmap = {r["code"]: r for _, r in quote_df.iterrows()}

    # ── pct_change 统一走 daily_pct ──
    pct_map = _load_pct_today(trade_date)

    # ── 未来 N 日 close ──
    c0 = _date.fromisoformat(trade_date)
    fwd_days = [d.isoformat() for d in trading_days(c0, c0 + _td(days=120))]
    try:
        i0 = fwd_days.index(trade_date)
    except ValueError:
        i0 = -1
    offsets = (5, 10, 20, 30)
    n2date = {n: fwd_days[i0 + n] for n in offsets
              if 0 <= i0 + n < len(fwd_days)}
    fmap = {}
    if n2date:
        date_list = list(set(n2date.values()))
        ph2 = ",".join(["?"] * len(date_list))
        fdf = _q_df(
            f"""
            SELECT code, trade_date, close
            FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true,
                              union_by_name=true)
            WHERE trade_date IN ({ph2}) AND code IN ({ph})
            """, [*date_list, *codes])
        date2n = {d: n for n, d in n2date.items()}
        for _, r in fdf.iterrows():
            n = date2n.get(str(r["trade_date"])[:10])
            if n is not None:
                fmap[(r["code"], n)] = r["close"]

    def _num(x):
        if x is None or not pd.notna(x):
            return None
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _norm(net, amount, trate):
        if net is None or amount is None or trate is None or amount == 0:
            return None
        return net * trate / amount

    stocks = []
    for code in codes:
        info = code_info.get(code)
        if not info:
            continue
        name, cs = info
        matched = sorted(cs & target_cfs)
        m = len(matched)
        if m > 0:
            weighted_scores = []
            gld_detail = []
            for c in matched:
                cs_score = concept_scores.get(c, {}).get("score", 0.0)
                gld = get_concept_gld(code, c, default=GLD_MAX)
                gld_norm = min(gld / GLD_MAX, 1.0)
                weighted_scores.append(cs_score * gld_norm)
                gld_detail.append({"name": c, "gld": round(float(gld), 2)})

            mean_score = sum(weighted_scores) / m
            base_importance = mean_score * (m ** 0.4)
            has_leader = any(
                concept_scores.get(c, {}).get("score", 0.0) >= 0.8
                for c in matched
            )
            importance = base_importance * (1.20 if has_leader else 1.0)
        else:
            importance = 0.0
            gld_detail = []
        r = qmap.get(code)
        if r is None:
            stocks.append({
                "code": code, "name": name,
                "streak_days": streak_map[code],
                "streak_label": _board_label(streak_map[code]),
                "concepts": sorted(cs),
                "matched_cfs_detail": gld_detail,
                "importance_index": round(importance, 2),
                "close": None, "pct_change": pct_map.get(code),
                "turnover_rate": None, "hot_rank": None,
                "huge_order_net": None, "big_order_net": None,
                "medium_order_net": None, "small_order_net": None,
                "huge_order_net_norm": None, "big_order_net_norm": None,
                "medium_order_net_norm": None, "small_order_net_norm": None,
                "fwd_5d": None, "fwd_10d": None,
                "fwd_20d": None, "fwd_30d": None,
            })
            continue

        close  = _num(r["close"])
        amount = _num(r["amount"])
        trate  = _num(r["turnover_rate"])
        n_h, n_b = _num(r["huge_order_net"]),   _num(r["big_order_net"])
        n_m, n_s = _num(r["medium_order_net"]), _num(r["small_order_net"])

        def _fwd(n):
            if close is None or close == 0:
                return None
            f = fmap.get((code, n))
            return (f / close - 1) * 100 if f is not None else None

        stocks.append({
            "code": code, "name": name,
            "streak_days": streak_map[code],
            "streak_label": _board_label(streak_map[code]),
            "concepts": sorted(cs),
            "importance_index": round(importance, 2),
            "close": close,
            "pct_change": pct_map.get(code),
            "turnover_rate": trate,
            "hot_rank": _num(r["hot_rank"]),
            "huge_order_net": n_h, "big_order_net": n_b,
            "medium_order_net": n_m, "small_order_net": n_s,
            "huge_order_net_norm":   _norm(n_h, amount, trate),
            "big_order_net_norm":    _norm(n_b, amount, trate),
            "medium_order_net_norm": _norm(n_m, amount, trate),
            "small_order_net_norm":  _norm(n_s, amount, trate),
            "fwd_5d": _fwd(5), "fwd_10d": _fwd(10),
            "fwd_20d": _fwd(20), "fwd_30d": _fwd(30),
        })

    stocks.sort(key=lambda x: (-x["streak_days"], -x["importance_index"], x["code"]))

    # ══════════════════════════════════════════════════════════
    # 归一化 importance（天梯榜），供前端颜色分档
    # ══════════════════════════════════════════════════════════
    if stocks:
        max_imp = max(s["importance_index"] for s in stocks) or 1.0
        total = len(stocks)
        for idx, s in enumerate(stocks):
            s["importance_pct"] = round(s["importance_index"] / max_imp, 3)
            s["rank_in_picks"] = idx + 1
            s["total_in_picks"] = total
    concept_colors = _compute_concept_colors(top_amount, top_pct, top_limit_up)
    result = {"trade_date": trade_date, "stocks": stocks, "concept_colors": concept_colors}
    return _json_safe(result)



def get_concept_panel(code: str, trade_date: str, days: int = 120) -> dict:
    """概念面板：某股票所属概念当日成交额 + 近 N 日序列 + 全市场概念 TOP15。"""

    # 1. 概念名
    row = _q_fetchone(
        f"SELECT concepts FROM read_parquet('{MAP_FILE}') WHERE code = ?",
        [code],
    )
    if not row or not row[0]:
        raise ValueError(f"未找到 {code} 的概念映射")
    concept_names = [c.strip() for c in str(row[0]).split(";") if c.strip()]
    if not concept_names:
        raise ValueError(f"{code} 无概念数据")

    def _fnum(x):
        return float(x) if pd.notna(x) else 0.0

    # 2. 概念名 → concept code
    ph = ",".join(["?"] * len(concept_names))
    name_df = _q_df(
        f"""
        SELECT DISTINCT code, name
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE name IN ({ph})
        """,
        concept_names,
    )
    if name_df.empty:
        raise ValueError(f"{code} 的概念在 concept_flow 中均无数据")

    code_to_name = dict(zip(name_df["code"], name_df["name"]))
    concept_codes = list(code_to_name.keys())
    ph2 = ",".join(["?"] * len(concept_codes))

    # 3. 该股票所属概念，当日柱状图数据（按成交额降序）
    bars_df = _q_df(
        f"""
        SELECT DISTINCT code, name, amount, turnover_rate
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE trade_date = ? AND code IN ({ph2})
        """,
        [trade_date, *concept_codes],
    )

    actual_trade_date = trade_date

    if bars_df.empty:
        fallback = _q_fetchone(
            f"""
            SELECT MAX(trade_date) FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
            WHERE trade_date <= ? AND code IN ({ph2})
            """,
            [trade_date, *concept_codes],
        )
        if not fallback or not fallback[0]:
            raise ValueError(f"该股票所属概念在 {trade_date} 及之前均无数据")

        def _fmt_any(v):
            return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]
        actual_trade_date = _fmt_any(fallback[0])

        bars_df = _q_df(
            f"""
            SELECT DISTINCT code, name, amount, turnover_rate
            FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
            WHERE trade_date = ? AND code IN ({ph2})
            """,
            [actual_trade_date, *concept_codes],
        )

    if bars_df.empty:
        raise ValueError(f"该股票所属概念在 {trade_date} 附近均无数据")

    bars_df = bars_df.sort_values("amount", ascending=False, na_position="last")

    bars = [
        {
            "code": r["code"],
            "name": r["name"],
            "amount": _fnum(r["amount"]),
            "turnover_rate": _fnum(r["turnover_rate"]),
        }
        for _, r in bars_df.iterrows()
    ]
    ordered_codes = [b["code"] for b in bars]

    # 3.5 全市场概念 TOP15（同 trade_date）
    market_df = _q_df(
        f"""
        SELECT DISTINCT code, name, amount, turnover_rate
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE trade_date = ?
        ORDER BY amount DESC NULLS LAST
        LIMIT 15
        """,
        [actual_trade_date],
    )

    market_top = [
        {
            "code": r["code"],
            "name": r["name"],
            "amount": _fnum(r["amount"]),
            "turnover_rate": _fnum(r["turnover_rate"]),
        }
        for _, r in market_df.iterrows()
    ]

    

    # 4. 近 N 日序列
    date_df = _q_df(
        f"""
        SELECT DISTINCT trade_date
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE code IN ({ph2}) AND trade_date <= ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        [*concept_codes, actual_trade_date, days],
    )

    if date_df.empty:
        return {
            "trade_date": trade_date,
            "bars": bars,
            "market_top": market_top,
            "series": {"dates": [], "concepts": {}},
        }

    def _fmt(v):
        return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]

    all_dates = sorted({_fmt(v) for v in date_df["trade_date"].tolist()})
    start_date = all_dates[0]

    base_df = _q_df(
        f"""
        SELECT DISTINCT code, trade_date, amount, turnover_rate,
               huge_order_net, big_order_net, medium_order_net, small_order_net
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE code IN ({ph2}) AND trade_date BETWEEN ? AND ?
        ORDER BY code, trade_date
        """,
        [*concept_codes, start_date, actual_trade_date],
    )

    series_concepts: dict[str, dict] = {}
    for c in ordered_codes:
        sub = base_df[base_df["code"] == c].sort_values("trade_date")
        if sub.empty:
            continue

        def _col(name):
            return [float(x) if pd.notna(x) else 0.0 for x in sub[name].tolist()]

        series_concepts[c] = {
            "name": code_to_name.get(c, c),
            "dates": [_fmt(v) for v in sub["trade_date"].tolist()],
            "amount": _col("amount"),
            "turnover_rate": _col("turnover_rate"),
            "huge_order_net": _col("huge_order_net"),
            "big_order_net": _col("big_order_net"),
            "medium_order_net": _col("medium_order_net"),
            "small_order_net": _col("small_order_net"),
        }

    # 3.6 概念交叉选股：按 TOP5 / TOP10 概念匹配数选股
    stocks_df = _q_df(
        f"SELECT DISTINCT code, name, concepts FROM read_parquet('{MAP_FILE}')"
    )

    def _pick_for_n(n: int) -> list[dict]:
        top_list = market_df.head(n)["name"].tolist()
        top_names = set(top_list)
        if not top_names:
            return []
        # 保持 top_list 的顺序，避免乱序
        order = {name: i for i, name in enumerate(top_list)}

        picks = []
        for _, r in stocks_df.iterrows():
            cs = str(r["concepts"] or "")
            sc = {c.strip() for c in cs.split(";") if c.strip()}
            matched = sc & top_names
            if matched:
                matched_sorted = sorted(matched, key=lambda x: order.get(x, 999))
                picks.append({
                    "code": r["code"],
                    "name": r["name"],
                    "match_count": len(matched),
                    "matched_concepts": matched_sorted,
                })
        picks.sort(key=lambda x: (-x["match_count"], x["code"]))
        return picks[:10]

    stock_picks = {
        "top5":  _pick_for_n(5),
        "top10": _pick_for_n(10),
    }
    return {
        "trade_date": actual_trade_date,
        "bars": bars,
        "market_top": market_top,
        "stock_picks": stock_picks,
        "series": {"dates": all_dates, "concepts": series_concepts},
    }



# ── 交易日 ──
def get_trading_days_around(center: str, before: int = 400, after: int = 0) -> dict:
    """以 center（含）为基准，返回前后各 N 个交易日的起止日期（clamp 到数据范围）。"""
    from datetime import date as _date, timedelta as _td

    # 数据实际边界
    row = _q_fetchone(
        f"""
        SELECT MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true)
        """
    )
    if not row or not row[0]:
        raise ValueError("quote 数据为空")

    def _fmt(x):
        return x.isoformat() if hasattr(x, "isoformat") else str(x)[:10]

    data_min, data_max = _fmt(row[0]), _fmt(row[1])

    c = _date.fromisoformat(center)
    span = max(before, after) * 3 + 30
    all_days = list(trading_days(c - _td(days=span), c + _td(days=span)))
    dates = [d.isoformat() for d in all_days
             if data_min <= d.isoformat() <= data_max]
    if not dates:
        raise ValueError(f"交易日历与数据范围无交集")
    try:
        i = dates.index(center)
    except ValueError:
        # center 是周末 / 节假日 / 越界 → 找 <= center 的最后一个；再不行取第一个
        le = [d for d in dates if d <= center]
        i = dates.index(le[-1]) if le else 0

    lo = max(0, i - before)
    hi = min(len(dates) - 1, i + after)
    return {"start": dates[lo], "end": dates[hi]}



def get_trading_day_neighbor(trade_date: str, direction: str) -> dict:
    """相邻交易日：先扫热分区目录（快），翻页超出范围再查 glob（慢但全）。"""
    if direction not in ("prev", "next"):
        raise ValueError("direction 必须是 prev 或 next")

    # 1. 热分区（快速路径）
    dates = sorted(_list_daily_pct_dates())
    if direction == "prev":
        prev = [d for d in dates if d < trade_date]
        if prev:
            return {"date": prev[-1]}
    else:
        nxt = [d for d in dates if d > trade_date]
        if nxt:
            return {"date": nxt[0]}

    # 2. glob 兜底（跨月归档后仍有数据）
    if direction == "prev":
        row = _q_fetchone(
            f"SELECT MAX(trade_date) FROM read_parquet({DAILY_PCT_GLOB}, hive_partitioning=true) "
            f"WHERE CAST(trade_date AS VARCHAR) < ?", [trade_date])
    else:
        row = _q_fetchone(
            f"SELECT MIN(trade_date) FROM read_parquet({DAILY_PCT_GLOB}, hive_partitioning=true) "
            f"WHERE CAST(trade_date AS VARCHAR) > ?", [trade_date])

    if not row or not row[0]:
        return {"date": None}
    d = row[0]
    return {"date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]}
