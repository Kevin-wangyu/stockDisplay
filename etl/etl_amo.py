"""概念指数 xlsx → parquet。

字段映射规范（中文表头 → 英文标准字段）见 :mod:`etl.field_mapping`。
输出到 config.CONCEPT_DIR 下的 hive 分区目录 trade_date=YYYY-MM-DD/part.parquet。
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import AMO_DIR
from etl.field_mapping import STOCK_AMO_COL_MAP, STOCK_AMO_OUTPUT_COLS, ingest_xlsx


def ingest(fn: Path):
    """转换单个概念 xlsx。返回 (trade_date, 行数)。"""
    return ingest_xlsx(fn, STOCK_AMO_COL_MAP, STOCK_AMO_OUTPUT_COLS, AMO_DIR, "买卖金额组成")


if __name__ == "__main__":
    ingest(Path(sys.argv[1]))
