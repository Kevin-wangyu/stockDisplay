"""DuckDB 连接、线程锁、路径 Glob、基础列定义。

⚠️ 本模块是 core/ 的最底层，不依赖任何内部模块。
"""
from __future__ import annotations

import threading
from pathlib import Path

import duckdb
import pandas as pd

from config import DATA_DIR, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR


# ── 单进程复用连接 ──
_con = duckdb.connect()
_con_lock = threading.Lock()


def _q_df(sql: str, params=None) -> pd.DataFrame:
    """线程安全 → DataFrame。"""
    with _con_lock:
        return _con.execute(sql, params).df() if params else _con.execute(sql).df()


def _q_fetchone(sql: str, params=None):
    """线程安全 → 单行 tuple。"""
    with _con_lock:
        return _con.execute(sql, params).fetchone() if params else _con.execute(sql).fetchone()


def _q_fetchall(sql: str, params=None):
    """线程安全 → 行列表。"""
    with _con_lock:
        return _con.execute(sql, params).fetchall() if params else _con.execute(sql).fetchall()


# ── 路径 ──
BASE_DIR      = QUOTE_DIR.parent.parent        # 项目根目录
COMPACT_ROOT  = BASE_DIR / "data_compact"

DAILY_PCT_DIR       = DATA_DIR / "daily_pct"
MARKET_AMOUNT_FILE  = DATA_DIR / "market_amount.parquet"
DAILY_PCT_FILE      = DATA_DIR / "daily_pct.parquet"       # 旧单文件（兼容）
CONCEPT_ALIAS_FILE  = DATA_DIR / "concept_alias.parquet"


# ── Glob 生成 ──
def _make_glob(kind: str) -> str:
    """查询层：只读 compact（月合并）；无 compact 时 fallback 到 data。"""
    compact_dir = COMPACT_ROOT / kind
    data_dir    = DATA_DIR / kind
    if compact_dir.exists() and any(compact_dir.rglob("*.parquet")):
        p = str(compact_dir / "**" / "*.parquet").replace("\\", "/")
        return f"'{p}'"
    p = str(data_dir / "**" / "*.parquet").replace("\\", "/")
    return f"'{p}'"


def _make_glob_raw(kind: str) -> str:
    """增量层：只读 data/（hive 分区格式，单一来源）。"""
    data_dir = DATA_DIR / kind
    p = str(data_dir / "**" / "*.parquet").replace("\\", "/")
    return f"'{p}'"


# 查询层 glob
QUOTE_GLOB     = _make_glob("quote")
INDUSTRY_GLOB  = _make_glob("industry_flow")
CONCEPT_GLOB   = _make_glob("concept_flow")
DAILY_PCT_GLOB = _make_glob("daily_pct")
AMO_GLOB       = _make_glob("amo_components")

# 增量层 glob（只读 data/）
QUOTE_RAW_GLOB     = _make_glob_raw("quote")
INDUSTRY_RAW_GLOB  = _make_glob_raw("industry_flow")
DAILY_PCT_RAW_GLOB = _make_glob_raw("daily_pct")


# ── 基础列 ──
STOCK_BASE_COLS = [
    "trade_date", "code", "name", "pct_change", "industry",
    "open", "high", "low", "close", "volume", "amount",
    "turnover_rate", "hot_rank",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
]
SECTOR_BASE_COLS = [
    "trade_date", "code", "name", "pct_change",
    "open", "high", "low", "close", "volume", "amount",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
]
DEFAULT_INDICATORS = ["ma5", "ma10"]