"""历史数据批量抓取（通达信问小达）。

按日期区间批量拉取 stock / concept / industry 三类数据，每天一个 xlsx。

用法：
    python scripts/fetch_history.py --kind stock --start 2024-02-01 --end 2024-10-21
    python scripts/fetch_history.py --kind concept --start 2026-07-01 --end 2026-09-24
    python scripts/fetch_history.py --kind industry --start 2024-02-01 --end 2024-10-21

支持的 kind：
    stock     个股数据      → raw_data/个股数据/
    concept   概念指数      → raw_data/概念数据/
    industry  通达信二级行业 → raw_data/行业数据/

注意：
    - 本脚本只做「一次性历史补数据」，每日增量由 serve.py 自动完成
    - 相邻交易日之间会随机等待 5~8 秒，防服务端限流
    - 已有有效 xlsx（或对应 parquet）的日期自动跳过
    - 连续失败 5 次会中止
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    COOKIE,
    RAW_QUOTE_DIR, RAW_CONCEPT_DIR, RAW_INDUSTRY_DIR,
    QUOTE_DIR, CONCEPT_DIR, INDUSTRY_DIR,
)
from etl.tdx_client import TDXXiaoda
from etl.tdx_calendar import TradeCalendar
from etl.tdx_progress import Progress

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# kind → 参数配置
# ═══════════════════════════════════════════════════════════════
from etl.registry import DATA_TYPES

# 各类型的 words 前缀规则（lambda 仍然放这里，因为逻辑不通用）
_WORDS_FMT = {
    "stock":         lambda d, f: f"{d} AND ({f})",
    "industry":      lambda d, f: f"{d}{f}",
    "concept":       lambda d, f: f"{d}{f}",
    "stock_concept": lambda d, f: f"{d} AND ({f})",
    "amo":           lambda d, f: f"{d} AND ({f})",
}

KINDS = {
    k: {
        "label":       v["label"],
        "rang":        v["tdx_rang"],
        "data_fields": v["tdx_words"],
        "words_fmt":   _WORDS_FMT.get(k, lambda d, f: f"{d} {f}"),
        "raw_dir":     v["raw_dir"],
        "parquet_dir": v["parquet_dir"],
        "file_tpl":    f"{{date_cn}}{v['label']}.xlsx",
    }
    for k, v in DATA_TYPES.items()
}

# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def fetch_range(
    kind: str,
    start_date: str,
    end_date: str,
    *,
    cookie: str = "",
    output_dir: str | None = None,
    max_fail: int = 5,
) -> None:
    """按日期区间批量抓取某一类数据。"""
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {list(KINDS)} 之一，收到: {kind}")

    cfg = KINDS[kind]

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")

    cal = TradeCalendar()
    days = cal.trading_days(start, end)
    if not days:
        logger.warning("区间内无交易日。")
        return

    out_dir = Path(output_dir) if output_dir else cfg["raw_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(out_dir / "progress.json")
    logger.info("当前进度：%s", progress.summary())

    client = TDXXiaoda(cookie=cookie, rang=cfg["rang"])

    total = len(days)
    fail_count = 0

    for i, day in enumerate(days, 1):
        date_cn = day.strftime("%Y年%m月%d日")
        date_dot = day.strftime("%Y.%m.%d")
        date_key = day.strftime("%Y-%m-%d")
        file_path = out_dir / cfg["file_tpl"].format(date_cn=date_cn)

        # 跳过已有
        if progress.should_skip(date_key, cfg["parquet_dir"], file_path):
            logger.info("[%d/%d] %s 已有有效文件，跳过。", i, total, date_cn)
            continue

        logger.info("[%d/%d] 正在导出 %s %s ...", i, total, date_cn, cfg["label"])
        t0 = time.time()

        try:
            words = cfg["words_fmt"](date_cn, cfg["data_fields"])
            logger.info("  查询词：%s", words)

            nlpse_id = client.parse_query(words)
            ok = client.export_excel(
                nlpse_id, date_dot=date_dot, save_path=str(file_path))

            elapsed = time.time() - t0

            if not ok or not Progress.is_xlsx_valid(cfg["parquet_dir"], file_path):
                logger.warning("%s 导出无效。", date_cn)
                progress.mark(date_key, "failed", error="导出文件无效")
                fail_count += 1
                if fail_count >= max_fail:
                    logger.error("连续失败 %d 次，中止。", fail_count)
                    break
                continue

            fail_count = 0  # 成功则重置
            size = file_path.stat().st_size
            logger.info("[%d/%d] %s 完成，%d 字节，耗时 %.1fs",
                        i, total, date_cn, size, elapsed)
            progress.mark(date_key, "done", size=size,
                          elapsed=round(elapsed, 1), file=file_path.name)

        except Exception as exc:
            elapsed = time.time() - t0
            logger.error("%s 导出失败（耗时 %.1fs）：%s",
                         date_cn, elapsed, exc, exc_info=True)
            progress.mark(date_key, "failed",
                          error=f"{type(exc).__name__}: {exc}",
                          elapsed=round(elapsed, 1))
            fail_count += 1
            if fail_count >= max_fail:
                logger.error("连续失败 %d 次，中止。", fail_count)
                break

        # 交易日之间长间隔
        if i < total:
            gap = random.uniform(*client.cfg.day_delay)
            logger.info("  等待 %.0f 秒后处理下一个交易日 ...", gap)
            time.sleep(gap)

    logger.info("全部完成。最终进度：%s", progress.summary())


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="通达信问小达历史数据批量抓取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--kind", required=True, choices=list(KINDS.keys()),
                    help="数据类型：stock / concept / industry")
    ap.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--cookie", default=None,
                    help="ASPSessionID；默认用 config.COOKIE")
    ap.add_argument("--output-dir", default=None,
                    help="输出目录；默认用 config 里的 RAW_*_DIR")
    ap.add_argument("--max-fail", type=int, default=5,
                    help="连续失败 N 次后中止（默认 5）")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cookie = args.cookie or f"ASPSESSIONID={COOKIE}"
    fetch_range(
        kind=args.kind,
        start_date=args.start,
        end_date=args.end,
        cookie=cookie,
        output_dir=args.output_dir,
        max_fail=args.max_fail,
    )


if __name__ == "__main__":
    main()