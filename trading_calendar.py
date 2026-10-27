"""交易日历，来自 akshare，本地 parquet 缓存。

首次调用会联网拉取，之后从 data/trading_calendar.parquet 读取。
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parent / "data" / "trading_calendar.parquet"


def _fetch_and_cache() -> pd.Series:
    """联网拉取并写入缓存。失败返回空 Series。"""
    try:
        import akshare as ak
    except ImportError:
        logger.error("未安装 akshare，无法获取交易日历。请 pip install akshare")
        return pd.Series(dtype="datetime64[ns]")

    try:
        df = ak.tool_trade_date_hist_sina()
        s = pd.to_datetime(df["trade_date"]).sort_values().reset_index(drop=True)
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        s.to_frame("trade_date").to_parquet(_CACHE_FILE, index=False)
        logger.info(
            "交易日历已更新并缓存：%d 条，范围 %s ~ %s",
            len(s), s.iloc[0].date(), s.iloc[-1].date(),
        )
        return s
    except Exception:
        logger.exception("akshare 拉取交易日历失败")
        return pd.Series(dtype="datetime64[ns]")

# 手动缓存：空结果不缓存，避免一次网络失败导致整个进程永久退化
_CAL_CACHE: pd.Series | None = None


def _load_calendar() -> pd.Series:
    """优先读本地缓存；缓存不存在或损坏则联网拉取。空结果不缓存，下次调用会重试。"""
    global _CAL_CACHE
    if _CAL_CACHE is not None:
        return _CAL_CACHE

    if _CACHE_FILE.exists():
        try:
            df = pd.read_parquet(_CACHE_FILE)
            s = pd.to_datetime(df["trade_date"]).sort_values().reset_index(drop=True)
            logger.info(
                "交易日历读取缓存：%d 条，范围 %s ~ %s",
                len(s), s.iloc[0].date(), s.iloc[-1].date(),
            )
            _CAL_CACHE = s
            return s
        except Exception:
            logger.exception("读取交易日历缓存失败，尝试重新拉取")

    s = _fetch_and_cache()
    if not s.empty:
        _CAL_CACHE = s
    else:
        logger.warning("交易日历拉取失败（空结果），本次不缓存，下次调用将重试")
    return s


def refresh() -> None:
    """强制重新联网拉取（比如每年更新）。"""
    global _CAL_CACHE
    _CAL_CACHE = None
    _fetch_and_cache()


def trading_days(start: date, end: date) -> list[date]:
    """返回 [start, end] 区间内的所有交易日（含端点）。"""
    cal = _load_calendar()
    if cal.empty:
        return []
    mask = (cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))
    return [d.date() for d in cal[mask]]


def refresh() -> None:
    """强制重新联网拉取（比如每年更新）。"""
    _load_calendar.cache_clear()
    _fetch_and_cache()

if __name__ == "__main__":
    print(_load_calendar())