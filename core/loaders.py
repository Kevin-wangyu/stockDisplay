"""按日数据加载（带 lru_cache）+ 目录扫描。"""
from __future__ import annotations

import math
import logging
from pathlib import Path
from functools import lru_cache

import pandas as pd

from config import DATA_DIR, MAP_FILE, CONCEPT_DATE_DIR, QUOTE_DIR
from core.db import (
    _q_df, _q_fetchone, COMPACT_ROOT, DAILY_PCT_DIR, DAILY_PCT_FILE,
    CONCEPT_ALIAS_FILE,
)

logger = logging.getLogger(__name__)


# ── 目录扫描 ──
def _list_partition_dates(dir_path: Path) -> list[str]:
    if not dir_path.exists():
        return []
    out = []
    for p in dir_path.iterdir():
        if not p.is_dir() or not p.name.startswith("trade_date="):
            continue
        d = p.name.split("=", 1)[1]
        if len(d) == 10 and d[4] == "-" and (p / "part.parquet").exists():
            out.append(d)
    return sorted(out)


def _list_daily_pct_dates() -> set[str]:
    if not DAILY_PCT_DIR.exists():
        return set()
    out = set()
    for p in DAILY_PCT_DIR.iterdir():
        if not p.is_dir() or not p.name.startswith("trade_date="):
            continue
        if (p / "part.parquet").exists():
            out.add(p.name.split("=", 1)[1])
    return out


def _load_existing_dates(file: Path, col: str = "trade_date") -> set[str]:
    if not file.exists():
        return set()
    try:
        df = pd.read_parquet(file, columns=[col])
        return set(df[col].astype(str).str.slice(0, 10))
    except Exception:
        logger.exception("读取 %s 失败", file)
        return set()


# ── 别名表 ──
@lru_cache(maxsize=1)
def _load_alias_map() -> dict[str, str]:
    if not CONCEPT_ALIAS_FILE.exists():
        return {}
    df = _q_df(f"SELECT cf_name, map_name FROM read_parquet('{CONCEPT_ALIAS_FILE}')")
    return {r["cf_name"]: r["map_name"] for _, r in df.iterrows() if r["map_name"]}


@lru_cache(maxsize=1)
def _load_alias_reverse() -> dict[str, str]:
    if not CONCEPT_ALIAS_FILE.exists():
        return {}
    df = _q_df(f"SELECT cf_name, map_name FROM read_parquet('{CONCEPT_ALIAS_FILE}')")
    return {r["map_name"]: r["cf_name"] for _, r in df.iterrows() if r["map_name"]}

def _parse_concept_date_df(df: pd.DataFrame) -> tuple[dict, dict]:
    """把 [code, concepts] 的 DataFrame 解析成 (code_info, cf2codes)。"""
    names = _q_df(f"SELECT code, name FROM read_parquet('{MAP_FILE}')")
    name_map = dict(zip(names["code"], names["name"]))

    code_info, cf2codes = {}, {}
    for code, cs_str in zip(df["code"], df["concepts"]):
        cs = frozenset(
            c.strip() for c in str(cs_str or "").split(";") if c.strip()
        )
        code_info[code] = (name_map.get(code, ""), cs)
        for c in cs:
            cf2codes.setdefault(c, []).append(code)
    return code_info, cf2codes

# ── 按日成分（关键） ──
@lru_cache(maxsize=256)
def _load_stock_index_by_date(trade_date: str):
    """返回 (code_info: {code: (name, frozenset(cf_name))}, cf2codes)。

    查找链：
      1. 当日 concept_date 分区
      2. 当月 concept_date compact
      3. ⭐ 往前找最近 5 个交易日的 concept_date（新概念自愈）
      4. map + alias 反向（极老数据兜底）
    """
    from datetime import date as _date, timedelta as _td

    # 1. 当日 concept_date 分区
    path = CONCEPT_DATE_DIR / f"trade_date={trade_date}" / "part.parquet"
    if path.exists():
        df = pd.read_parquet(path, columns=["code", "concepts"])
        if not df.empty:
            return _parse_concept_date_df(df)

    # 2. 当月 compact
    month = trade_date[:7]
    mf = COMPACT_ROOT / "concept_date" / f"{month}.parquet"
    if mf.exists():
        df = _q_df(
            f"SELECT code, concepts FROM read_parquet('{mf}') "
            f"WHERE CAST(trade_date AS VARCHAR) = ?", [trade_date])
        if not df.empty:
            return _parse_concept_date_df(df)

    # 3. ⭐ 往前找最近 5 个交易日的 concept_date（复用）
    try:
        end_d = _date.fromisoformat(trade_date)
    except ValueError:
        end_d = None

    if end_d is not None:
        try:
            from trading_calendar import trading_days
            prev_days = [
                d.isoformat() for d in trading_days(end_d - _td(days=15), end_d)
                if d.isoformat() < trade_date
            ]
        except Exception:
            prev_days = []

        for prev_d in reversed(prev_days[-5:]):
            # 先找分区
            p = CONCEPT_DATE_DIR / f"trade_date={prev_d}" / "part.parquet"
            if p.exists():
                df = pd.read_parquet(p, columns=["code", "concepts"])
                if not df.empty:
                    logger.warning(
                        "[stock_index] %s 无 concept_date，复用 %s 的成分",
                        trade_date, prev_d)
                    return _parse_concept_date_df(df)

            # 再找 compact
            pm = prev_d[:7]
            pmf = COMPACT_ROOT / "concept_date" / f"{pm}.parquet"
            if pmf.exists():
                df = _q_df(
                    f"SELECT code, concepts FROM read_parquet('{pmf}') "
                    f"WHERE CAST(trade_date AS VARCHAR) = ?", [prev_d])
                if not df.empty:
                    logger.warning(
                        "[stock_index] %s 复用 %s 的成分（compact）",
                        trade_date, prev_d)
                    return _parse_concept_date_df(df)

    # 4. 最后 fallback：map + alias 反向（极老数据）
    logger.warning(
        "[stock_index] %s 往前 5 天也无 concept_date，走 map+alias fallback",
        trade_date)

    rev = _load_alias_reverse()
    stocks = _q_df(f"SELECT code, name, concepts FROM read_parquet('{MAP_FILE}')")
    code_info, cf2codes = {}, {}
    for code, name, cs_str in zip(stocks["code"], stocks["name"], stocks["concepts"]):
        maps = frozenset(c.strip() for c in str(cs_str or "").split(";") if c.strip())
        cfs = frozenset(rev.get(m, m) for m in maps)
        code_info[code] = (name or "", cfs)
        for c in cfs:
            cf2codes.setdefault(c, []).append(code)
    return code_info, cf2codes

