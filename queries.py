"""DuckDB 查询接口。

对外提供：
    list_codes()                     → 股票列表（code, name）
    list_available_indicators()      → 所有已注册指标
    get_stock_chart(...)             → 个股 K 线 + 指标
    get_stock_sectors(code)          → 个股所属行业与概念
    get_sector_chart(...)            → 行业/概念 K 线 + 指标
"""
from __future__ import annotations

import logging
from typing import Iterable, Sequence
from datetime import date
from trading_calendar import trading_days
import duckdb
import pandas as pd
from config import (
    QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, MAP_FILE, DATA_DIR, CONCEPT_DATE_DIR,
    CONCEPT_PICKS_DIR, LIMIT_UP_STREAK_DIR, DAILY_PCT_DIR
)
from indicators import (
    SQL_INDICATORS,
    PYTHON_INDICATORS,
    list_indicators,
)
import threading
from functools import lru_cache
logger = logging.getLogger(__name__)

# 单进程复用连接
_con = duckdb.connect()
_con_lock = threading.Lock()


def _q_df(sql: str, params=None):
    """线程安全的 DuckDB 查询 → DataFrame。所有 _con.execute(...).df() 都改用它。"""
    with _con_lock:
        if params is not None:
            return _con.execute(sql, params).df()
        return _con.execute(sql).df()


def _q_fetchone(sql: str, params=None):
    """线程安全的 DuckDB 查询 → 单行 tuple。"""
    with _con_lock:
        if params is not None:
            return _con.execute(sql, params).fetchone()
        return _con.execute(sql).fetchone()


def _q_fetchall(sql: str, params=None):
    """线程安全的 DuckDB 查询 → 行列表。"""
    with _con_lock:
        if params is not None:
            return _con.execute(sql, params).fetchall()
        return _con.execute(sql).fetchall()
# CONCEPT_PICKS_DIR / LIMIT_UP_STREAK_DIR 统一从 config 导入（单一真相来源）
# 优先读合并目录；不存在时回退到原始分区目录
COMPACT_ROOT = DATA_DIR.parent / "data_compact"
# 辅助函数：如果 compact 目录存在，则生成合并查询的 Glob 数组
BASE_DIR = QUOTE_DIR.parent.parent

def _make_glob(kind: str) -> str:
    compact_dir = BASE_DIR / "data_compact" / kind
    data_dir = BASE_DIR / "data" / kind
    
    paths = []
    if compact_dir.exists():
        paths.append(str(compact_dir / "**" / "*.parquet").replace("\\", "/"))
    if data_dir.exists():
        paths.append(str(data_dir / "**" / "*.parquet").replace("\\", "/"))
        
    if not paths:
        return "''"
    if len(paths) == 1:
        return f"'{paths[0]}'"  # 返回： 'D:/.../...'
    
    # 返回： ['D:/.../...', 'D:/.../...']
    quoted_paths = [f"'{p}'" for p in paths]
    return f"[{', '.join(quoted_paths)}]"

# 这里的变量现在自带了单引号，或者是一个列表
QUOTE_GLOB     = _make_glob("quote")
INDUSTRY_GLOB  = _make_glob("industry_flow")
CONCEPT_GLOB   = _make_glob("concept_flow")
DAILY_PCT_GLOB = _make_glob("daily_pct")

def _get_globs(data_dir: Path, sub_name: str) -> str:
    return _make_glob(sub_name)

# 基础列（分区列 trade_date 由 hive_partitioning 自动注入）
SECTOR_BASE_COLS = [
    "trade_date", "code", "name", "pct_change",
    "open", "high", "low", "close", "volume", "amount",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
]

DEFAULT_INDICATORS = ["main_net_3d", "volume_3d"]
STOCK_BASE_COLS = [
    "trade_date", "code", "name", "pct_change", "industry",
    "open", "high", "low", "close", "volume", "amount",
    "turnover_rate", "hot_rank",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
]
# ───────────────────────── 汇总表（增量构建） ─────────────────────────

import numpy as np
from pathlib import Path
from config import DATA_DIR

MARKET_AMOUNT_FILE  = DATA_DIR / "market_amount.parquet"
DAILY_PCT_FILE = DATA_DIR / "daily_pct.parquet"
DAILY_PCT_DIR  = DATA_DIR / "daily_pct"
CONCEPT_ALIAS_FILE  = DATA_DIR / "concept_alias.parquet"



def _list_partition_dates(dir_path: Path) -> list[str]:
    """扫描原 data/ 目录（热数据区），只返回按日的分区（YYYY-MM-DD）。
    
    注意：合并后的历史数据在 data_compact/ 中格式为 YYYY，不参与日常日期遍历。
    """
    if not dir_path.exists():
        return []
    out = []
    for p in dir_path.iterdir():
        if not (p.is_dir() and p.name.startswith("trade_date=")):
            continue
        d_str = p.name.split("=", 1)[1]
        # 只认 YYYY-MM-DD 格式，排除可能被误建的年目录
        if len(d_str) == 10 and d_str[4] == "-":
            if (p / "part.parquet").exists():
                out.append(d_str)
    return sorted(out)


def _load_existing_dates(file: Path, col: str = "trade_date") -> set[str]:
    """读取已有 parquet 里出现过的日期集合；文件不存在返回空集。"""
    if not file.exists():
        return set()
    try:
        df = pd.read_parquet(file, columns=[col])
        return set(df[col].astype(str).str.slice(0, 10))
    except Exception:
        logger.exception("读取 %s 失败，视为空", file)
        return set()


