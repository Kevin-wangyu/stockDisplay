"""每日增量 ETL。

用法
----
    python -m etl.daily_update                     # 处理所有源目录
    python -m etl.daily_update --date 20260918     # 只处理指定日期
    python -m etl.daily_update --type stock        # 只处理个股
    python -m etl.daily_update --force             # 强制重跑已存在分区
    python -m etl.daily_update --type map --force  # 重建映射表
    python -m etl.daily_update --file "raw_data/个股数据/xxx.xlsx" --type stock

编程入口
--------
    from etl import daily_update
    daily_update.run(types=["stock", "industry", "concept", "map"],
                     target_date=None, force=False)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    DATA_DIR, 
    RAW_QUOTE_DIR, RAW_INDUSTRY_DIR, RAW_CONCEPT_DIR, RAW_STOCK_CONCEPT_DIR,
    QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR, RAW_AMO_DIR, AMO_DIR,
    MAP_FILE, MAP_RAW_FILE,
    setup_logging,
)
from etl.field_mapping import (
    STOCK_COL_MAP, STOCK_OUTPUT_COLS,
    INDUSTRY_COL_MAP, INDUSTRY_OUTPUT_COLS,
    CONCEPT_COL_MAP, CONCEPT_OUTPUT_COLS,
    MAP_COL_MAP, MAP_OUTPUT_COLS,
    STOCK_CONCEPT_COL_MAP, STOCK_CONCEPT_OUTPUT_COLS,
    STOCK_AMO_COL_MAP, STOCK_AMO_OUTPUT_COLS,
    parse_trade_date,
    ingest_xlsx,
)

logger = logging.getLogger("daily_update")

import duckdb
from functools import lru_cache
from config import DATA_DIR

# 定位冷数据目录
_COMPACT_DIR = DATA_DIR.parent / "data_compact"

# 直接用 kind 字符串映射，绝不用绝对路径匹配（跨平台/跨环境最稳）
_COMPACT_KIND_MAP = {
    "stock": "quote",
    "industry": "industry_flow",
    "concept": "concept_flow",
    "stock_concept": "concept_date",
    "amo_components": "amo_components"
}

@lru_cache(maxsize=32)
def _get_compact_dates(sub_name: str, year: int) -> set[str]:
    """读取合并文件日期，不依赖任何时间库转换，直接硬切字符串。"""
    year_file = _COMPACT_DIR / sub_name / f"trade_date={year}" / "part.parquet"
    if not year_file.exists():
        return set()
    try:
        con = duckdb.connect()
        df = con.execute(
            f"SELECT DISTINCT trade_date FROM read_parquet('{year_file}')"
        ).df()
        
        if df.empty:
            return set()
            
        res = set()
        # 无论它是 BIGINT(20240201)、DATE(2024-02-01) 还是带时间的字符串
        for val in df["trade_date"]:
            if val is None:
                continue
            # 去掉时间部分，去掉横杠，变成纯数字字符串
            s = str(val).split(" ")[0].split("T")[0].replace("-", "")
            # 如果长度够 8 位且前 8 位是数字，直接拼接
            if len(s) >= 8 and s[:8].isdigit():
                res.add(f"{s[:4]}-{s[4:6]}-{s[6:8]}")
        return res
        
    except Exception as e:
        logger.warning("读取合并文件 %s 失败: %s", year_file, e)
        return set()

# ───────────────────────── 类型规格 ─────────────────────────

TYPE_SPECS = {
    "amo_components":    (RAW_AMO_DIR, STOCK_AMO_COL_MAP, STOCK_AMO_OUTPUT_COLS, AMO_DIR),
    "stock":    (RAW_QUOTE_DIR,    STOCK_COL_MAP,    STOCK_OUTPUT_COLS,    QUOTE_DIR),
    "industry": (RAW_INDUSTRY_DIR, INDUSTRY_COL_MAP, INDUSTRY_OUTPUT_COLS, INDUSTRY_DIR),
    "concept":  (RAW_CONCEPT_DIR,  CONCEPT_COL_MAP,  CONCEPT_OUTPUT_COLS,  CONCEPT_DIR),
    "stock_concept": (RAW_STOCK_CONCEPT_DIR,  STOCK_CONCEPT_COL_MAP,  STOCK_CONCEPT_OUTPUT_COLS,  CONCEPT_DATE_DIR),
}
KINDS = ["amo_components","stock", "industry", "concept", "stock_concept"]


# ───────────────────────── 工具 ─────────────────────────

def partition_exists(out_dir: Path, trade_date: date, kind: str) -> bool:
    """检查该日期的数据是否已经存在（热分区或冷年文件）。"""
    # 1. 查热数据（按日分区）
    if (out_dir / f"trade_date={trade_date}" / "part.parquet").exists():
        return True
        
    # 2. 查冷数据（只要该年份的文件存在，就认为这一年的数据都有了）
    sub_name = _COMPACT_KIND_MAP.get(kind)
    if sub_name:
        year_file = _COMPACT_DIR / sub_name / f"trade_date={trade_date.year}" / "part.parquet"
        if year_file.exists():
            return True
            
    return False


def parse_date_arg(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"--date 需为 YYYYMMDD，收到: {s}")


# ───────────────────────── 单类处理 ─────────────────────────

def process_type(kind: str, target_date: date | None, force: bool) -> dict:
    """处理某一类。返回 stats: {processed, skipped, failed}。

    processed: [(文件名, trade_date, 行数), ...]
    skipped:   [(文件名, trade_date), ...]
    failed:    [(文件名, 错误信息), ...]
    """
    src_dir, col_map, out_cols, out_dir = TYPE_SPECS[kind]
    stats = {"processed": [], "skipped": [], "failed": []}

    if not src_dir.exists():
        logger.warning("[%s] 源目录不存在: %s", kind, src_dir)
        return stats

    files = sorted(src_dir.glob("*.xlsx"))
    if not files:
        logger.info("[%s] 无 xlsx 文件", kind)
        return stats

    logger.info("[%s] 扫描到 %d 个 xlsx 文件", kind, len(files))

    for fn in files:
        # 1. 解析日期
        try:
            trade_date = parse_trade_date(fn.name)
        except ValueError as e:
            logger.warning("[%s] 跳过（无法解析日期）: %s | %s", kind, fn.name, e)
            stats["failed"].append((fn.name, f"无法解析日期: {e}"))
            continue

        # 2. 日期过滤
        if target_date and trade_date != target_date:
            continue

        # 3. 幂等检查
        if not force and partition_exists(out_dir, trade_date, kind):
            logger.debug("[%s] 已存在，跳过: %s (%s)", kind, fn.name, trade_date)
            stats["skipped"].append((fn.name, trade_date))
            continue

        # 4. 写入
        try:
            d, n = ingest_xlsx(fn, col_map, out_cols, out_dir, kind)
            stats["processed"].append((fn.name, d, n))
        except Exception as e:
            logger.exception("[%s] 处理失败: %s", kind, fn.name)
            stats["failed"].append((fn.name, str(e)))

    logger.info("[%s] 完成: 处理 %d | 跳过 %d | 失败 %d",
                kind, len(stats["processed"]), len(stats["skipped"]), len(stats["failed"]))
    return stats


# ───────────────────────── 映射表 ─────────────────────────

def process_map(force: bool) -> dict:
    """构建 stock_sector_map.parquet。返回 {ok, rows, msg, filtered}。"""
    if MAP_FILE.exists() and not force:
        logger.info("[map] 已存在，跳过（用 --type map --force 重建）")
        return {"ok": True, "rows": 0, "filtered": 0, "msg": "已存在，跳过"}

    if not MAP_RAW_FILE.exists():
        logger.warning("[map] 原始文件不存在: %s", MAP_RAW_FILE)
        return {"ok": False, "rows": 0, "filtered": 0, "msg": "原始文件不存在"}

    logger.info("[map] 读取 %s", MAP_RAW_FILE)
    try:
        df = pd.read_excel(MAP_RAW_FILE).rename(columns=MAP_COL_MAP)
        if "code" not in df.columns:
            raise ValueError(f"重命名后缺少 code 列: {list(df.columns)}")

        df["code"] = pd.to_numeric(df["code"], errors="coerce")
        before = len(df)
        df = df[df["code"].notna()].copy()
        filtered = before - len(df)

        for c in MAP_OUTPUT_COLS:
            if c not in df.columns:
                df[c] = None
        df = df[MAP_OUTPUT_COLS]

        # ── 数值清洗：把 '--'、''、'N/A' 等占位符转成 NaN ──
        NUMERIC_COLS = ["concept_count"]
        for c in NUMERIC_COLS:
            if c in df.columns:
                df[c] = (
                    df[c]
                    .replace({"--": None, "-": None, "": None, "N/A": None, "NA": None})
                    .apply(pd.to_numeric, errors="coerce")
                )
                # 转成可空整型，避免 float 引入 .0
                df[c] = df[c].astype("Int64")

        # code 规范化
        df["code"] = df["code"].astype("Int64").astype(str).str.zfill(6)

        df.to_parquet(MAP_FILE, index=False)
        logger.info("[map] 写入 %d 行（过滤脏行 %d）", len(df), filtered)
        return {"ok": True, "rows": len(df), "filtered": filtered, "msg": "OK"}
    except Exception as e:
        logger.exception("[map] 失败")
        return {"ok": False, "rows": 0, "filtered": 0, "msg": str(e)}


# ───────────────────────── 编程入口 ─────────────────────────

def run(
    types: list[str] | None = None,
    target_date: date | None = None,
    force: bool = False,
    single_file: str | Path | None = None,
) -> dict:
    """编程入口，供 serve.py 等调用。返回 stats dict。"""
    setup_logging()
    t0 = time.time()

    if types is None:
        types = KINDS + ["map"]

    logger.info("=" * 64)
    logger.info("增量更新开始 | types=%s date=%s force=%s file=%s",
                types, target_date, force, single_file)
    logger.info("=" * 64)

    # 单文件模式
    if single_file:
        if len(types) != 1 or types[0] not in TYPE_SPECS:
            raise ValueError(f"单文件模式 types 必须为单一类别，收到 {types}")
        kind = types[0]
        src_dir, col_map, out_cols, out_dir = TYPE_SPECS[kind]
        fn = Path(single_file)
        if not fn.exists():
            raise FileNotFoundError(fn)
        d, n = ingest_xlsx(fn, col_map, out_cols, out_dir, kind)
        logger.info("[%s] 单文件处理完成: %s → %s (%d 行)", kind, fn.name, d, n)
        return {"single": {"file": str(fn), "date": d, "rows": n}}

    # 常规模式
    stats: dict = {}
    for kind in KINDS:
        if kind in types:
            stats[kind] = process_type(kind, target_date, force)
    if "map" in types:
        stats["map"] = process_map(force=force)

    elapsed = time.time() - t0

    # ── 全局摘要 ──
    logger.info("=" * 64)
    logger.info("运行摘要（用时 %.2f 秒）", elapsed)
    logger.info("-" * 64)

    total_files = total_rows = total_skipped = total_failed = 0
    for kind in KINDS:
        if kind not in stats:
            continue
        s = stats[kind]
        p, sk, f = len(s["processed"]), len(s["skipped"]), len(s["failed"])
        rows = sum(n for _, _, n in s["processed"])
        total_files += p
        total_rows += rows
        total_skipped += sk
        total_failed += f

        logger.info("  %-10s 处理 %3d 个文件 (%6d 行) | 跳过 %3d | 失败 %3d",
                    kind, p, rows, sk, f)
        for name, d, n in s["processed"]:
            logger.info("    ✓ %s → %s (%d 行)", name, d, n)
        for name, d in s["skipped"]:
            logger.debug("    - %s (%s 已存在)", name, d)
        for name, err in s["failed"]:
            logger.error("    ✗ %s: %s", name, err)

    if "map" in stats:
        m = stats["map"]
        tag = "OK" if m["ok"] else "FAILED"
        logger.info("  %-10s %s | %d 行 | 过滤脏行 %d | %s",
                    "map", tag, m["rows"], m["filtered"], m["msg"])

    logger.info("-" * 64)
    logger.info("  合计: 处理 %d 个文件, %d 行 | 跳过 %d | 失败 %d | 用时 %.2f 秒",
                total_files, total_rows, total_skipped, total_failed, elapsed)
    logger.info("=" * 64)

    return stats


# ───────────────────────── CLI ─────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="每日增量 ETL：Excel → Parquet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--type", choices=["stock", "industry", "concept", "map", "all"],
                   default="all", help="处理哪类数据（默认 all）")
    p.add_argument("--date", type=parse_date_arg, default=None,
                   help="只处理该日期（YYYYMMDD）")
    p.add_argument("--file", type=Path, default=None,
                   help="只处理单个 xlsx（需同时指定 --type）")
    p.add_argument("--force", action="store_true",
                   help="即使目标分区/映射已存在也重新处理")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.type == "all":
        types = KINDS + ["map"]
    elif args.type == "map":
        types = ["map"]
    else:
        types = [args.type]

    run(types=types, target_date=args.date, force=args.force,
        single_file=args.file)


if __name__ == "__main__":
    main()