"""按时间窗判断是否拉数据（09:15 / 15:30）。"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from config import QUOTE_DIR
from trading_calendar import trading_days
from startup.cookie import prompt_and_reload

logger = logging.getLogger(__name__)


def _has_quote_data(trade_date: date) -> bool:
    """quote 分区目录下是否已有该交易日的 parquet。"""
    p = QUOTE_DIR / f"trade_date={trade_date.isoformat()}"
    if not p.exists():
        return False
    return any(p.glob("*.parquet"))


def _prev_trading_day(d: date) -> date | None:
    """严格小于 d 的最近交易日。"""
    days = trading_days(d - timedelta(days=20), d - timedelta(days=1))
    return days[-1] if days else None


def _is_trading_day(d: date) -> bool:
    return bool(trading_days(d, d))


def _prompt_cookie_and_fetch(target_date: date, force_manual: bool = False) -> None:
    """统一流程：获取 cookie → 写入 config → 重载 → 拉数据。"""
    prompt_and_reload(force_manual=force_manual)

    print(f"[serve] 开始拉取 {target_date} 数据 …")
    try:
        import xdget_daily
        xdget_daily.run(target_date)
        print(f"[serve] {target_date} 数据拉取完成")
    except Exception as e:
        print(f"[serve] {target_date} 数据拉取失败: {e}")
        print("[serve] 继续启动服务，请手动检查数据/日志")


def ensure_data_ready(force_manual_cookie: bool = False) -> None:
    """按时间窗处理数据更新。

    ┌──────────────┬────────────────────────────────────────────┐
    │ < 09:15      │ 允许补"上一交易日"数据（需要 ASPSessionID）│
    │ 09:15~15:30  │ 拒绝补上一交易日数据；跳过当日数据          │
    │ >= 15:30     │ 更新"当日"数据（今日须是交易日）            │
    └──────────────┴────────────────────────────────────────────┘
    """
    now = datetime.now()
    now_t = now.time()
    today = now.date()

    T_0915 = datetime.strptime("09:15", "%H:%M").time()
    T_1530 = datetime.strptime("15:30", "%H:%M").time()

    # ── 窗口 1：9:15 前，可补上一交易日 ──
    if now_t < T_0915:
        prev_d = _prev_trading_day(today)
        if prev_d is None:
            logger.info("[serve] 无法确定上一交易日，跳过")
            return
        if _has_quote_data(prev_d):
            logger.info(f"[serve] 上一交易日 {prev_d} 已有数据，跳过 cookie 输入")
            return
        logger.info(f"[serve] 检测到上一交易日 {prev_d} 无数据，开始补拉…")
        _prompt_cookie_and_fetch(prev_d, force_manual=force_manual_cookie)
        return

    # ── 窗口 2：9:15 ~ 15:30，盘中，拒绝补数据 ──
    if now_t < T_1530:
        prev_d = _prev_trading_day(today)
        if prev_d and not _has_quote_data(prev_d):
            logger.warning(f"[serve] ⚠ 上一交易日 {prev_d} 数据未更新，且已超过 09:15")
            logger.info("[serve]   无法自动获取（行情源仅支持补拉至 09:15），"
                        "请手动获取后再运行 serve.py")
        else:
            logger.info(f"[serve] 盘中时段（09:15~15:30），跳过数据更新")
        return

    # ── 窗口 3：15:30 后，更新当日数据 ──
    if not _is_trading_day(today):
        logger.info(f"[serve] 今日（{today}）非交易日，跳过当日数据更新")
        return
    if _has_quote_data(today):
        logger.info(f"[serve] 今日（{today}）已有数据，跳过 cookie 输入")
        return
    logger.info(f"[serve] 检测到今日（{today}）无数据，开始拉取…")
    _prompt_cookie_and_fetch(today, force_manual=force_manual_cookie)