def rebuild_market_amount() -> int:
    """增量汇总每日全市场成交额（对 industry_flow 当日所有记录 amount 求和）。

    写入 data/market_amount.parquet：
        trade_date (str, YYYY-MM-DD), total_amount (float, 元)
    返回本次新增的日期数。
    """
    existing = _load_existing_dates(MARKET_AMOUNT_FILE)
    all_dates = _list_partition_dates(INDUSTRY_DIR)
    todo = [d for d in all_dates if d not in existing]
    if not todo:
        logger.info("[market_amount] 已是最新，共 %d 天", len(existing))
        return 0

    rows = []
    for d in todo:
        r = _q_fetchone(
            f"""
            SELECT SUM(amount) AS total
            FROM read_parquet({INDUSTRY_GLOB}, hive_partitioning=true)
            WHERE trade_date = ?
            """,
            [d],
        )
        total = float(r[0]) if r and r[0] is not None else 0.0
        rows.append({"trade_date": d, "total_amount": total})

    new_df = pd.DataFrame(rows)
    if MARKET_AMOUNT_FILE.exists():
        old = pd.read_parquet(MARKET_AMOUNT_FILE)
        merged = (
            pd.concat([old, new_df], ignore_index=True)
            .drop_duplicates(subset=["trade_date"], keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
    else:
        merged = new_df.sort_values("trade_date").reset_index(drop=True)

    MARKET_AMOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(MARKET_AMOUNT_FILE, index=False)
    logger.info("[market_amount] 新增 %d 天，总计 %d 天", len(todo), len(merged))
    return len(todo)


def rebuild_daily_pct(force_recent_days: int = 10) -> int:
    """增量计算个股每日涨跌幅（%），写入 data/daily_pct/trade_date=*/part.parquet。

    - T-1 是日历上的前一交易日（由 quote 分区目录推出）
    - 该股在 T-1 无记录 → pct_change = NaN
    - 最近 force_recent_days 天总是重算（防分区写入时序错位 / 前复权变动）
    """
    existing = _list_daily_pct_dates()
    all_td = _list_partition_dates(QUOTE_DIR)
    recent_set = set(all_td[-force_recent_days:]) if force_recent_days > 0 else set()
    todo = [d for d in all_td if d not in existing or d in recent_set]
    if not todo:
        logger.info("[daily_pct] 已是最新，共 %d 天", len(existing))
        return 0

    td_idx = {d: i for i, d in enumerate(all_td)}
    needed = set(todo)
    for d in todo:
        i = td_idx[d]
        if i > 0:
            needed.add(all_td[i - 1])
    needed = sorted(needed)

    ph = ",".join(["?"] * len(needed))
    raw = _q_df(
        f"""
        SELECT code, trade_date, close
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true,
                          union_by_name=true)
        WHERE trade_date IN ({ph})
        """, needed)
    if raw.empty:
        logger.warning("[daily_pct] quote 里未读到任何 close 数据")
        return 0

    raw["trade_date"] = raw["trade_date"].astype(str).str.slice(0, 10)
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    by_date = {d: g[["code", "close"]].copy()
               for d, g in raw.groupby("trade_date")}

    for d in todo:
        i = td_idx[d]
        if i == 0:
            continue
        cur = by_date.get(d)
        if cur is None or cur.empty:
            continue
        prev_d = all_td[i - 1]
        prev = by_date.get(prev_d)

        m = cur.rename(columns={"close": "cur_close"}).copy()
        if prev is None or prev.empty:
            m["prev_close"] = np.nan
        else:
            m = m.merge(
                prev.rename(columns={"close": "prev_close"}),
                on="code", how="left",
            )
        m["pct_change"] = (
            (m["cur_close"] - m["prev_close"]) / m["prev_close"] * 100.0
        )

        out_df = m[["code", "pct_change"]].copy()
        out_df["trade_date"] = d

        partition = DAILY_PCT_DIR / f"trade_date={d}"
        partition.mkdir(parents=True, exist_ok=True)
        out_df[["code", "trade_date", "pct_change"]].to_parquet(
            partition / "part.parquet", index=False)

    logger.info("[daily_pct] 写入 %d 天分区（含最近 %d 天重算）",
                len(todo), force_recent_days)
    return len(todo)
def _list_daily_pct_dates() -> set[str]:
    """扫描 daily_pct 分区，只认含 part.parquet 的目录。"""
    if not DAILY_PCT_DIR.exists():
        return set()
    out = set()
    for p in DAILY_PCT_DIR.iterdir():
        if not (p.is_dir() and p.name.startswith("trade_date=")):
            continue
        if not (p / "part.parquet").exists():
            logger.debug("[daily_pct] 目录存在但缺 part.parquet: %s", p)
            continue
        out.add(p.name.split("=", 1)[1])
    return out
# ───────────────────────── 概念面板 API ─────────────────────────

# 涨停阈值（按代码前缀）
def _limit_up_threshold(code: str) -> float:
    p = str(code)[:2]
    if p in ("30", "68"):       return 19.8
    if p in ("8", "4", "92"):   return 29.8   # 北交所（含 92 开头）
    return 9.8


def _clean_concept_rows(df):
    out = []
    for _, r in df.iterrows():
        out.append({
            "code":  r["code"],
            "name":  r["name"],
            "amount":           float(r["amount"])           if pd.notna(r["amount"])           else 0.0,
            "turnover_rate":    float(r["turnover_rate"])    if pd.notna(r["turnover_rate"])    else 0.0,
            "total_market_cap": float(r["total_market_cap"]) if pd.notna(r["total_market_cap"]) else 0.0,
            "pct_change":       float(r["pct_change"])       if pd.notna(r["pct_change"])       else 0.0,
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
@lru_cache(maxsize=1)
def _load_alias_map():
    """cf_name -> map_name（进程内永久缓存）。"""
    if not CONCEPT_ALIAS_FILE.exists():
        return {}
    df = _q_df(
        f"SELECT cf_name, map_name FROM read_parquet({CONCEPT_ALIAS_FILE})"
    )
    return {r["cf_name"]: r["map_name"]
            for _, r in df.iterrows() if r["map_name"]}

# 更新那些直接读文件的函数，确保能动态拼接路径
def _find_file(sub_name: str, date_dir: str, filename: str) -> Path:
    """优先在 compact 中找，找不到再去 data 中找"""
    compact_p = COMPACT_ROOT / sub_name / date_dir / filename
    if compact_p.exists():
        return compact_p
    data_p = DATA_DIR / sub_name / date_dir / filename
    if data_p.exists():
        return data_p
    return data_p # 返回默认路径（让后续代码报错并触发重算）

@lru_cache(maxsize=256)
def _load_stock_index_by_date(trade_date: str):
    # 1. 热分区
    path = CONCEPT_DATE_DIR / f"trade_date={trade_date}" / "part.parquet"
    
    df = pd.DataFrame()
    if path.exists():
        df = pd.read_parquet(path, columns=["code", "concepts"])
    else:
        # 2. 冷年文件
        year = trade_date[:4]
        year_file = COMPACT_ROOT / "concept_date" / f"trade_date={year}" / "part.parquet"
        if year_file.exists():
            df = _q_df(f"SELECT code, concepts FROM read_parquet({year_file}) WHERE trade_date = ?", [trade_date])

    if not df.empty:
        names = _q_df(f"SELECT code, name FROM read_parquet('{MAP_FILE}')")
        name_map = dict(zip(names["code"], names["name"]))
        code_info, cf2codes = {}, {}
        for code, cs_str in zip(df["code"], df["concepts"]):
            cs = frozenset(c.strip() for c in str(cs_str or "").split(";") if c.strip())
            code_info[code] = (name_map.get(code, ""), cs)
            for c in cs:
                cf2codes.setdefault(c, []).append(code)
        return code_info, cf2codes

    # 3. fallback 快照 + alias 反向
    rev = _load_alias_reverse()
    stocks = _q_df(f"SELECT code, name, concepts FROM read_parquet('{MAP_FILE}')")
    code_info, cf2codes = {}, {}
    for code, name, cs_str in zip(
            stocks["code"], stocks["name"], stocks["concepts"]):
        maps = frozenset(
            c.strip() for c in str(cs_str or "").split(";") if c.strip()
        )
        cfs = frozenset(rev.get(m, m) for m in maps)
        code_info[code] = (name or "", cfs)
        for c in cfs:
            cf2codes.setdefault(c, []).append(code)
    return code_info, cf2codes
@lru_cache(maxsize=1)
def _load_alias_reverse():
    """map_name -> cf_name（用于 fallback 时反查）。"""
    if not CONCEPT_ALIAS_FILE.exists():
        return {}
    df = _q_df(
        f"SELECT cf_name, map_name FROM read_parquet({CONCEPT_ALIAS_FILE})"
    )
    return {r["map_name"]: r["cf_name"]
            for _, r in df.iterrows() if r["map_name"]}


@lru_cache(maxsize=256)
def _load_pct_today(trade_date: str):
    """当日全市场 pct_change：优先热分区，其次冷年文件，最后降级兜底。"""
    # 1. 热数据（按日分区）
    pdir = DAILY_PCT_DIR / f"trade_date={trade_date}"
    path = pdir / "part.parquet"

    if path.exists():
        df = pd.read_parquet(path, columns=["code", "pct_change"])
        return {r["code"]: float(r["pct_change"])
                for _, r in df.iterrows() if pd.notna(r["pct_change"])}

    # 2. 冷数据（按年合并）
    year = trade_date[:4]
    year_file = COMPACT_ROOT / "daily_pct" / f"trade_date={year}" / "part.parquet"
    if year_file.exists():
        # 用 DuckDB 查，速度极快，避免 pandas 读入整个大文件
        df = _q_df(
            f"SELECT code, pct_change FROM read_parquet({year_file}) WHERE trade_date = ?", 
            [trade_date]
        )
        if not df.empty:
            return {r["code"]: float(r["pct_change"])
                    for _, r in df.iterrows() if pd.notna(r["pct_change"])}

    # 3. 降级：目录存在但 part 文件名不对 → glob 兜底
    if pdir.exists():
        alts = list(pdir.glob("*.parquet"))
        if alts:
            logger.warning(
                "[daily_pct] %s part.parquet 缺失，使用 %s（建议重算）",
                trade_date, alts[0].name,
            )
            df = pd.read_parquet(alts[0], columns=["code", "pct_change"])
            return {r["code"]: float(r["pct_change"])
                    for _, r in df.iterrows() if pd.notna(r["pct_change"])}

    # 4. 最后兜底：走单文件
    if DAILY_PCT_FILE.exists():
        logger.warning(
            "[daily_pct] %s 分区缺失，走单文件 fallback（口径可能不一致）",
            trade_date,
        )
        df = pd.read_parquet(
            DAILY_PCT_FILE, columns=["code", "trade_date", "pct_change"])
        df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 10)
        df = df[df["trade_date"] == trade_date]
        return {r["code"]: float(r["pct_change"])
                for _, r in df.iterrows() if pd.notna(r["pct_change"])}
                
    return {}

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
            it["top_stock"] = (
                {"code": top[0], "name": top[1], "pct": top[2]} if top else None
            )
# ───────────────────────── 交集榜单 Parquet 缓存 ─────────────────────────

def _picks_cache_path(trade_date: str) -> Path:
    return _find_file("concept_picks", f"trade_date={trade_date}", "part.parquet")


def _load_cached_picks(trade_date: str) -> list[dict] | None:
    """命中返回 records；未命中返回 None。"""
    p = CONCEPT_PICKS_DIR / f"trade_date={trade_date}" / "part.parquet"
    df = None
    
    if p.exists():
        df = pd.read_parquet(p)
    else:
        # 查冷年文件
        year = trade_date[:4]
        year_file = COMPACT_ROOT / "concept_picks" / f"trade_date={year}" / "part.parquet"
        if year_file.exists():
            df = _q_df(f"SELECT * FROM read_parquet({year_file}) WHERE trade_date = ?", [trade_date])

    if df is None or df.empty:
        return None
        
    if "matched_concepts" in df.columns:
        df["matched_concepts"] = df["matched_concepts"].fillna("").apply(
            lambda s: [x for x in str(s).split(";") if x]
        )
    return df.to_dict(orient="records")

def _save_picks_cache(trade_date: str, picks: list[dict]) -> None:
    if not picks:
        return
    try:
        df = pd.DataFrame(picks)
        if "matched_concepts" in df.columns:
            df["matched_concepts"] = df["matched_concepts"].apply(
                lambda xs: ";".join(xs) if isinstance(xs, list) else ""
            )
        out = _picks_cache_path(trade_date)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
    except Exception:
        logger.exception("写入 concept_picks 缓存失败 %s", trade_date)


def _is_stable_date(trade_date: str) -> bool:
    """该日期距离最新数据 ≥ 45 自然日 → 未来 30 交易日数据已稳定，可落盘。"""
    row = _q_fetchone(
        f"SELECT MAX(trade_date) AS m FROM read_parquet('{MARKET_AMOUNT_FILE}')"
    )
    if not row or not row[0]:
        return False
    m = row[0]
    max_d = m if hasattr(m, "year") else pd.Timestamp(str(m)).date()
    try:
        td = pd.Timestamp(trade_date).date()
    except Exception:
        return False
    return (max_d - td).days >= 45
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
        SELECT code, name, amount, turnover_rate, total_market_cap, pct_change
        FROM read_parquet({CONCEPT_GLOB}, hive_partitioning=true)
        WHERE trade_date = ?
        """, [trade_date])
    if base.empty:
        raise ValueError(f"{trade_date} 无概念数据")

    top_amount   = _clean_concept_rows(base.nlargest(10, "amount"))
    top_pct      = _clean_concept_rows(base.nlargest(10, "pct_change"))
    top_limit_up = _compute_top_limit_up(base, trade_date, topn=10)

    _enrich_with_stocks([top_amount, top_pct, top_limit_up], trade_date)

    concept_scores = _compute_concept_scores(
        top_amount, top_pct, top_limit_up, trade_date, market_total)

    return market_total, top_amount, top_pct, top_limit_up, concept_scores
def _compute_concept_scores(top_amount, top_pct, top_limit_up,
                            trade_date, market_total):
    """
    每个维度 = 0.5 × 排名分 + 0.5 × 数值分
    权重：成交额 0.35 / 涨幅 0.30 / 涨停数 0.35
    """
    rank_amt = {it["name"]: i + 1 for i, it in enumerate(top_amount)}
    rank_pct = {it["name"]: i + 1 for i, it in enumerate(top_pct)}
    rank_lu  = {it["name"]: i + 1 for i, it in enumerate(top_limit_up)}

    max_amount = max((it["amount"] for it in top_amount), default=0)
    max_pct_pos = max((it["pct_change"] for it in top_pct
                       if it["pct_change"] > 0), default=0)
    max_lu = max((it.get("limit_up_count", 0) for it in top_limit_up), default=0)

    all_data = {}
    for lst in (top_amount, top_pct, top_limit_up):
        for it in lst:
            all_data.setdefault(it["name"], {}).update(it)

    all_cfs = set(rank_amt) | set(rank_pct) | set(rank_lu)
    scores = {}
    for cf in all_cfs:
        data = all_data.get(cf, {})

        # 成交额
        amt_rank = (11 - rank_amt[cf]) / 10 if cf in rank_amt else 0.0
        amt_val_raw = data.get("amount", 0)
        amt_val = amt_val_raw / max_amount if max_amount > 0 else 0.0
        amt_part = 0.5 * amt_rank + 0.5 * amt_val

        # 涨幅
        pct_rank = 0.0
        pct_val = 0.0
        pct_raw = data.get("pct_change", 0)
        if pct_raw > 0:
            pct_rank = (11 - rank_pct[cf]) / 10 if cf in rank_pct else 0.0
            pct_val = pct_raw / max_pct_pos if max_pct_pos > 0 else 0.0
        pct_part = 0.5 * pct_rank + 0.5 * pct_val

        # 涨停数（排名 + 数值）
        lu_rank = (11 - rank_lu[cf]) / 10 if cf in rank_lu else 0.0
        lu_val_raw = data.get("limit_up_count", 0)
        lu_val = lu_val_raw / max_lu if max_lu > 0 else 0.0
        lu_part = 0.5 * lu_rank + 0.5 * lu_val

        score = 0.35 * amt_part + 0.30 * pct_part + 0.35 * lu_part

        scores[cf] = {
            "score": round(score, 4),
            "parts": {
                "amount": round(amt_part, 3),
                "pct": round(pct_part, 3),
                "limitup": round(lu_part, 3),
            },
        }
    return scores

def _build_intersection_picks(top_amount, top_pct, top_limit_up,
                              trade_date, market_total, topn=20):
    """三榜并集 → 每只股票命中几个概念，取前 topn。向量化版本。"""
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

    # ── 向量化：先用 list comprehension 收集，再用 dict 聚合 ──
    hits = {}    # code -> [cf1, cf2, ...]
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
        picks.append({
            "code": code,
            "name": name,
            "match_count": len(matched_sorted),
            "matched_concepts": matched_sorted,
        })

    picks.sort(key=lambda x: (-x["match_count"], x["code"]))
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
        return float(x) if pd.notna(x) else None

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

    return picks
def _market_avg_amount(trade_date: str, days: int) -> float:
    row = _q_fetchone(
        f"""
        SELECT AVG(total_amount) FROM (
            SELECT total_amount FROM read_parquet('{MARKET_AMOUNT_FILE}')
            WHERE trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
        )
        """, [trade_date, days])
    return float(row[0]) if row and row[0] is not None else 0.0

@lru_cache(maxsize=32)
def get_concept_date_top10(trade_date: str) -> dict:
    """三榜 + 交集榜（纯读缓存，< 100ms）。"""
    market_total, top_amount, top_pct, top_limit_up, _ = \
        _get_tops_and_scores(trade_date)

    # picks：纯读 parquet 缓存，没有就空（不现场算）
    picks = _load_cached_picks(trade_date) or []

    return {
        "trade_date": trade_date,
        "market_total_amount": market_total,
        "market_avg_5d":  _market_avg_amount(trade_date, 5),
        "market_avg_20d": _market_avg_amount(trade_date, 20),
        "top_amount":   top_amount,
        "top_pct":      top_pct,
        "top_limit_up": top_limit_up,
        "intersection_picks": picks,
        "picks_cached": bool(picks),
    }

# ───────────────────────── 内部工具 ─────────────────────────

def _dedup(seq: Iterable[str]) -> list[str]:
    """去重保序。"""
    seen: set[str] = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


def _classify(indicators: Sequence[str]) -> tuple[list[str], list[str]]:
    """把指标名分成 (sql_names, python_names)；未注册的直接抛异常。"""
    sql_names: list[str] = []
    py_names: list[str] = []
    for name in indicators:
        if name in SQL_INDICATORS:
            sql_names.append(name)
        elif name in PYTHON_INDICATORS:
            py_names.append(name)
        else:
            raise ValueError(f"未注册的指标: {name}")
    return sql_names, py_names


def _build_select_cols(base_cols: Sequence[str], sql_names: Sequence[str]) -> str:
    """
    拼 SELECT 列表达式。

    sql_names 已通过 _classify 白名单校验，故可安全拼进 SQL；
    expr 本身来自 indicators.py 常量，不接受外部输入。
    """
    parts = list(base_cols)
    for name in sql_names:
        expr = SQL_INDICATORS[name]["expr"]
        parts.append(f"({expr}) AS {name}")
    return ",\n        ".join(parts)


def _query_chart(
    glob: str,
    key_col: str,
    key_val: str,
    start: str,
    end: str,
    indicators: Sequence[str],
    base_cols: Sequence[str],
) -> pd.DataFrame:
    """通用 K 线 + 指标查询。"""
    indicators = _dedup(indicators)
    sql_names, py_names = _classify(indicators)

    cols = _build_select_cols(base_cols, sql_names)
    sql = f"""
    SELECT
        {cols}
    FROM read_parquet({glob}, hive_partitioning=true, union_by_name=true)
    WHERE {key_col} = ?
      AND trade_date BETWEEN ? AND ?
    ORDER BY trade_date
    """

    df = _q_df(sql, [key_val, str(start), str(end)])

    # 依次执行 Python 指标（每个函数自己往 df 里加列）
    for name in py_names:
        df = PYTHON_INDICATORS[name]["func"](df)

    # ── 过滤掉 OHLC 不全 / 非正 / 日期异常的行 ──
    # ── 强制清洗：日期标准化 + OHLC 有效 + 去重排序 ──
    if "trade_date" in df.columns and not df.empty:
        df["trade_date"] = (
            pd.to_datetime(df["trade_date"], errors="coerce")
              .dt.strftime("%Y-%m-%d")
        )
        df = df[df["trade_date"].notna()]

    for c in ("open", "high", "low", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            # 只保留 4 个值都有效 且 > 0 的行
            df = df[df[c].notna() & (df[c] > 0)]

    if "trade_date" in df.columns:
        df = (df.sort_values("trade_date")
                .drop_duplicates(subset=["trade_date"], keep="last")
                .reset_index(drop=True))

    return df


# ───────────────────────── 对外接口 ─────────────────────────

def list_codes() -> pd.DataFrame:
    """所有股票代码 + 名称。"""
    sql = f"""
    SELECT code, ANY_VALUE(name) AS name
    FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true, union_by_name=true)
    GROUP BY code
    ORDER BY code
    """
    return _q_df(sql)


def list_available_indicators() -> list[dict]:
    """所有已注册指标，供前端下拉框。"""
    return list_indicators()


def get_stock_chart(
    code: str,
    start: str,
    end: str,
    indicators: Sequence[str] | None = None,
) -> pd.DataFrame:
    """个股 K 线 + 指定指标。indicators=None 时使用默认指标集。"""
    if indicators is None:
        indicators = DEFAULT_INDICATORS
    return _query_chart(
        glob=QUOTE_GLOB,
        key_col="code",
        key_val=code,
        start=start,
        end=end,
        indicators=indicators,
        base_cols=STOCK_BASE_COLS,
    )


def get_stock_sectors(code: str) -> dict:
    """该股票所属行业与概念（结构化返回，方便前端直接渲染）。

    返回：
        {
          "industry": {"name": "银行", "code": "880471"} | None,
          "concepts": [{"name": "沪深300"}, {"name": "MSCI中国"}, ...]
        }
    """
    sql = f"""
    SELECT industry, industry_code, concepts
    FROM read_parquet('{MAP_FILE}', hive_partitioning=true, union_by_name=true)
    WHERE code = ?
    LIMIT 1
    """
    row = _q_fetchone(sql, [code])
    if not row:
        return {"industry": None, "concepts": []}

    industry, industry_code, concepts = row
    concept_list: list[dict] = []
    if concepts:
        concept_list = [
            {"name": c.strip()}
            for c in str(concepts).split(";")
            if c.strip()
        ]
    return {
        "industry": {"name": industry, "code": industry_code} if industry else None,
        "concepts": concept_list,
    }

def get_sector_chart(
    sector_key: str,
    start: str,
    end: str,
    kind: str = "industry",
    indicators: Sequence[str] | None = None,
    key_col: str = "code",          # "code" 或 "name"
) -> pd.DataFrame:
    """行业/概念 K 线 + 指标。

    kind:    "industry" | "concept"
    key_col: "code"（默认）或 "name"（概念名称一般没有 code 时用）
    """
    if indicators is None:
        indicators = DEFAULT_INDICATORS
    if kind == "industry":
        glob = INDUSTRY_GLOB
    elif kind == "concept":
        glob = CONCEPT_GLOB
    else:
        raise ValueError(f"未知的 kind: {kind}（应为 industry 或 concept）")
    if key_col not in ("code", "name"):
        raise ValueError("key_col 只能是 code 或 name")

    return _query_chart(
        glob=glob,
        key_col=key_col,
        key_val=sector_key,
        start=start,
        end=end,
        indicators=indicators,
        base_cols=SECTOR_BASE_COLS,
    )

def get_stock_industry_chart(
    code: str,
    start: str,
    end: str,
    indicators: Sequence[str] | None = None,
) -> pd.DataFrame:
    """该股票所属行业的 K 线 + 指标。

    流程：
      1. stock_sector_map 查该 code 的 industry_code / industry
      2. 用 industry_code 到 industry_flow 查对应日期的数据
    """
    if indicators is None:
        indicators = ["main_net", "main_net_3d", "main_net_5d", "main_net_20d"]

    # 先解析 industry_code
    row = _q_fetchone(
        f"SELECT industry_code, industry "
        f"FROM read_parquet('{MAP_FILE}', hive_partitioning=true, union_by_name=true) WHERE code = ?",
        [code],
    )
    if not row or not row[0]:
        raise ValueError(f"未找到 {code} 的行业映射")

    industry_code, industry_name = row[0], row[1]

    indicators = _dedup(indicators)
    sql_names, py_names = _classify(indicators)

    cols = _build_select_cols(SECTOR_BASE_COLS, sql_names)
    sql = f"""
    SELECT
        {cols}
    FROM read_parquet({INDUSTRY_GLOB}, hive_partitioning=true, union_by_name=true)
    WHERE code = ?
      AND trade_date BETWEEN ? AND ?
    ORDER BY trade_date
    """

    df = _q_df(sql, [industry_code, str(start), str(end)])
    for name in py_names:
        df = PYTHON_INDICATORS[name]["func"](df)

    # 把行业名挂在 DataFrame 上（API 层会用）
    df.attrs["industry_name"] = industry_name
    df.attrs["industry_code"] = industry_code
    return df

def get_missing_trading_days(
    code: str,
    start: str,
    end: str,
    kind: str = "stock",
) -> list[str]:
    """返回 [start, end] 区间内，该 code 应有数据但缺失的交易日。

    注意：
      - 停牌、停更、无热度数据都会算成"缺失"，仅作提醒，不影响系统运行。
      - 交易日历范围外的日期（比如未来）不计入。
    """
    if kind == "stock":
        glob = QUOTE_GLOB
    elif kind == "industry":
        glob = INDUSTRY_GLOB
    elif kind == "concept":
        glob = CONCEPT_GLOB
    else:
        raise ValueError(f"未知 kind: {kind}")

    def _fmt(v) -> str:
        """统一成 YYYY-MM-DD 字符串。"""
        if v is None:
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        return str(v)[:10]

    # 该 code 实际有数据的日期
    df = _q_df(
        f"""
        SELECT DISTINCT trade_date
        FROM read_parquet({glob}, hive_partitioning=true)
        WHERE code = ? AND trade_date BETWEEN ? AND ?
        """,
        [code, str(start), str(end)],
    )

    actual = {_fmt(v) for v in df["trade_date"].tolist()}
    actual.discard("")

    # 交易日历
    try:
        expected = {
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            for d in trading_days(date.fromisoformat(start), date.fromisoformat(end))
        }
    except Exception:
        logger.exception("获取交易日历失败，跳过缺失检测")
        return []
    
    missing = sorted(expected - actual)

    logger.debug(
        "[missing] code=%s kind=%s | expected=%d actual=%d missing=%d | "
        "actual sample=%s",
        code, kind, len(expected), len(actual), len(missing),
        sorted(actual)[:3],
    )

    return missing

def get_stock_date_range(code: str) -> dict:
    """返回该股票在 Parquet 里的最小/最大交易日。无数据返回 {None, None}。"""
    row = _q_fetchone(
        f"""
        SELECT MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true)
        WHERE code = ?
        """,
        [code],
    )

    if not row or not row[0]:
        return {"min_date": None, "max_date": None}

    def _fmt(x):
        return x.isoformat() if hasattr(x, "isoformat") else str(x)[:10]

    return {"min_date": _fmt(row[0]), "max_date": _fmt(row[1])}

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
        SELECT code, name, amount, turnover_rate
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
            SELECT code, name, amount, turnover_rate
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
        SELECT code, name, amount, turnover_rate
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
        SELECT code, trade_date, amount, turnover_rate,
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
        f"SELECT code, name, concepts FROM read_parquet('{MAP_FILE}')"
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

# ───────────────────────── 概念面板 API ─────────────────────────

def _resolve_map_name(cf_name: str) -> str | None:
    """把 concept_flow 的概念名翻译成 stock_sector_map 里的概念名。"""
    if not CONCEPT_ALIAS_FILE.exists():
        return cf_name  # 别名表没建好时降级为原样匹配
    row = _q_fetchone(
        f"SELECT map_name FROM read_parquet({CONCEPT_ALIAS_FILE}) "
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
        return {
            "concept_code": concept_code, "concept_name": cf_name,
            "window_start": window_start, "window_end": window_end,
            "stocks": [],
        }
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
        return {
            "concept_code": concept_code, "concept_name": cf_name,
            "window_start": window_start, "window_end": window_end,
            "stocks": [],
        }

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
        f"SELECT code, name FROM read_parquet({MAP_FILE}) WHERE code IN ({ph2})",
        hit_codes,
    )
    name_by_code = dict(zip(name_df["code"], name_df["name"]))

    def _num(x):
        return float(x) if pd.notna(x) else None

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

    return {
        "concept_code": concept_code, "concept_name": cf_name,
        "window_start": window_start, "window_end": window_end,
        "stocks": stocks,
    }

_CN_NUM = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
           7: "七", 8: "八", 9: "九", 10: "十"}


def _board_label(n: int) -> str:
    return f"{_CN_NUM.get(n, str(n))}板"
# ───────────────────────── 连续涨停天数缓存（向量化） ─────────────────────────

def _vectorized_streak(dp_df: pd.DataFrame, buffered: list[str]) -> pd.DataFrame:
    """向量化计算连续涨停天数。

    - dp_df: 列 [code, trade_date, pct_change]，可全量也可已过滤
    - buffered: 日期列表（升序）
    - 返回: DataFrame [code, trade_date, streak_days]，只保留 streak_days >= 1
    """
    empty = pd.DataFrame(columns=["code", "trade_date", "streak_days"])
    if dp_df.empty:
        return empty

    df = dp_df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 10)
    df = df[df["trade_date"].isin(set(buffered))]
    if df.empty:
        return empty

    # ── 涨停判定（按板块阈值，向量化） ──
    prefix = df["code"].astype(str).str[:2]
    threshold = pd.Series(9.8, index=df.index)
    threshold[prefix.isin(["30", "68"])] = 19.8
    threshold[prefix.isin(["8", "4", "92"])] = 29.8
    df["is_lu"] = (df["pct_change"].fillna(-999) >= threshold).astype(np.int8)

    # ── pivot 成 code × date 矩阵（去重后保证唯一性） ──
    df = df.drop_duplicates(subset=["code", "trade_date"], keep="last")
    pivot = df.pivot(index="code", columns="trade_date", values="is_lu")
    pivot = pivot.reindex(columns=buffered)
    arr = pivot.fillna(-1).to_numpy(dtype=np.int8)   # -1 = 无数据（视为断板）

    # ── 逐列累加（230 列 × 5000 行，秒级） ──
    R, C = arr.shape
    streak = np.zeros((R, C), dtype=np.int32)
    cur = np.zeros(R, dtype=np.int32)
    for j in range(C):
        cur = np.where(arr[:, j] == 1, cur + 1, 0)
        streak[:, j] = cur

    # ── 提取 >=1 的单元格（纯 numpy，不用 stack） ──
    mask = streak >= 1
    if not mask.any():
        return empty
    rows, cols = np.where(mask)
    codes_arr = np.asarray(pivot.index, dtype=object)
    dates_arr = np.asarray(buffered, dtype=object)
    return pd.DataFrame({
        "code": codes_arr[rows],
        "trade_date": dates_arr[cols],
        "streak_days": streak[rows, cols],
    })


def _write_streak_partitions(long_df: pd.DataFrame, target_dates: list[str]) -> int:
    """把长表按日期拆分写盘。返回写盘天数。"""
    if long_df.empty:
        by_date = {}
    else:
        by_date = {
            d: g[["code", "streak_days"]]
            for d, g in long_df.groupby("trade_date")
        }

    n = 0
    for d in target_dates:
        cur = by_date.get(d)
        out = LIMIT_UP_STREAK_DIR / f"trade_date={d}"
        out.mkdir(parents=True, exist_ok=True)
        if cur is not None and len(cur):
            cur.to_parquet(out / "part.parquet", index=False)
        else:
            pd.DataFrame({
                "code": pd.Series([], dtype=str),
                "streak_days": pd.Series([], dtype=int),
            }).to_parquet(out / "part.parquet", index=False)
        n += 1
    return n


def rebuild_limit_up_streak(force: bool = False, limit_days: int = 200,
                            buffer_days: int = 30,
                            force_recent_days: int = 3) -> int:
    """增量构建连续涨停天数缓存（向量化，200 天约 2~3s）。"""
    import time
    t0 = time.time()

    all_td = _list_partition_dates(QUOTE_DIR)
    if not all_td:
        return 0

    target_dates = all_td[-limit_days:][::-1]
    recent_set = set(all_td[-force_recent_days:]) if force_recent_days > 0 else set()

    if not force:
        existing = set()
        if LIMIT_UP_STREAK_DIR.exists():
            existing = {
                p.name.split("=", 1)[1]
                for p in LIMIT_UP_STREAK_DIR.iterdir()
                if p.is_dir()
            }
        target_dates = [
            d for d in target_dates
            if d not in existing or d in recent_set
        ]

    if not target_dates:
        logger.info("[limit_up_streak] 目标日期均已缓存，跳过")
        return 0

    earliest = min(target_dates)
    idx = all_td.index(earliest)
    buf_start = max(0, idx - buffer_days)
    buffered = all_td[buf_start:]

    t1 = time.time()
    ph = ",".join(["?"] * len(buffered))
    dp_df = _q_df(
        f"SELECT code, trade_date, pct_change "
        f"FROM read_parquet({DAILY_PCT_GLOB}, hive_partitioning=true) "
        f"WHERE trade_date IN ({ph})", buffered)
    logger.info("[limit_up_streak] 读 daily_pct 耗时 %.2fs（%d 行）",
                time.time() - t1, len(dp_df))

    t2 = time.time()
    long_df = _vectorized_streak(dp_df, buffered)
    logger.info("[limit_up_streak] 向量计算耗时 %.2fs（%d 条 streak 记录）",
                time.time() - t2, len(long_df))

    t3 = time.time()
    n = _write_streak_partitions(long_df, target_dates)
    logger.info("[limit_up_streak] 写盘 %d 天耗时 %.2fs | 总耗时 %.2fs",
                n, time.time() - t3, time.time() - t0)
    return n


def _rebuild_streak_for_date(trade_date: str) -> None:
    """单日重算（向量化，前置 30 天预热）。"""
    all_td = _list_partition_dates(QUOTE_DIR)
    if trade_date not in all_td:
        return
    idx = all_td.index(trade_date)
    buf_start = max(0, idx - 30)
    buffered = all_td[buf_start:idx + 1]

    ph = ",".join(["?"] * len(buffered))
    dp_df = _q_df(
        f"SELECT code, trade_date, pct_change "
        f"FROM read_parquet({DAILY_PCT_GLOB}, hive_partitioning=true) "
        f"WHERE trade_date IN ({ph})", buffered)
    long_df = _vectorized_streak(dp_df, buffered)
    _write_streak_partitions(long_df, [trade_date])
    if not long_df.empty:
        cnt = len(long_df[long_df["trade_date"] == trade_date])
    else:
        cnt = 0
    logger.info("[limit_up_streak] 单日 %s：%d 只", trade_date, cnt)


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
        _, top_amount, top_pct, top_limit_up, concept_scores = \
            _get_tops_and_scores(trade_date)
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
        return float(x) if pd.notna(x) else None

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
            scores = [concept_scores.get(c, {}).get("score", 0.0)
                      for c in matched]
            importance = (sum(scores) / m) * (m ** 0.3)
        else:
            importance = 0.0

        r = qmap.get(code)
        if r is None:
            stocks.append({
                "code": code, "name": name,
                "streak_days": streak_map[code],
                "streak_label": _board_label(streak_map[code]),
                "concepts": sorted(cs),
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
    return {"trade_date": trade_date, "stocks": stocks}
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
    """返回相邻的实际交易日（基于 daily_pct 分区日期）。"""
    if direction not in ("prev", "next"):
        raise ValueError("direction 必须是 prev 或 next")

    dates = sorted(_list_daily_pct_dates())
    if not dates:
        return {"date": None}

    if direction == "prev":
        prev = [d for d in dates if d < trade_date]
        return {"date": prev[-1]} if prev else {"date": None}
    else:
        nxt = [d for d in dates if d > trade_date]
        return {"date": nxt[0]} if nxt else {"date": None}
# ───────────────────────── 测试 ─────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== 所有指标 ===")
    for it in list_available_indicators():
        print(f"  [{it['type']:6s}] {it['name']:16s} {it['description']}")

    print("\n=== 股票列表（前 5） ===")
    print(list_codes().head())

    print("\n=== 000001 默认指标 ===")
    df = get_stock_chart("000001", "2026-01-01", "2026-12-31")
    print(df.head())
    print("列：", list(df.columns))

    print("\n=== 000001 指定指标 [amp, net_ratio] ===")
    df2 = get_stock_chart("000001", "2026-01-01", "2026-12-31",
                          indicators=["amp", "net_ratio"])
    print(df2.head())
    print("列：", list(df2.columns))

    print("\n=== 000001 含 Python 指标 macd ===")
    df3 = get_stock_chart("000001", "2026-01-01", "2026-12-31",
                          indicators=["main_net_3d", "macd"])
    print(df3.head())
    print("列：", list(df3.columns))