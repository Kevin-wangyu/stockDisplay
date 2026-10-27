"""指标注册表。

新增指标方式
------------
SQL 指标（可直接用 SQL 表达式表达，含窗口函数）:
    register_sql("my_indicator",
                 expr="SUM(volume) OVER (ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)",
                 description="5日累计成交量")

Python 指标（需要 pandas 后处理）:
    def _my_func(df, *args):
        df["my_col"] = ...
        return df
    register_python("my_indicator", _my_func, "描述")

下游 queries.py 依赖 SQL_INDICATORS / PYTHON_INDICATORS 两个字典。
注册时名字必须唯一，重复注册会抛异常。
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ───────────────────────── 注册表 ─────────────────────────

SQL_INDICATORS: dict[str, dict] = {}     # name -> {"expr": str, "description": str}
PYTHON_INDICATORS: dict[str, dict] = {}  # name -> {"func": callable, "description": str}


SQL_INDICATORS: dict[str, dict] = {}

def register_sql(name: str, expr: str, description: str = "", scope: str = "all"):
    if name in SQL_INDICATORS or name in PYTHON_INDICATORS:
        raise ValueError(f"指标名已存在: {name}")
    SQL_INDICATORS[name] = {
        "expr": expr,
        "description": description,
        "scope": scope,
    }


def register_python(name: str, func: Callable[[pd.DataFrame], pd.DataFrame],
                    description: str = "") -> None:
    if name in SQL_INDICATORS or name in PYTHON_INDICATORS:
        raise ValueError(f"指标名已存在: {name}")
    PYTHON_INDICATORS[name] = {"func": func, "description": description}


def list_indicators() -> list[dict]:
    out = []
    for name, meta in SQL_INDICATORS.items():
        out.append({
            "name": name,
            "type": "sql",
            "description": meta["description"],
            "scope": meta.get("scope", "all"),
        })
    for name, meta in PYTHON_INDICATORS.items():
        out.append({
            "name": name,
            "type": "python",
            "description": meta["description"],
            "scope": meta.get("scope", "all"),
        })
    return out


def get_indicator(name: str) -> dict:
    if name in SQL_INDICATORS:
        return {"name": name, "type": "sql", **SQL_INDICATORS[name]}
    if name in PYTHON_INDICATORS:
        return {"name": name, "type": "python", **PYTHON_INDICATORS[name]}
    raise KeyError(f"未注册的指标: {name}")


# ───────────────────────── 内置 SQL 指标 ─────────────────────────

register_sql("volume", "volume", "成交量（原始）")
register_sql("net_ratio", "(huge_order_net + big_order_net) / NULLIF(amount, 0)",
             "主力净额占成交额比")
register_sql("scrplus_70", "scrplus_70", "集中度70%", scope="stock")
register_sql("scrplus_90", "scrplus_90", "集中度90%", scope="stock")

## ───────────────────────── 热度 ─────────────────────────
register_sql("hot_rank", "hot_rank", "热度", scope="stock")
# register_sql("max_up_count", "max_up_count", "涨停家数", scope="industry")
# register_sql("up_count", "up_count", "上涨家数", scope="industry")
# register_sql("down_count", "down_count", "下跌家数", scope="industry")

## ───────────────────────── 累计净额族 ─────────────────────────
# 超大单 / 大单 / 中单 / 小单 的 1/3/5/10/20/30/60 日累计净额
#
# 前端将渲染成级联下拉：悬停"超大单累计净额"时，
# 右侧展开 1日 / 3日 / 5日 / 10日 / 20日 / 30日 / 60日 子列。

_CUMULATIVE_DAYS = [1, 3, 5, 10, 20, 30, 60]

_CUMULATIVE_FAMILIES = [
    ("huge_order_net",   "huge_order_net",                     "超大单累计净额", "超大单净额"),
    ("big_order_net",    "big_order_net",                      "大单累计净额",   "大单净额"),
    ("medium_order_net", "medium_order_net",                   "中单累计净额",   "中单净额"),
    ("small_order_net",  "small_order_net",                    "小单累计净额",   "小单净额"),
    ("main_net",         "(huge_order_net + big_order_net)",   "主力净额累计净额", "主力净额"),
]

for _base, _expr_base, _family_label, _short_label in _CUMULATIVE_FAMILIES:
    for _d in _CUMULATIVE_DAYS:
        _name = f"{_base}_{_d}d"
        if _d == 1:
            _expr = _expr_base
        else:
            _expr = (
                f"SUM({_expr_base}) OVER (ORDER BY trade_date "
                f"ROWS BETWEEN {_d - 1} PRECEDING AND CURRENT ROW)"
            )
        register_sql(_name, _expr, f"{_d}日{_short_label}", scope="all")

CUMULATIVE_GROUPS = [
    {"key": _base, "label": _family_label, "days": list(_CUMULATIVE_DAYS)}
    for _base, _, _family_label, _ in _CUMULATIVE_FAMILIES
]





# ───────────────────────── 均线（MA） ─────────────────────────

for _n in (5, 10, 20, 30, 60, 120, 250):
    register_sql(
        f"ma{_n}",
        f"AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN {_n-1} PRECEDING AND CURRENT ROW)",
        f"{_n}日均线",
    )

# ───────────────────────── 布林带（BOLL） ─────────────────────────

_MID = "AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)"
_STD = "STDDEV(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)"

register_sql("boll_mid", _MID, "BOLL 中轨（MA20）")
register_sql("boll_up",  f"{_MID} + 2 * {_STD}", "BOLL 上轨")
register_sql("boll_low", f"{_MID} - 2 * {_STD}", "BOLL 下轨")

# ───────────────────────── 内置 Python 指标（示例） ─────────────────────────

def _macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD：返回 macd_dif / macd_dea / macd 三列。"""
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    df["macd_dif"] = dif
    df["macd_dea"] = dea
    df["macd"] = (dif - dea) * 2
    return df


register_python("macd", _macd, "MACD（返回 macd_dif / macd_dea / macd 三列）")


# ───────────────────────── 测试 ─────────────────────────

if __name__ == "__main__":
    import json
    print(f"共 {len(SQL_INDICATORS)} 个 SQL 指标, "
          f"{len(PYTHON_INDICATORS)} 个 Python 指标\n")
    print(json.dumps(list_indicators(), ensure_ascii=False, indent=2))