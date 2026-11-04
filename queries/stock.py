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
    """给个股 K 线追加未来 5/10/20/30 日涨幅。"""
    if df.empty:
        return df
    from datetime import date as _date, timedelta as _td
    from trading_calendar import trading_days
    from core.db import _q_df, QUOTE_GLOB

    c0 = _date.fromisoformat(end)
    fwd_days = [d.isoformat() for d in trading_days(c0, c0 + _td(days=120))]
    try:
        i0 = fwd_days.index(end)
    except ValueError:
        i0 = -1

    offsets = (5, 10, 20, 30)
    n2date = {n: fwd_days[i0 + n] for n in offsets if 0 <= i0 + n < len(fwd_days)}

    if not n2date:
        for n in offsets:
            df[f"fwd_{n}d"] = None
        return df

    date_list = list(set(n2date.values()))
    ph = ",".join(["?"] * len(date_list))
    fdf = _q_df(
        f"SELECT trade_date, close FROM read_parquet({QUOTE_GLOB}, hive_partitioning=true) "
        f"WHERE code = ? AND trade_date IN ({ph})",
        [code, *date_list])
    close_by_date = {str(r["trade_date"])[:10]: float(r["close"])
                     for _, r in fdf.iterrows() if pd.notna(r["close"])}

    for n in offsets:
        d_fwd = n2date.get(n)
        if d_fwd is None:
            df[f"fwd_{n}d"] = None
            continue
        target_close = close_by_date.get(d_fwd)
        if target_close is None:
            df[f"fwd_{n}d"] = None
            continue
        df[f"fwd_{n}d"] = (target_close / df["close"] - 1) * 100.0

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


def get_stock_chart(
    code: str,
    start: str,
    end: str,
    indicators: Sequence[str] | None = None,
) -> pd.DataFrame:
    """个股 K 线 + 指定指标。indicators=None 时使用默认指标集。"""
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
