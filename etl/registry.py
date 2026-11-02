"""数据类型注册表。

所有 ETL / 抓取相关的类型元信息集中在这里。
加新类型只需在本文件加一条 DATA_TYPES 条目，其它模块自动生效。

依赖方：
  - etl/daily_update.py       读 in_daily_etl=True 的类型做增量
  - etl/tdx_fetch.py          读所有类型（按 tdx_order）做每日抓取
  - scripts/fetch_history.py  读所有类型（用户指定 --kind）做历史抓取
"""
from __future__ import annotations

from config import (
    RAW_QUOTE_DIR, QUOTE_DIR,
    RAW_CONCEPT_DIR, CONCEPT_DIR,
    RAW_INDUSTRY_DIR, INDUSTRY_DIR,
    RAW_STOCK_CONCEPT_DIR, CONCEPT_DATE_DIR,
)

from etl.field_mapping import (
    STOCK_COL_MAP, STOCK_OUTPUT_COLS,
    CONCEPT_COL_MAP, CONCEPT_OUTPUT_COLS,
    INDUSTRY_COL_MAP, INDUSTRY_OUTPUT_COLS,
    STOCK_CONCEPT_COL_MAP, STOCK_CONCEPT_OUTPUT_COLS,
)


# ── AMO 类型（可选，缺失时自动跳过）──
try:
    from config import RAW_AMO_DIR, AMO_DIR
    from etl.field_mapping import STOCK_AMO_COL_MAP, STOCK_AMO_OUTPUT_COLS
    _AMO_AVAILABLE = True
except ImportError:
    RAW_AMO_DIR = AMO_DIR = None
    STOCK_AMO_COL_MAP = STOCK_AMO_OUTPUT_COLS = None
    _AMO_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
# 查询词（tdx_fetch 用）
# ═══════════════════════════════════════════════════════════════

WORDS_STOCK = (
    "全部A股，开盘价，收盘价，最高价，最低价，成交量，成交额，换手率，"
    "实际换手率，筹码分布，内盘，外盘，热度，主动买入，主动卖出，"
    "超大单净额，大单净额，中单净额，小单净额"
)
WORDS_CONCEPT = (
    "所有概念指数收盘价，涨跌幅，成交量，自由换手率，"
    "超大单净额，大单净额，中单净额，小单净额，涨停家数，上涨家数，下跌家数"
)
WORDS_INDUSTRY = (
    "通达信二级行业收盘价，涨跌幅，成交量，自由换手率，"
    "超大单净额，大单净额，中单净额，小单净额，涨停家数，上涨家数，下跌家数"
)
WORDS_STOCK_CONCEPT = "全部A股，收盘价，所属概念，所属通达信研究行业"
WORDS_AMO = (
    "全部A股收盘价、超大单总买金额、超大单总卖金额、大单总买金额、"
    "大单总卖金额、中单总买金额、中单总卖金额、小单总买金额、小单总总卖金额"
)


# ═══════════════════════════════════════════════════════════════
# 数据类型注册表
# ═══════════════════════════════════════════════════════════════

DATA_TYPES: dict[str, dict] = {
    "stock": {
        "label": "个股数据",
        "in_daily_etl": True,        # daily_update 处理
        "tdx_order": 3,              # tdx_fetch 执行顺序
        "tdx_rang": "AG",
        "tdx_words": WORDS_STOCK,
        "raw_dir": RAW_QUOTE_DIR,
        "parquet_dir": QUOTE_DIR,
        "col_map": STOCK_COL_MAP,
        "output_cols": STOCK_OUTPUT_COLS,
    },
    "industry": {
        "label": "行业数据",
        "in_daily_etl": True,
        "tdx_order": 4,
        "tdx_rang": "ZS",
        "tdx_words": WORDS_INDUSTRY,
        "raw_dir": RAW_INDUSTRY_DIR,
        "parquet_dir": INDUSTRY_DIR,
        "col_map": INDUSTRY_COL_MAP,
        "output_cols": INDUSTRY_OUTPUT_COLS,
    },
    "concept": {
        "label": "概念指数",
        "in_daily_etl": True,
        "tdx_order": 5,
        "tdx_rang": "ZS",
        "tdx_words": WORDS_CONCEPT,
        "raw_dir": RAW_CONCEPT_DIR,
        "parquet_dir": CONCEPT_DIR,
        "col_map": CONCEPT_COL_MAP,
        "output_cols": CONCEPT_OUTPUT_COLS,
    },
    "stock_concept": {
        "label": "A股概念数据",
        "in_daily_etl": True,
        "tdx_order": 2,
        "tdx_rang": "AG",
        "tdx_words": WORDS_STOCK_CONCEPT,
        "raw_dir": RAW_STOCK_CONCEPT_DIR,
        "parquet_dir": CONCEPT_DATE_DIR,
        "col_map": STOCK_CONCEPT_COL_MAP,
        "output_cols": STOCK_CONCEPT_OUTPUT_COLS,
    },
}

if _AMO_AVAILABLE:
    DATA_TYPES["amo"] = {
        "label": "买卖金额组成",
        "in_daily_etl": False,       # 暂未纳入 daily_update（手动跑 etl_amo.py）
        "tdx_order": 1,              # tdx_fetch 里最先抓
        "tdx_rang": "AG",
        "tdx_words": WORDS_AMO,
        "raw_dir": RAW_AMO_DIR,
        "parquet_dir": AMO_DIR,
        "col_map": STOCK_AMO_COL_MAP,
        "output_cols": STOCK_AMO_OUTPUT_COLS,
    }


# ═══════════════════════════════════════════════════════════════
# 对外 API
# ═══════════════════════════════════════════════════════════════

def get_spec(kind: str) -> dict:
    """按规范 key 取类型定义。"""
    if kind not in DATA_TYPES:
        raise ValueError(
            f"未知的数据类型: {kind}，可选: {list(DATA_TYPES.keys())}")
    return DATA_TYPES[kind]


def list_kinds(group: str = "all") -> list[str]:
    """返回规范 kind 列表。

    group:
        "all"        全部（按 DATA_TYPES 声明顺序）
        "daily_etl"  只有 in_daily_etl=True 的（daily_update 用）
        "tdx_fetch"  按 tdx_order 升序（tdx_fetch 用）
    """
    if group == "all":
        return list(DATA_TYPES.keys())

    if group == "daily_etl":
        return [k for k, v in DATA_TYPES.items() if v.get("in_daily_etl")]

    if group == "tdx_fetch":
        items = sorted(
            DATA_TYPES.items(),
            key=lambda kv: kv[1].get("tdx_order", 999),
        )
        return [k for k, _ in items]

    raise ValueError(f"未知 group: {group}")


def get_tdx_params() -> list[dict]:
    """tdx_fetch 用的参数列表（按 tdx_order 升序）。"""
    out = []
    for kind in list_kinds("tdx_fetch"):
        spec = DATA_TYPES[kind]
        out.append({
            "kind": spec["label"],         # 中文显示名（生成 xlsx 文件名用）
            "internal_kind": kind,         # 规范 key
            "rang": spec["tdx_rang"],
            "words": spec["tdx_words"],
            "raw_dir": spec["raw_dir"],
            "parquet_dir": spec["parquet_dir"],
        })
    return out