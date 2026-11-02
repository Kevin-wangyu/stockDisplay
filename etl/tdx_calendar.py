"""抓取用的交易日历（从 akshare 缓存加载）。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from trading_calendar import _load_calendar

logger = logging.getLogger(__name__)


class TradeCalendar:
    """基于 akshare 的 A 股交易日历。"""

    def __init__(self) -> None:
        logger.info("正在获取 A 股交易日历...")
        s = _load_calendar()
        self._dates = set(s.dt.date.tolist())
        logger.info("交易日历加载完成，共 %d 个交易日。", len(self._dates))

    def is_trading_day(self, d: datetime) -> bool:
        return d.date() in self._dates

    def trading_days(self, start: datetime, end: datetime) -> list:
        out, cur = [], start
        while cur <= end:
            if self.is_trading_day(cur):
                out.append(cur)
            cur += timedelta(days=1)
        return out[::-1]