"""个股 / 概念 / 行业 三类 xlsx → parquet 的字段映射规范与统一 schema。

设计原则
--------
1. 三类数据共享统一的英文列名，便于后续 DuckDB 跨表查询与 JOIN。
2. 列名稳定：一旦定义不再随意变更；下游 queries.py 依赖这些英文名。
3. 中文原始列名带单位/后缀（如 "(元)"、"(手)"、".前复权"），映射时统一去掉
   单位，由英文名隐含语义；数值类型由 pandas 自动推断。
4. trade_date 不作为列写入 parquet，而是通过 hive 分区目录
   ``trade_date=YYYY-MM-DD`` 表达，读取时由 ``hive_partitioning=true`` 注入。

统一英文 schema（13 个核心字段，三类共用）
---------------------------------------------
  code               证券/指数代码（统一为 6 位字符串）
  name               证券/指数简称
  pct_change         涨跌幅（%）
  open               开盘价（前复权）
  high               最高价（前复权）
  low                最低价（前复权）
  close              收盘价（前复权）
  volume             成交量（手）
  amount             成交额（元）
  huge_order_net     超大单净额（元）
  big_order_net      大单净额（元）
  medium_order_net   中单净额（元）
  small_order_net    小单净额（元）

类别专属字段
-----------
  industry           所属行业（仅「个股」保留；概念/行业不带该列）

备注
----
- 主力净额 = 超大单净额 + 大单净额，可由核心字段推导，故不单独保留。
- 「现价/最新价」不保留，统一用 close 表达当日收盘价。
- 原始 xlsx 首行数据为表头重复行（序号 == "序号"），转换时按 code 非数字过滤。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ───────────────────────── 字段映射表 ─────────────────────────


# 「个股」原始中文列 → 标准英文列

STOCK_COL_MAP: dict[str, str] = {
    "股票代码": "code",
    "sec_code": "code",
    "股票简称": "name",
    "sec_name": "name",
    "涨跌幅(%)": "pct_change",
    "chg": "pct_change",
    "自由换手率(%)": "turnover_rate",
    "所属行业": "industry",
    "开盘价.前复权": "open",
    "最高价.前复权": "high",
    "最低价.前复权": "low",
    "收盘价.前复权": "close",
    "成交量(手)": "volume",
    "成交额(元)": "amount",
    "SCRPLUS.集中度90%": "scrplus_90",
    "SCRPLUS.集中度70%": "scrplus_70",
    "热搜度": "hot_rank",
    "超大单净额": "huge_order_net",
    "大单净额": "big_order_net",
    "中单净额": "medium_order_net",
    "小单净额": "small_order_net",
}
STOCK_OUTPUT_COLS: list[str] = [
    "code", "name", "pct_change", "industry", "turnover_rate",
    "open", "high", "low", "close", "volume", "amount", "scrplus_90", "scrplus_70", "hot_rank",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
]

# 「概念」原始中文列 → 标准英文列
CONCEPT_COL_MAP: dict[str, str] = {
    "sec_code": "code",
    "sec_name": "name",
    "指数代码": "code",
    "指数简称": "name",
    "开盘价.前复权": "open",
    "最高价.前复权": "high",
    "最低价.前复权": "low",
    "收盘价.前复权": "close",
    "涨跌幅(%).前复权": "pct_change",
    "成交量(手)": "volume",
    "成交额(元)": "amount",
    "自由流通市值(元)": "total_market_cap",
    "自由换手率(%)": "turnover_rate",
    "超大单净额": "huge_order_net",
    "大单净额": "big_order_net",
    "中单净额": "medium_order_net",
    "小单净额": "small_order_net",
    "最新涨停家数": "max_up_count",
    "最新上涨家数": "up_count",
    "最新下跌家数": "down_count",
}

CONCEPT_OUTPUT_COLS: list[str] = [
    "code", "name", 
    "open", "high", "low", "close", "pct_change", "volume", "amount",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
    "total_market_cap", "turnover_rate",
    "max_up_count", "up_count", "down_count"
]

# 「行业」原始中文列与概念一致（仅多一列冗余的「通达信二级行业」，与 name 重复，不保留）
INDUSTRY_COL_MAP: dict[str, str] = dict(CONCEPT_COL_MAP)
INDUSTRY_OUTPUT_COLS: list[str] = list(CONCEPT_OUTPUT_COLS)

# 跨类别统一 schema（取超集；概念/行业读回时 industry 列不存在，DuckDB 自动补 NULL）
UNIFIED_SCHEMA: list[str] = STOCK_OUTPUT_COLS


# ───────────────────────── 个股→板块映射 ─────────────────────────
#
# 「个股→行业/概念映射」为快照型数据（非每日行情），输出为单个 flat parquet，
# 不按 trade_date 分区。industry_code 与「行业」分区的 code 字段对齐（同为 6 位
# 字符串），可直接 JOIN：
#   map.industry_code  <->  industry_flow.code
# concepts 为分号分隔的概念名串，下游可用 DuckDB 的
#   unnest(string_split(concepts, ';'))  展开为长表做概念检索。
MAP_COL_MAP: dict[str, str] = {
    "股票代码": "code",
    "股票简称": "name",
    "所属通达信研究行业": "industry",
    "研究行业代码": "industry_code",
    "所属概念": "concepts",
    "概念个数": "concept_count",
}
MAP_OUTPUT_COLS: list[str] = [
    "code", "name", "industry", "industry_code", "concepts", "concept_count",
]
# 序号（行号）丢弃；研究行业市场代码恒为 1，丢弃。

INDEX_COL_MAP: dict[str, str] = {
    "trade_date": "trade_date",
    "total_amount": "total_amount",
    "sz_market_amount": "sz_market_amount",
    "cyb_amount": "cyb_amount",
    "star_amount": "star_amount",
    "sh_a_amount": "sh_a_amount",
}
INDEX_OUTPUT_COLS: list[str] = [
    "trade_date", "total_amount"
]

STOCK_CONCEPT_COL_MAP: dict[str, str] = {
    "股票代码": "code",
    "sec_code": "code",
    "股票简称": "name",
    "sec_name": "name",
    "涨跌幅(%)": "pct_change",
    "chg": "pct_change",
    "所属通达信研究行业": "industry",
    "研究行业代码": "industry_code",
    "所属概念": "concepts",
    "概念个数": "concepts_num"
}
STOCK_CONCEPT_OUTPUT_COLS: list[str] = [
    "code", "concepts"
]

STOCK_AMO_COL_MAP: dict[str, str] = {
    "股票代码": "code",
    "sec_code": "code",
    "股票简称": "name",
    "sec_name": "name",
    "涨跌幅(%)": "pct_change",
    "chg": "pct_change",
    "超大单总买金额": "huge_order_buy",
    "超大单总卖金额": "huge_order_sell",
    "大单总买金额": "big_order_buy",
    "大单总卖金额": "big_order_sell",
    "中单总买金额": "medium_order_buy",
    "中单总卖金额": "medium_order_sell",
    "小单总买金额": "small_order_buy",
    "小单总卖金额": "small_order_sell",
}
STOCK_AMO_OUTPUT_COLS: list[str] = [
    "code", "name", "huge_order_buy", "huge_order_sell",
    "big_order_buy", "big_order_sell","medium_order_buy",
    "medium_order_sell","small_order_buy", "small_order_sell",
]

# ───────────────────────── 日期解析 ─────────────────────────

# 支持文件名中的中文日期 "2026年9月9日" / "2026年09月09日"
_DATE_CN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
# 兼容旧式 8 位数字日期 "20240917"
_DATE_DIGIT = re.compile(r"(20\d{6})")
# 支持横杠分隔的日期 "2026-09-17"
_DATE_DASH = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
def parse_trade_date(filename: str) -> date:
    """从文件名解析交易日；优先中文日期，其次 8 位数字日期，最后横杠分隔日期。"""
    m = _DATE_CN.search(filename)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return date(y, mo, d)
    m = _DATE_DIGIT.search(filename)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    m = _DATE_DASH.search(filename)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return date(y, mo, d)
    raise ValueError(f"文件名无法解析日期: {filename}")


# ───────────────────────── 通用转换函数 ─────────────────────────

def ingest_xlsx(
    fn: Path,
    col_map: dict[str, str],
    output_cols: list[str],
    out_dir: Path,
    category: str,
) -> tuple[date, int]:
    """
    通用 xlsx → parquet 转换（hive 分区 ``trade_date=YYYY-MM-DD``）。

    流程（沿用原 etl_*.py 的数据转化思想，并修正列名/日期/脏行）：
      1. 从文件名解析交易日；
      2. 读 xlsx，按 col_map 重命名中文列；
      3. 过滤表头重复行 / 脏数据（code 非数字的行）；
      4. 只保留 output_cols（缺失列补 None 避免 KeyError）；
      5. code 规范化：整数字符串并左补零至 6 位；
      6. 写 parquet 到 out_dir/trade_date=YYYY-MM-DD/part.parquet。

    返回 ``(trade_date, 行数)``。
    """
    trade_date = parse_trade_date(fn.name)
    logger.info("[%s] 开始处理 %s (trade_date=%s)", category, fn.name, trade_date)

    df = pd.read_excel(fn)
    # 清洗列名：去空格、去换行、全角空格转普通空格
    df.columns = [
        str(c).strip().replace("\n", "").replace("\u3000", " ")
        for c in df.columns
    ]

    # 动态构建 rename：同一目标列只取第一个命中的源列
    rename_map = {}
    used_targets = set()
    for src, tgt in col_map.items():
        if src in df.columns and tgt not in used_targets:
            rename_map[src] = tgt
            used_targets.add(tgt)

    df = df.rename(columns=rename_map)

    # 只在「必需的目标列」真的缺失时才警告
    REQUIRED_COLS = {"code", "open", "high", "low", "close"}
    missing_targets = REQUIRED_COLS - set(df.columns)
    if missing_targets:
        logger.warning("[%s] 必需列缺失(将置空): %s | 原始列: %s",
                    category, sorted(missing_targets), list(df.columns))
    if "code" not in df.columns:
        raise ValueError(f"[{category}] 重命名后缺少 code 列: {fn.name}")

    # code 转数值，过滤表头重复行（序号=='序号'）与脏数据
    df["code"] = pd.to_numeric(df["code"], errors="coerce")
    before = len(df)
    df = df[df["code"].notna()].copy()
    if len(df) != before:
        logger.debug("[%s] 过滤脏行 %d -> %d", category, before, len(df))

    # 补齐缺失输出列（如某类别源列缺失）
    for c in output_cols:
        if c not in df.columns:
            df[c] = None
    df = df[output_cols]

    # code 规范化：整数字符串，左补零至 6 位
    df["code"] = df["code"].astype("Int64").astype(str).str.zfill(6)

    partition = out_dir / f"trade_date={trade_date}"
    partition.mkdir(parents=True, exist_ok=True)
    out_path = partition / "part.parquet"
    df.to_parquet(out_path, index=False)

    logger.info("[%s] %s 写入 %d 行", category, trade_date, len(df))
    return trade_date, len(df)
