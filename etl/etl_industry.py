"""行业指数 xlsx → parquet。

字段映射规范（中文表头 → 英文标准字段）见 :mod:`etl.field_mapping`。
输出到 config.INDUSTRY_DIR 下的 hive 分区目录 trade_date=YYYY-MM-DD/part.parquet。
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import INDUSTRY_DIR
from etl.field_mapping import INDUSTRY_COL_MAP, INDUSTRY_OUTPUT_COLS, ingest_xlsx


def ingest(fn: Path):
    """转换单个行业 xlsx。返回 (trade_date, 行数)。"""
    return ingest_xlsx(fn, INDUSTRY_COL_MAP, INDUSTRY_OUTPUT_COLS, INDUSTRY_DIR, "行业")


if __name__ == "__main__":
    ingest(Path(sys.argv[1]))
