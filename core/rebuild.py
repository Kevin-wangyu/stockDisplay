"""汇总表增量构建：market_amount / daily_pct / limit_up_streak。"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from config import QUOTE_DIR, INDUSTRY_DIR
from core.db import (
    _q_df, _q_fetchone, MARKET_AMOUNT_FILE, DAILY_PCT_DIR, DATA_DIR,
    QUOTE_RAW_GLOB, INDUSTRY_RAW_GLOB, DAILY_PCT_RAW_GLOB,
)
from core.helpers import _limit_up_threshold
from core.loaders import _list_partition_dates, _list_daily_pct_dates, _load_existing_dates

logger = logging.getLogger(__name__)

LIMIT_UP_STREAK_DIR = DATA_DIR / "limit_up_streak"


# ═══ rebuild_market_amount ═══
def rebuild_market_amount() -> int:
    """增量汇总每日全市场成交额（对 industry_flow 当日所有记录 amount 求和）。

    写入 data/market_amount.parquet：
        trade_date (str, YYYY-MM-DD), total_amount (float, 元)
    返回本次新增的日期数。

    ⚠ 直接读 data/industry_flow/trade_date=xxx/part.parquet，
       不用 glob + hive_partitioning，避免列名绑定问题。
    """
    from config import INDUSTRY_DIR

    existing = _load_existing_dates(MARKET_AMOUNT_FILE)
    all_dates = _list_partition_dates(INDUSTRY_DIR)
    todo = [d for d in all_dates if d not in existing]
    if not todo:
        logger.info("[market_amount] 已是最新，共 %d 天", len(existing))
        return 0

    rows = []
    for d in todo:
        p = INDUSTRY_DIR / f"trade_date={d}" / "part.parquet"
        if not p.exists():
            logger.warning("[market_amount] %s 无分区文件，跳过", d)
            continue
        p_str = str(p).replace("\\", "/")

        try:
            r = _q_fetchone(
                f"SELECT SUM(amount) AS total FROM read_parquet('{p_str}')"
            )
        except Exception:
            logger.exception("[market_amount] 读 %s 失败", d)
            continue

        total = 0.0
        if r and r[0] is not None:
            try:
                v = float(r[0])
                if math.isfinite(v) and v > 0:
                    total = v
            except (TypeError, ValueError):
                pass

        if total <= 0:
            logger.warning("[market_amount] %s SUM=0 或非有限值，跳过", d)
            continue

        rows.append({"trade_date": d, "total_amount": total})

    if not rows:
        logger.info("[market_amount] 无新增有效行")
        return 0

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
    logger.info("[market_amount] 新增 %d 天，总计 %d 天", len(rows), len(merged))
    return len(rows)


# ═══ rebuild_daily_pct ═══
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
        SELECT DISTINCT code, trade_date, close
        FROM read_parquet({QUOTE_RAW_GLOB}, hive_partitioning=true,
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



# ═══ streak（向量化） ═══
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
                            buffer_days: int = 30, force_recent_days: int = 3) -> int:
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
        f"SELECT DISTINCT code, trade_date, pct_change "
        f"FROM read_parquet({DAILY_PCT_RAW_GLOB}, hive_partitioning=true) "
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
        f"SELECT DISTINCT code, trade_date, pct_change "
        f"FROM read_parquet({DAILY_PCT_RAW_GLOB}, hive_partitioning=true) "
        f"WHERE trade_date IN ({ph})", buffered)
    long_df = _vectorized_streak(dp_df, buffered)
    _write_streak_partitions(long_df, [trade_date])
    if not long_df.empty:
        cnt = len(long_df[long_df["trade_date"] == trade_date])
    else:
        cnt = 0
    logger.info("[limit_up_streak] 单日 %s：%d 只", trade_date, cnt)
