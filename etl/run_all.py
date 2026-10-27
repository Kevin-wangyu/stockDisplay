"""批量 ETL：把 raw_data 下所有 xlsx 转为 parquet。

用法：python -m etl.run_all   或   python etl/run_all.py
日志：写入 config.LOG_FILE（追加），同时输出到控制台。
"""
import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    setup_logging,
    RAW_QUOTE_DIR,
    RAW_CONCEPT_DIR,
    RAW_INDUSTRY_DIR,
    RAW_STOCK_CONCEPT_DIR
)
from etl.etl_quote import ingest as ingest_quote
from etl.etl_concept import ingest as ingest_concept
from etl.etl_industry import ingest as ingest_industry
from etl.elt_stock_concept import ingest as ingest_stock_concepts
from etl.etl_map import build as build_map

logger = logging.getLogger("etl.run_all")


def main() -> None:
    setup_logging()
    tasks = [
        ("个股", RAW_QUOTE_DIR, ingest_quote),
        ("概念", RAW_CONCEPT_DIR, ingest_concept),
        ("行业", RAW_INDUSTRY_DIR, ingest_industry),
        ("个股概念", RAW_STOCK_CONCEPT_DIR, ingest_stock_concepts),
    ]
    for cat, raw_dir, fn in tasks:
        files = sorted(raw_dir.glob("*.xlsx"))
        logger.info("=== %s: 共 %d 个文件 ===", cat, len(files))
        ok = fail = 0
        for f in files:
            try:
                fn(f)
                ok += 1
            except Exception as e:
                logger.error("[%s] 处理失败 %s: %s", cat, f.name, e, exc_info=True)
                fail += 1
        logger.info("=== %s 完成: 成功 %d, 失败 %d ===", cat, ok, fail)

    # 个股→板块映射（快照，单文件，最后处理）
    try:
        build_map()
    except Exception as e:
        logger.error("[map] 处理失败: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
