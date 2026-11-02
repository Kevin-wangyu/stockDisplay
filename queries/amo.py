"""AMO 买卖结构。"""
from __future__ import annotations

import logging

import pandas as pd

from core.db import _q_df, AMO_GLOB

logger = logging.getLogger(__name__)


def get_amo_chart(
    code: str, start: str, end: str,
) -> dict:
    """个股买卖金额结构（超大/大/中/小 单的主买主卖原始金额）。

    返回:
      {"rows": [
        {"trade_date": "2026-10-29",
         "huge_buy": ..., "big_buy": ..., "medium_buy": ..., "small_buy": ...,
         "huge_sell": ..., "big_sell": ..., "medium_sell": ..., "small_sell": ...,
         "total_buy": ..., "total_sell": ...},
        ...
      ]}
    """
    sql = f"""
    SELECT trade_date,
           huge_order_buy, huge_order_sell,
           big_order_buy, big_order_sell,
           medium_order_buy, medium_order_sell,
           small_order_buy, small_order_sell
    FROM read_parquet({AMO_GLOB}, hive_partitioning=true, union_by_name=true)
    WHERE code = ? AND trade_date BETWEEN ? AND ?
    ORDER BY trade_date
    """
    df = _q_df(sql, [code, str(start), str(end)])
    if df.empty:
        return {"rows": []}

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df[df["trade_date"].notna()]

    cols = ["huge_order_buy", "huge_order_sell",
            "big_order_buy", "big_order_sell",
            "medium_order_buy", "medium_order_sell",
            "small_order_buy", "small_order_sell"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    rows = []
    for _, r in df.iterrows():
        tb = (r["huge_order_buy"] + r["big_order_buy"]
              + r["medium_order_buy"] + r["small_order_buy"])
        ts = (r["huge_order_sell"] + r["big_order_sell"]
              + r["medium_order_sell"] + r["small_order_sell"])
        if tb + ts <= 0:
            continue   # 停牌 / 无成交
        rows.append({
            "trade_date": r["trade_date"],
            "huge_buy": float(r["huge_order_buy"]),
            "big_buy": float(r["big_order_buy"]),
            "medium_buy": float(r["medium_order_buy"]),
            "small_buy": float(r["small_order_buy"]),
            "huge_sell": float(r["huge_order_sell"]),
            "big_sell": float(r["big_order_sell"]),
            "medium_sell": float(r["medium_order_sell"]),
            "small_sell": float(r["small_order_sell"]),
            "total_buy": float(tb),
            "total_sell": float(ts),
        })

    return {"rows": rows}
