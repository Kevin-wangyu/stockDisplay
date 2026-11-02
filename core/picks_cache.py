"""交集榜 parquet 缓存（按日）。"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from config import DATA_DIR
from core.db import _q_df, _q_fetchone, COMPACT_ROOT, MARKET_AMOUNT_FILE

logger = logging.getLogger(__name__)

CONCEPT_PICKS_DIR = DATA_DIR / "concept_picks"


def _picks_cache_path(trade_date: str) -> Path:
    return CONCEPT_PICKS_DIR / f"trade_date={trade_date}" / "part.parquet"


def _load_cached_picks(trade_date: str):
    p = _picks_cache_path(trade_date)
    df = None
    if p.exists():
        df = pd.read_parquet(p)
    else:
        month = trade_date[:7]
        mf = COMPACT_ROOT / "concept_picks" / f"{month}.parquet"
        if mf.exists():
            df = _q_df(
                f"SELECT * FROM read_parquet('{mf}') "
                f"WHERE CAST(trade_date AS VARCHAR) = ?", [trade_date])

    if df is None or df.empty:
        return None

    if "matched_concepts" in df.columns:
        df["matched_concepts"] = df["matched_concepts"].fillna("").apply(
            lambda s: [x for x in str(s).split(";") if x])
    return df.to_dict(orient="records")


def _save_picks_cache(trade_date: str, picks: list[dict]) -> None:
    if not picks:
        return
    try:
        df = pd.DataFrame(picks)
        if "matched_concepts" in df.columns:
            df["matched_concepts"] = df["matched_concepts"].apply(
                lambda xs: ";".join(xs) if isinstance(xs, list) else "")
        out = _picks_cache_path(trade_date)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
    except Exception:
        logger.exception("写入 concept_picks 缓存失败 %s", trade_date)


def _is_stable_date(trade_date: str) -> bool:
    row = _q_fetchone(
        f"SELECT MAX(trade_date) AS m FROM read_parquet('{MARKET_AMOUNT_FILE}')")
    if not row or not row[0]:
        return False
    m = row[0]
    max_d = m if hasattr(m, "year") else pd.Timestamp(str(m)).date()
    try:
        td = pd.Timestamp(trade_date).date()
    except Exception:
        return False
    return (max_d - td).days >= 45