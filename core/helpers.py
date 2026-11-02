"""无状态纯函数：阈值、标签、JSON 兜底、指标分类。"""
from __future__ import annotations

import math
from typing import Sequence

import pandas as pd

from core.db import _q_fetchone, MARKET_AMOUNT_FILE
from indicators import SQL_INDICATORS, PYTHON_INDICATORS

# ── 涨停阈值 ──
def _limit_up_threshold(code: str) -> float:
    p = str(code)[:2]
    if p in ("30", "68"):       return 19.8
    if p in ("8", "4", "92"):   return 29.8
    return 9.8


# ── 连板标签 ──
_CN_NUM = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
           7: "七", 8: "八", 9: "九", 10: "十"}


def _board_label(n: int) -> str:
    return f"{_CN_NUM.get(n, str(n))}板"


# ── JSON 兜底（递归转 inf/nan → None） ──
def _json_safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    return obj


# ── 指标分类 ──
def _dedup(seq) -> list:
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


def _classify(indicators):
    """把指标名分成 (sql_names, python_names)；未注册抛异常。"""
    sql_names, py_names = [], []
    for name in indicators:
        if name in SQL_INDICATORS:
            sql_names.append(name)
        elif name in PYTHON_INDICATORS:
            py_names.append(name)
        else:
            raise ValueError(f"未注册的指标: {name}")
    return sql_names, py_names


def _build_select_cols(base_cols, sql_names):
    """拼 SELECT 列表达式。"""
    parts = list(base_cols)
    for name in sql_names:
        expr = SQL_INDICATORS[name]["expr"]
        parts.append(f"({expr}) AS {name}")
    return ",\n        ".join(parts)


# ── 市场均量 ──
def _market_avg_amount(trade_date: str, days: int) -> float:
    row = _q_fetchone(
        f"""
        SELECT AVG(total_amount) FROM (
            SELECT total_amount FROM read_parquet('{MARKET_AMOUNT_FILE}')
            WHERE trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
        )
        """, [trade_date, days])
    if not row or row[0] is None:
        return 0.0
    try:
        f = float(row[0])
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0