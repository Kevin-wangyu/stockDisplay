"""启动时检查最近 N 个交易日的整行缺失，可选自动补全。

整行缺失：concept_date 里有该股票，但 quote 里没有对应行。

与 audit_quote_with_eltdx.py 的区别：
  - audit_quote_with_eltdx.py: 查"有行但 close=0"（脏行）
  - 本模块: 查"连行都没有"（整行缺失）

用法（供 serve.py 调用）：
    from startup.missing_kline_check import check_recent_missing
    missing = check_recent_missing(n_days=5, auto_backfill=False)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import QUOTE_DIR, CONCEPT_DATE_DIR

logger = logging.getLogger(__name__)


def _to_tdx_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "51", "58", "11", "50")):
        return f"sh{c}"
    if c.startswith(("00", "30", "12", "15", "16", "18", "39")):
        return f"sz{c}"
    if c.startswith(("8", "4", "92")):
        return f"bj{c}"
    return f"sz{c}"


def _detect_missing(n_days: int) -> dict[str, list[str]]:
    """检测最近 n_days 个交易日的整行缺失。

    返回: {trade_date: [code, ...]}
    """
    from core.loaders import _list_partition_dates

    quote_dates = _list_partition_dates(QUOTE_DIR)
    if not quote_dates:
        return {}

    recent = quote_dates[-n_days:]
    logger.info("[missing-check] 检查最近 %d 天: %s ~ %s",
                len(recent), recent[0], recent[-1])

    result = {}

    for d in recent:
        # concept_date（应有股票）
        cd_p = CONCEPT_DATE_DIR / f"trade_date={d}" / "part.parquet"
        if not cd_p.exists():
            logger.debug("[missing-check] %s 无 concept_date，跳过", d)
            continue
        cd_df = pd.read_parquet(cd_p, columns=["code"])
        expected = set(cd_df["code"].astype(str).str.zfill(6))

        # quote（实际有数据）
        q_p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        if not q_p.exists():
            logger.warning("[missing-check] %s quote 分区不存在", d)
            continue
        q_df = pd.read_parquet(q_p, columns=["code"])
        actual = set(q_df["code"].astype(str).str.zfill(6))

        missing = sorted(expected - actual)
        if missing:
            result[d] = missing

    return result


def _backfill(missing: dict[str, list[str]]) -> dict[str, int]:
    """用 eltdx 补全缺失的 (code, trade_date)。

    返回: {trade_date: 补全成功数}
    """
    from eltdx import TdxClient

    if not missing:
        return {}

    # 按 code 分组，一次拉全历史
    by_code: dict[str, set[str]] = defaultdict(set)
    for d, codes in missing.items():
        for c in codes:
            by_code[c].add(d)

    logger.info("[missing-check] 需补全 %d 只股票", len(by_code))

    code_bars = {}
    t0 = time.time()
    done = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch(code):
        tdx_code = _to_tdx_code(code)
        try:
            with TdxClient(timeout=30) as cli:
                s = cli.bars.get(tdx_code, period="day",
                                 adjust="qfq", all_pages=True)
            out = {}
            for b in s.bars:
                out[b.time.strftime("%Y-%m-%d")] = {
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume_lots),
                    "amount": float(b.amount),
                }
            return code, out
        except Exception as e:
            logger.warning("[missing-check] %s eltdx 失败: %s", code, e)
            return code, {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch, c): c for c in by_code}
        for fut in as_completed(futures):
            code, bars = fut.result()
            code_bars[code] = bars
            done += 1
            if done % 10 == 0:
                logger.info("[missing-check] eltdx 进度 %d/%d，已用 %.0fs",
                            done, len(by_code), time.time() - t0)

    # 按日期分组写盘
    by_date_rows: dict[str, list[dict]] = defaultdict(list)
    skipped_suspended = 0

    for d, codes in missing.items():
        for code in codes:
            bars = code_bars.get(code, {})
            if d not in bars:
                # eltdx 也无 → 停牌
                skipped_suspended += 1
                continue
            bar = bars[d]
            by_date_rows[d].append({
                "code": code,
                "name": "",
                "pct_change": None,
                "industry": None,
                "turnover_rate": None,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                "amount": bar["amount"],
                "scrplus_90": None, "scrplus_70": None, "hot_rank": None,
                "huge_order_net": None, "big_order_net": None,
                "medium_order_net": None, "small_order_net": None,
            })

    # 逐日写盘
    result = {}
    for d, rows in by_date_rows.items():
        p = QUOTE_DIR / f"trade_date={d}" / "part.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        existing = set(df["code"].astype(str))
        new_rows = [r for r in rows if r["code"] not in existing]
        if not new_rows:
            continue
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        df.to_parquet(p, index=False)
        result[d] = len(new_rows)
        logger.info("[missing-check] %s 补全 %d 行", d, len(new_rows))

    if skipped_suspended:
        logger.info("[missing-check] %d 只次 eltdx 也无数据（停牌/退市），跳过",
                    skipped_suspended)

    return result


def check_recent_missing(n_days: int = 5, auto_backfill: bool = False) -> dict:
    """检查（并可选补全）最近 n_days 个交易日的整行缺失。

    返回:
      {
        "checked_days": int,
        "missing": {date: [codes]},
        "backfilled": {date: count} | None,
        "elapsed_s": float,
      }
    """
    t0 = time.time()

    missing = _detect_missing(n_days)
    total = sum(len(v) for v in missing.values())

    result = {
        "checked_days": len(missing),
        "missing": missing,
        "backfilled": None,
        "elapsed_s": 0,
    }

    if not missing:
        logger.info("[missing-check] 最近 %d 天无整行缺失", n_days)
        result["elapsed_s"] = round(time.time() - t0, 1)
        return result

    logger.warning("[missing-check] 最近 %d 天发现 %d 只次整行缺失，涉及 %d 天",
                   n_days, total, len(missing))
    for d, codes in missing.items():
        sample = ", ".join(codes[:5])
        suffix = f" 等 {len(codes)} 只" if len(codes) > 5 else ""
        logger.warning("  %s: %s%s", d, sample, suffix)

    if auto_backfill:
        logger.info("[missing-check] 开始用 eltdx 补全…")
        result["backfilled"] = _backfill(missing)
        total_filled = sum(result["backfilled"].values())
        logger.info("[missing-check] 补全完成：%d 行写入 %d 天",
                    total_filled, len(result["backfilled"]))
    else:
        logger.warning("[missing-check] 未开启自动补全（加 --auto-backfill-missing 启用）")
        logger.warning("[missing-check] 手动补全：")
        logger.warning("  python scripts\\audit_quote_with_eltdx.py --yes")
        logger.warning("  或: python scripts\\backfill_single_stock_day.py <code> <date>")

    result["elapsed_s"] = round(time.time() - t0, 1)
    return result