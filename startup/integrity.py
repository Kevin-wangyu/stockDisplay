"""启动时数据完整性检查。"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import duckdb

from config import QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR
from trading_calendar import trading_days

logger = logging.getLogger(__name__)


def _glob(dir_path: Path) -> str:
    p = str(dir_path / "**" / "*.parquet").replace("\\", "/")
    return f"'{p}'"


def check_data_integrity() -> None:
    """检查数据日期覆盖。只读 data/ 原始分区（权威完整）。"""
    con = duckdb.connect()

    items = [
        ("quote",           _glob(QUOTE_DIR)),
        ("industry_flow",   _glob(INDUSTRY_DIR)),
        ("concept_flow",    _glob(CONCEPT_DIR)),
        ("stock_concepts",  _glob(CONCEPT_DATE_DIR)),
    ]

    for name, glob_expr in items:
        try:
            r = con.execute(
                f"SELECT MIN(trade_date), MAX(trade_date) "
                f"FROM read_parquet({glob_expr}, hive_partitioning=true, union_by_name=true)"
            ).fetchone()
        except Exception as e:
            logger.warning("[check] %s 读取失败: %s", name, e)
            continue

        if not r or not r[0]:
            logger.warning("[check] %s 无数据", name)
            continue

        def _to_date(x):
            if isinstance(x, date):
                return x
            s = str(x).split(" ")[0].replace("-", "")
            if len(s) == 8:
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            return date.fromisoformat(str(x)[:10])

        min_d, max_d = _to_date(r[0]), _to_date(r[1])

        expected = set(trading_days(min_d, max_d))
        rows = con.execute(
            f"SELECT DISTINCT trade_date "
            f"FROM read_parquet({glob_expr}, hive_partitioning=true, union_by_name=true)"
        ).fetchall()
        actual = {_to_date(v[0]) for v in rows if v[0]}

        missing = sorted(expected - actual)
        logger.info(
            "[check] %-14s 范围 %s ~ %s | 应有 %d 天 | 已有 %d 天",
            name, min_d, max_d, len(expected), len(actual),
        )
        if missing:
            sample = ", ".join(d.isoformat() for d in missing[:15])
            suffix = f" … 共 {len(missing)} 天" if len(missing) > 15 else ""
            logger.warning("[check] %-14s 缺失 %d 个交易日: %s%s",
                           name, len(missing), sample, suffix)