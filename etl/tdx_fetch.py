"""抓取主流程：字段定义 + type_params + fetch_today_index + run。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from config import (
    COOKIE, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR,
    RAW_QUOTE_DIR, RAW_CONCEPT_DIR, RAW_INDUSTRY_DIR, RAW_DIR,
    RAW_STOCK_CONCEPT_DIR, CONCEPT_DATE_DIR, RAW_AMO_DIR, AMO_DIR,
)
from etl.tdx_client import TDXXiaoda
from etl.tdx_progress import Progress
from etl.tdx_calendar import TradeCalendar

logger = logging.getLogger(__name__)
from etl.registry import get_tdx_params

type_params = get_tdx_params()

# ── 模块级全局 ──
progress = Progress(RAW_DIR / "daily_data.json")
cal = TradeCalendar()
today = datetime.today()
if not cal.is_trading_day(today):
    logger.warning("非交易日仅获取最后数据")
while not cal.is_trading_day(today):
    today -= __import__("datetime").timedelta(days=1)


# ── 查询词 + 类型定义 ──


# ── 单类型抓取 ──
def fetch_today_index(cookie="", date=None, **kwargs):
    """拉取指定日期的某一类数据。

    - date: datetime.date 或 "YYYY-MM-DD" 字符串；None 时用模块级 today
    - kwargs 里的 kind/words/raw_dir/parquet_dir 由 type_params 传入
    """
    kind = kwargs.get("kind") or kwargs.get("type", "")
    rang = kwargs.get("rang", "")
    words = kwargs.get("words", "")
    raw_dir = kwargs.get("raw_dir", "")
    parquet_dir = kwargs.get("parquet_dir", "")

    # 1. 规范化 date
    if date is None:
        date = today
    elif isinstance(date, str):
        try:
            date = datetime.strptime(date[:10], "%Y-%m-%d").date()
        except ValueError:
            logger.error("[fetch] 非法日期字符串: %r", date)
            return
    if isinstance(date, datetime):
        date = date.date()

    # 2. 交易日校验
    if not cal.is_trading_day(datetime.combine(date, datetime.min.time())):
        logger.warning("[fetch] %s 非交易日，跳过（%s）", date, kind)
        return

    out_dir = Path(raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        client = TDXXiaoda(cookie=cookie, rang=rang)
    except Exception as exc:
        logger.exception("[fetch] TDXXiaoda 初始化失败（cookie=%s）",
                         cookie[:20])
        return

    date_cn = date.strftime("%Y年%m月%d日")
    date_dot = date.strftime("%Y.%m.%d")
    date_key = date.strftime("%Y-%m-%d")
    file_path = out_dir / f"{date_cn}{kind}.xlsx"

    if progress.should_skip(date_key, parquet_dir, file_path):
        logger.info("%s 已有有效文件，跳过。", date_cn + kind)
        return
    logger.info("正在导出 %s ...", date_cn + kind)
    t0 = time.time()

    try:
        logger.info("  查询词：%s", words)
        nlpse_id = client.parse_query(words)
        ok = client.export_excel(nlpse_id, date_dot=date_dot,
                                 save_path=str(file_path))
        elapsed = time.time() - t0

        if not ok or not Progress.is_xlsx_valid(parquet_dir, file_path):
            logger.warning("%s 导出无效。", date_cn)
            progress.mark(date_key, "failed", error="导出文件无效")
            return

        size = file_path.stat().st_size
        logger.info("%s 完成，%d 字节，耗时 %.1fs", date_cn, size, elapsed)
        progress.mark(date_key, "done", size=size,
                      elapsed=round(elapsed, 1), file=file_path.name)

    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("%s 导出失败（耗时 %.1fs）：%s",
                     date_cn, elapsed, exc, exc_info=True)
        progress.mark(date_key, "failed",
                      error=f"{type(exc).__name__}: {exc}",
                      elapsed=round(elapsed, 1))


# ── 入口 ──
def run(date=None):
    """拉取指定日期的所有类型数据。

    date: datetime.date / "YYYY-MM-DD" / None（用模块级 today）
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if date is None:
        date = today
    elif isinstance(date, str):
        date = datetime.strptime(date[:10], "%Y-%m-%d").date()
    if isinstance(date, datetime):
        date = date.date()

    logger.info("=" * 60)
    logger.info("tdx_fetch.run → 目标日期: %s，类型数: %d", date, len(type_params))
    logger.info("=" * 60)

    for kwargs in type_params:
        try:
            fetch_today_index(
                cookie=f"ASPSESSIONID={COOKIE}",
                date=date,
                **kwargs,
            )
        except Exception:
            logger.exception("[run] 类型 %s 拉取失败，继续下一个",
                             kwargs.get("kind") or kwargs.get("type"))