# ══════════════════════════════════════════════════════════════
# 概念元信息（gld / ztnr）—— 独立表，不随交易日膨胀
# ══════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_concept_meta_table() -> dict:
    """加载 concept_meta.parquet 到内存。

    返回: {(code, topic_name): {"gld": float, "ztnr": str, "selected_date": str}}

    - 一次性约 40 MB 内存
    - 进程内永久缓存（lru_cache=1）
    - 文件不存在时返回空 dict
    """
    from config import CONCEPT_META_FILE
    meta_path = CONCEPT_META_FILE
    if not meta_path.exists():
        logger.warning(
            "[concept_meta] %s 不存在，gld 将全部默认 1.0。"
            "请跑 step2_expand.py 生成。", meta_path)
        return {}

    df = pd.read_parquet(meta_path)
    out = {}
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        topic = str(r["topic_name"])
        try:
            gld = float(r["gld"])
            if gld != gld:   # NaN
                gld = 1.0
        except (ValueError, TypeError):
            gld = 1.0
        out[(code, topic)] = {
            "gld": gld,
            "ztnr": str(r["ztnr"] or ""),
            "selected_date": str(r["selected_date"] or ""),
        }
    logger.info("[concept_meta] 加载 %d 条到内存", len(out))
    return out


def get_concept_gld(code: str, concept_name: str, default: float = 1.0) -> float:
    """按需查某股票某概念的 gld。

    - 不存在时返回 default（默认 1.0）
    - O(1) 查字典
    """
    table = _load_concept_meta_table()
    meta = table.get((str(code).zfill(6), str(concept_name)))
    return meta["gld"] if meta else default


def get_concept_ztnr(code: str, concept_name: str, default: str = "") -> str:
    """按需查某股票某概念的入选原因（ztnr）。

    - 不存在时返回 default（默认空串）
    - O(1) 查字典
    """
    table = _load_concept_meta_table()
    meta = table.get((str(code).zfill(6), str(concept_name)))
    return meta["ztnr"] if meta else default

# ── 当日 pct_change ──
@lru_cache(maxsize=256)
def _load_pct_today(trade_date: str) -> dict[str, float]:
    def _clean(df):
        out = {}
        for _, r in df.iterrows():
            try:
                v = float(r["pct_change"])
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                out[r["code"]] = v
        return out

    pdir = DAILY_PCT_DIR / f"trade_date={trade_date}"
    p = pdir / "part.parquet"
    if p.exists():
        return _clean(pd.read_parquet(p, columns=["code", "pct_change"]))

    month = trade_date[:7]
    mf = COMPACT_ROOT / "daily_pct" / f"{month}.parquet"
    if mf.exists():
        df = _q_df(
            f"SELECT code, pct_change FROM read_parquet('{mf}') "
            f"WHERE CAST(trade_date AS VARCHAR) = ?", [trade_date])
        if not df.empty:
            return _clean(df)

    if pdir.exists():
        alts = list(pdir.glob("*.parquet"))
        if alts:
            logger.warning("[daily_pct] %s 用 %s 兜底", trade_date, alts[0].name)
            return _clean(pd.read_parquet(alts[0], columns=["code", "pct_change"]))

    if DAILY_PCT_FILE.exists():
        df = pd.read_parquet(DAILY_PCT_FILE, columns=["code", "trade_date", "pct_change"])
        df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 10)
        df = df[df["trade_date"] == trade_date]
        return _clean(df)

    return {}