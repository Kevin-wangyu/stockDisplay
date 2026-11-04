"""个股查询：K 线 / sectors / date_range / missing_days。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Sequence

import pandas as pd

from config import MAP_FILE
from core.db import (
    _q_df, _q_fetchone, QUOTE_GLOB, STOCK_BASE_COLS, DEFAULT_INDICATORS, INDUSTRY_GLOB, CONCEPT_GLOB
)
from core.helpers import _dedup, _classify, _build_select_cols
from indicators import SQL_INDICATORS, PYTHON_INDICATORS, list_indicators
from trading_calendar import trading_days

logger = logging.getLogger(__name__)
def _add_forward_returns(df: pd.DataFrame, code: str, end: str) -> pd.DataFrame:
    """给个股 K 线逐行追加未来 N 日的收盘涨幅 + 最高涨幅。

    对每一行（trade_date = T）：
      fwd_Nd      = (close[T+N] / close[T] - 1) * 100
      fwd_max_Nd  = (max(high[T+1..T+N]) / close[T] - 1) * 100

    规则：
      - 起点 T+1，终点 T+N（不含当日 T）
      - 未来交易日不足 N 天 → null
      - 未来某天 data 缺失（停牌）→ 该天 high/close 视为 null；
        若窗口内有效 high 数量 < N → fwd_max_Nd = null
      - 当前行 close 为 null 或 0 → 整行 fwd / fwd_max 全 null
    """
    OFFSETS = (5, 10, 20, 30)
    MAX_N = max(OFFSETS)

    # 1. 先保证所有列存在（即使 df 为空也返回完整 schema）
    for n in OFFSETS:
        if f"fwd_{n}d" not in df.columns:
            df[f"fwd_{n}d"] = None
        if f"fwd_max_{n}d" not in df.columns:
            df[f"fwd_max_{n}d"] = None

    if df.empty:
        return df

    from datetime import date as _date, timedelta as _td
    from trading_calendar import trading_days
    from core.db import _q_df, QUOTE_GLOB

    df = df.sort_values("trade_date").reset_index(drop=True)

    first_d = _date.fromisoformat(str(df.iloc[0]["trade_date"])[:10])
    last_d = _date.fromisoformat(end)

    # 2. 覆盖 df 全部日期 + 未来 MAX_N 个交易日
    span_days = MAX_N * 3 + 30
    all_days = [
        d.isoformat()
        for d in trading_days(first_d, last_d + _td(days=span_days))
    ]
    if not all_days:
        return df

    # 3. 一次查询所有 trade_date 的 close + high
    ph = ",".join(["?"] * len(all_days))
    fdf = _q_df(
        f"""
        SELECT trade_date, close, high
        FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true, union_by_name=true)
        WHERE code = ? AND trade_date IN ({ph})
        """,
        [code, *all_days],
    )

    ts_data: dict[str, dict] = {}
    for _, r in fdf.iterrows():
        d = str(r["trade_date"])[:10]
        ts_data[d] = {
            "close": float(r["close"]) if pd.notna(r["close"]) else None,
            "high":  float(r["high"])  if pd.notna(r["high"])  else None,
        }

    td_idx = {d: i for i, d in enumerate(all_days)}

    # 4. 逐行计算
    results = {n: {"fwd": [], "fwd_max": []} for n in OFFSETS}

    for _, row in df.iterrows():
        cur_date = str(row["trade_date"])[:10]
        cur_close = float(row["close"]) if pd.notna(row["close"]) else None
        cur_i = td_idx.get(cur_date)

        # 快速失败：无价格、或日期不在日历里
        if cur_close is None or cur_close == 0 or cur_i is None:
            for n in OFFSETS:
                results[n]["fwd"].append(None)
                results[n]["fwd_max"].append(None)
            continue

        # 拿未来 MAX_N 天的 [close, high]
        future = []
        for k in range(1, MAX_N + 1):
            if cur_i + k >= len(all_days):
                break
            d_fut = all_days[cur_i + k]
            info = ts_data.get(d_fut, {})
            future.append({
                "close": info.get("close"),
                "high":  info.get("high"),
            })

        for n in OFFSETS:
            # 未来交易日不足 N → null
            if len(future) < n:
                results[n]["fwd"].append(None)
                results[n]["fwd_max"].append(None)
                continue

            # fwd_Nd = close[T+N] / close[T] - 1
            target_close = future[n - 1]["close"]
            if target_close is None:
                results[n]["fwd"].append(None)
            else:
                results[n]["fwd"].append((target_close / cur_close - 1) * 100)

            # fwd_max_Nd = max(high[T+1..T+N]) / close[T] - 1
            window_highs = [
                x["high"] for x in future[:n] if x["high"] is not None
            ]
            if not window_highs:
                results[n]["fwd_max"].append(None)
            else:
                results[n]["fwd_max"].append((max(window_highs) / cur_close - 1) * 100)

    # 5. 写回 df
    for n in OFFSETS:
        df[f"fwd_{n}d"] = results[n]["fwd"]
        df[f"fwd_max_{n}d"] = results[n]["fwd_max"]

    return df
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


def get_stock_chart(code, start, end, indicators=None):
    if indicators is None:
        indicators = DEFAULT_INDICATORS
    df = _query_chart(
        glob=QUOTE_GLOB,
        key_col="code",
        key_val=code,
        start=start,
        end=end,
        indicators=indicators,
        base_cols=STOCK_BASE_COLS,
    )
    # ⭐ 追加 fwd_* / fwd_max_*
    df = _add_forward_returns(df, code, end)
    return df



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
    
    missing_sorted = sorted(expected - actual)

    if not missing_sorted:
        return {"missing": [], "suspected_suspension": []}

    # ⭐ 按连续段分组
    from datetime import date as _date
    dates = [_date.fromisoformat(d) for d in missing_sorted]
    groups = []
    cur_group = [dates[0]]
    for i in range(1, len(dates)):
        # 两个缺失日之间 ≤5 个自然日 → 同一组（覆盖周末）
        if (dates[i] - dates[i-1]).days <= 5:
            cur_group.append(dates[i])
        else:
            groups.append(cur_group)
            cur_group = [dates[i]]
    groups.append(cur_group)

    # 连续 ≥3 天 → 疑似停牌；否则 → 数据缺失
    suspected = []
    real_missing = []
    for g in groups:
        start = g[0].isoformat()
        end = g[-1].isoformat()
        n = len(g)
        if n >= 3:
            suspected.append({"start": start, "end": end, "days": n})
        else:
            real_missing.extend([d.isoformat() for d in g])

    logger.debug(
        "[missing] code=%s kind=%s | expected=%d actual=%d | "
        "缺失=%d（疑似停牌 %d 段）",
        code, kind, len(expected), len(actual),
        len(missing_sorted), len(suspected),
    )

    return {
        "missing": real_missing,
        "suspected_suspension": suspected,
    }
