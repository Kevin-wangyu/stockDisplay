"""行业/概念 K 线。"""
from __future__ import annotations

import logging
from typing import Sequence

import pandas as pd

from config import MAP_FILE
from core.db import (
    _q_df, _q_fetchone, INDUSTRY_GLOB, CONCEPT_GLOB,
    SECTOR_BASE_COLS, DEFAULT_INDICATORS,
)
from core.helpers import _dedup, _classify, _build_select_cols
from indicators import SQL_INDICATORS, PYTHON_INDICATORS
from queries.stock import _query_chart   # ← 复用

logger = logging.getLogger(__name__)


def get_sector_chart(
    sector_key: str,
    start: str,
    end: str,
    kind: str = "industry",
    indicators: Sequence[str] | None = None,
    key_col: str = "code",          # "code" 或 "name"
) -> pd.DataFrame:
    """行业/概念 K 线 + 指标。

    kind:    "industry" | "concept"
    key_col: "code"（默认）或 "name"（概念名称一般没有 code 时用）
    """
    if indicators is None:
        indicators = DEFAULT_INDICATORS
    if kind == "industry":
        glob = INDUSTRY_GLOB
    elif kind == "concept":
        glob = CONCEPT_GLOB
    else:
        raise ValueError(f"未知的 kind: {kind}（应为 industry 或 concept）")
    if key_col not in ("code", "name"):
        raise ValueError("key_col 只能是 code 或 name")

    return _query_chart(
        glob=glob,
        key_col=key_col,
        key_val=sector_key,
        start=start,
        end=end,
        indicators=indicators,
        base_cols=SECTOR_BASE_COLS,
    )



def get_stock_industry_chart(
    code: str,
    start: str,
    end: str,
    indicators: Sequence[str] | None = None,
) -> pd.DataFrame:
    """该股票所属行业的 K 线 + 指标。

    流程：
      1. stock_sector_map 查该 code 的 industry_code / industry
      2. 用 industry_code 到 industry_flow 查对应日期的数据
    """
    if indicators is None:
        indicators = ["main_net", "main_net_3d", "main_net_5d", "main_net_20d"]

    # 先解析 industry_code
    row = _q_fetchone(
        f"SELECT industry_code, industry "
        f"FROM read_parquet('{MAP_FILE}', hive_partitioning=true, union_by_name=true) WHERE code = ?",
        [code],
    )
    if not row or not row[0]:
        raise ValueError(f"未找到 {code} 的行业映射")

    industry_code, industry_name = row[0], row[1]

    indicators = _dedup(indicators)
    sql_names, py_names = _classify(indicators)

    cols = _build_select_cols(SECTOR_BASE_COLS, sql_names)
    sql = f"""
    SELECT
        {cols}
    FROM read_parquet({INDUSTRY_GLOB}, hive_partitioning=true, union_by_name=true)
    WHERE code = ?
      AND trade_date BETWEEN ? AND ?
    ORDER BY trade_date
    """

    df = _q_df(sql, [industry_code, str(start), str(end)])
    for name in py_names:
        df = PYTHON_INDICATORS[name]["func"](df)

    # 把行业名挂在 DataFrame 上（API 层会用）
    df.attrs["industry_name"] = industry_name
    df.attrs["industry_code"] = industry_code
    return df
