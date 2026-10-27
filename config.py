import logging
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent
COOKIE = "17950683220840222118"
# 原始 Excel 存放目录
RAW_DIR = BASE_DIR / "raw_data"
RAW_QUOTE_DIR = RAW_DIR / "个股数据"
RAW_CONCEPT_DIR = RAW_DIR / "概念数据"
RAW_INDUSTRY_DIR = RAW_DIR / "行业数据"
RAW_STOCK_CONCEPT_DIR = RAW_DIR / "A股概念"
RAW_AMO_DIR = RAW_DIR / "成交组成"
RAW_INDEX_DIR = RAW_DIR

# Parquet 数据层
DATA_DIR = BASE_DIR / "data"
QUOTE_DIR = DATA_DIR / "quote"
INDUSTRY_DIR = DATA_DIR / "industry_flow"
CONCEPT_DIR = DATA_DIR / "concept_flow"
AMO_DIR = DATA_DIR / "amo_components"
DAILY_PCT_DIR = DATA_DIR / "daily_pct"


MAP_FILE = DATA_DIR / "stock_sector_map.parquet" # 行业概念映射文件
INDEX_FILE = DATA_DIR / "index_amount.parquet" # 指数资金流文件

# 个股→板块映射原始 xlsx（快照型，非每日数据）
MAP_RAW_DIR = BASE_DIR / "map"
MAP_RAW_FILE = MAP_RAW_DIR / "个股所属行业概念（2026年9月18日前用）.xlsx"
CONCEPT_DATE_DIR = DATA_DIR / "concept_date"
# 日志
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "etl.log"

# 确保目录存在
for d in [DATA_DIR, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    """配置 ETL 日志：同时输出到文件（追加）和控制台。幂等，可重复调用。"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if getattr(root, "_etl_configured", False):
        return
    root._etl_configured = True
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")  # 追加模式，保留历史
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(ch)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
