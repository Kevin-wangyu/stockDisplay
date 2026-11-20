# -*- coding: utf-8 -*-
"""C6/C7 策略池增量构建（对照 core/rebuild.py::rebuild_limit_up_streak）。

设计原则
--------
1. 口径与 queries/c7_picks.py::_compute 逐股一致（已用 5 个日期验证
   only_live=0 / only_pre=0，见工作目录 prebuild_c7_verify.py）。
2. 增量范式照搬 rebuild_limit_up_streak：limit_days + buffer_days +
   force_recent_days，只写目标日期分区，日分区一律保留（零删除）。
3. fwd_max_5d/10d 是复盘列（未来数据），不参与筛选；靠近数据末尾的日期
   未来数据未到 → null，数据推进后该日期进入 recent_set 自动重算补全。
4. streak（连续在池天数）依赖往前 buffer；buffer_days=125 覆盖 ma60/lu20/ret5
   与绝大多数连续段，不影响 C6/C7 判定（判定只用当日可得窗口特征）。

写盘：data/c7_picks/trade_date=YYYY-MM-DD/part.parquet（新增派生缓存，
不覆盖任何原始行情/概念/榜单 parquet）。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb

from config import COMPACT_DIR, DATA_DIR, C7_POOL_DIR

logger = logging.getLogger(__name__)
_TMP = DATA_DIR.parent / "_duckdb_tmp"

_QG = f"read_parquet('{(COMPACT_DIR / 'quote' / '*.parquet').as_posix()}', union_by_name=true)"
_DG = f"read_parquet('{(COMPACT_DIR / 'daily_pct' / '*.parquet').as_posix()}', union_by_name=true)"
_CG = f"read_parquet('{(COMPACT_DIR / 'concept_date' / '*.parquet').as_posix()}', union_by_name=true)"
_BARS = f"read_parquet('{(COMPACT_DIR / '_tops_cache' / 'v1' / 'bars.parquet').as_posix()}')"
_META = f"read_parquet('{(COMPACT_DIR / '_tops_cache' / 'v1' / 'meta.parquet').as_posix()}')"

# 阈值与综合分基准（与 queries/c7_picks.py 完全一致）
THR = dict(lu_cnt_20d=1.0, main_net_norm=0.0, vol20=3.5238, turnover_rate=3.7,
           lu_gap=-3.0, close_hh20_ratio=0.95, ret_5d=4.8679, n_leader_concepts=1.0)
Z = dict(vol_m=3.010096, vol_s=1.639706, ret_m=0.848288, ret_s=7.840214,
         tor_m=4.794629, tor_s=5.705012)

C6COND = f"""lu_cnt_20d >= {THR['lu_cnt_20d']}
  AND main_net_norm > {THR['main_net_norm']}
  AND vol20 >= {THR['vol20']}
  AND turnover_rate >= {THR['turnover_rate']}
  AND lu_gap >= {THR['lu_gap']}
  AND close_hh20_ratio >= {THR['close_hh20_ratio']}
  AND ret_5d >= {THR['ret_5d']}
  AND ma5 > ma20 AND ma20 > ma60"""


def _list_quote_dates() -> list[str]:
    from core.loaders import _list_partition_dates
    return _list_partition_dates(COMPACT_DIR / "quote") or _list_partition_dates(
        DATA_DIR / "quote")


def _pool_sql(win_dates_sql: str) -> str:
    """win_dates_sql: 形如 (SELECT d FROM (...日期...) ) 的日期集合子查询。"""
    return f"""
WITH
qx AS (
  SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code, name, close, high,
         amount, turnover_rate, pct_change, huge_order_net, big_order_net
  FROM {_QG} WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates_sql}
),
dx AS (
  SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code,
         CASE WHEN isfinite(pct_change) THEN pct_change END AS pct_change
  FROM {_DG} WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates_sql}
),
cal AS (SELECT d, row_number() OVER (ORDER BY d) AS di FROM (SELECT DISTINCT d FROM qx)),
base AS (
  SELECT q.*, c.di, d.pct_change AS daily_pct,
    CASE WHEN left(q.code,2) IN ('30','68') THEN 19.8
         WHEN left(q.code,1) IN ('8','4') OR left(q.code,2)='92' THEN 29.8
         ELSE 9.8 END AS lu_thr
  FROM qx q JOIN cal c USING (d)
  LEFT JOIN dx d ON q.d=d.d AND q.code=d.code
),
st AS (
  SELECT b.*,
    avg(close) OVER w5  AS ma5,
    avg(close) OVER w20 AS ma20,
    avg(close) OVER w60 AS ma60,
    stddev_samp(daily_pct) OVER w20 AS vol20,
    max(high) OVER w20 AS hh20,
    lag(close,5) OVER wc AS close_5d_ago,
    sum(CASE WHEN daily_pct >= lu_thr-0.15 AND daily_pct IS NOT NULL THEN 1 ELSE 0 END)
        OVER w20 AS lu_cnt_20d
  FROM base b
  WINDOW wc AS (PARTITION BY code ORDER BY di),
         w5  AS (PARTITION BY code ORDER BY di ROWS BETWEEN 4  PRECEDING AND CURRENT ROW),
         w20 AS (PARTITION BY code ORDER BY di ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
         w60 AS (PARTITION BY code ORDER BY di ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
),
cdx AS (
  SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code, trim(t.x) AS cname
  FROM {_CG}, unnest(string_split(concepts,';')) AS t(x)
  WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates_sql}
),
sc AS (
  SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, concept_name AS cname,
         max(score) AS score FROM {_META} WHERE row_type='score' GROUP BY 1,2
),
lead AS (
  SELECT x.d, x.code, count(DISTINCT x.cname) FILTER (WHERE s.score>=0.8) AS n_leader_concepts
  FROM cdx x LEFT JOIN sc s ON x.d=s.d AND x.cname=s.cname
  GROUP BY x.d, x.code
),
feat AS (
  SELECT st.d AS trade_date, st.di, st.code, st.name, st.daily_pct AS pct_change,
    st.close, st.amount, st.lu_cnt_20d,
    (coalesce(st.huge_order_net,0)+coalesce(st.big_order_net,0))/NULLIF(st.amount,0) AS main_net_norm,
    st.vol20, st.turnover_rate, st.daily_pct - st.lu_thr AS lu_gap,
    st.close/NULLIF(st.hh20,0) AS close_hh20_ratio,
    (st.close/NULLIF(st.close_5d_ago,0)-1)*100 AS ret_5d,
    st.ma5, st.ma20, st.ma60,
    coalesce(ld.n_leader_concepts,0) AS n_leader_concepts
  FROM st LEFT JOIN lead ld ON st.d=ld.d AND st.code=ld.code
),
flag AS (
  SELECT *, CAST(({C6COND}) AS INT) AS c6,
            CAST(({C6COND} AND n_leader_concepts >= {THR['n_leader_concepts']}) AS INT) AS c7
  FROM feat
),
run6 AS (SELECT *, di - row_number() OVER (PARTITION BY code ORDER BY di) AS grp FROM flag WHERE c6=1),
st6 AS (SELECT *, row_number() OVER (PARTITION BY code,grp ORDER BY di) AS s6 FROM run6),
run7 AS (SELECT *, di - row_number() OVER (PARTITION BY code ORDER BY di) AS grp FROM flag WHERE c7=1),
st7 AS (SELECT *, row_number() OVER (PARTITION BY code,grp ORDER BY di) AS s7 FROM run7),
concepts AS (
  SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code,
         string_agg(DISTINCT t.x, ';' ORDER BY t.x) AS cfs,
         count(DISTINCT t.x) AS n_cfs
  FROM {_CG}, unnest(string_split(concepts,';')) AS t(x)
  WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates_sql}
  GROUP BY substr(CAST(trade_date AS VARCHAR),1,10), code
),
fwd AS (
  SELECT d, di, code,
    max(high) OVER (PARTITION BY code ORDER BY di RANGE BETWEEN 1 FOLLOWING AND 5  FOLLOWING) AS hi5,
    max(high) OVER (PARTITION BY code ORDER BY di RANGE BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS hi10,
    count(*)  OVER (PARTITION BY code ORDER BY di RANGE BETWEEN 1 FOLLOWING AND 5  FOLLOWING) AS n5,
    count(*)  OVER (PARTITION BY code ORDER BY di RANGE BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS n10
  FROM base
),
pool AS (
  SELECT s6.trade_date, s6.code, s6.name, s6.close, s6.pct_change, s6.turnover_rate,
    s6.vol20, s6.ret_5d, s6.lu_cnt_20d, s6.main_net_norm, s6.n_leader_concepts,
    s6.lu_gap, s6.close_hh20_ratio, s6.ma5, s6.ma20, s6.ma60,
    CASE WHEN s7.code IS NOT NULL THEN 'C7' ELSE 'C6' END AS source,
    CASE WHEN s7.code IS NOT NULL THEN s7.s7 ELSE s6.s6 END AS streak_days,
    coalesce(c.cfs,'') AS matched_concepts, coalesce(c.n_cfs,0) AS n_concepts,
    CASE WHEN fw.n5=5  THEN (fw.hi5 /NULLIF(s6.close,0)-1)*100 END AS fwd_max_5d,
    CASE WHEN fw.n10=10 THEN (fw.hi10/NULLIF(s6.close,0)-1)*100 END AS fwd_max_10d,
    ((s6.vol20-{Z['vol_m']})/{Z['vol_s']}) + ((s6.ret_5d-{Z['ret_m']})/{Z['ret_s']})
      + ((s6.turnover_rate-{Z['tor_m']})/{Z['tor_s']}) AS c7_score
  FROM st6 s6
  LEFT JOIN st7 s7 ON s6.trade_date=s7.trade_date AND s6.code=s7.code
  LEFT JOIN concepts c ON c.d=s6.trade_date AND c.code=s6.code
  LEFT JOIN fwd fw ON fw.d=s6.trade_date AND fw.di=s6.di AND fw.code=s6.code
  WHERE s6.c6=1
)
SELECT * FROM pool ORDER BY trade_date, c7_score DESC
"""


def _template_schema():
    """取一个已有正确分区的 pyarrow schema 作为写盘模板，统一 matched_concepts 为 list<string>。"""
    import pyarrow.parquet as pq
    for p in sorted(C7_POOL_DIR.glob("trade_date=*/part.parquet")):
        try:
            sch = pq.read_schema(p)
        except Exception:
            continue
        if "matched_concepts" in sch.names and "list" in str(sch.field("matched_concepts").type).lower():
            return sch
    return None


def _fwd_incomplete_dates(all_td: list[str], lookback: int = 15) -> list[str]:
    """找已缓存、未来数据已齐(距末尾>=10)、但 fwd_max_10d 全 null 的近期日期。

    这类日期是"数据推进后需补全复盘列"的真实场景；只看近期 lookback 天，
    避免远处空洞日把 buffer 起点拉远导致窗口膨胀。只读 parquet，开销极小。
    """
    import pandas as pd
    need = []
    recent = all_td[-lookback:]
    for pos, d in enumerate(recent):
        dist_from_end = len(recent) - 1 - pos
        if dist_from_end < 10:      # 未来 10 日还没走完，fwd 本就应为 null
            continue
        p = C7_POOL_DIR / f"trade_date={d}" / "part.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["fwd_max_10d"])
        except Exception:
            continue
        if len(df) and df["fwd_max_10d"].notna().sum() == 0:
            need.append(d)
    return need


def rebuild_c7_pool(force: bool = False, limit_days: int = 200,
                    buffer_days: int = 70, force_recent_days: int = 0,
                    dry_run: bool = False) -> dict:
    """增量构建 C6/C7 策略池分区。对照 rebuild_limit_up_streak 的接口语义。

    增量口径（修复 2026-11-20 线上 129s 问题）：
      - 默认只补"未缓存"的缺失日 + "fwd 待补全"的近期日；不无脑强制重算最近 N 天。
      - buffer_days=70 覆盖 ma60(60) + lu20 + ret5 预热；streak 连续段(实测 max=4)
        在 70 日窗口内起点必然可见，判定与更长 buffer 一致（见 A/B 验证）。
      - 写盘统一 matched_concepts=list<string>，空分区补齐完整 schema，
        消除 archive_month 月合并的 union_by_name 类型冲突。

    返回统计 dict：{days, rows, source_counts, date_min, date_max, seconds,
    skipped, target_dates}。只写目标日期分区；日分区零删除。
    """
    t0 = time.time()
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    all_td = _list_quote_dates()
    if not all_td:
        logger.info("[c7_pool] 无交易日，跳过")
        return dict(days=0, rows=0, source_counts={}, seconds=0)

    if force:
        target_dates = list(all_td[-limit_days:])
    else:
        existing = set()
        if C7_POOL_DIR.exists():
            existing = {p.name.split("=", 1)[1] for p in C7_POOL_DIR.iterdir()
                        if p.is_dir() and p.name.startswith("trade_date=")}
        missing = [d for d in all_td[-limit_days:] if d not in existing]
        fwd_need = _fwd_incomplete_dates(all_td)
        target_dates = sorted(set(missing) | set(fwd_need))

    if force_recent_days > 0:
        target_dates = sorted(set(target_dates) | set(all_td[-force_recent_days:]))

    if not target_dates:
        logger.info("[c7_pool] 目标日期均已缓存且 fwd 完整，跳过（零写入）")
        return dict(days=0, rows=0, source_counts={}, seconds=round(time.time()-t0, 1),
                    skipped=True)

    if dry_run:
        return dict(days=len(target_dates), rows=0, source_counts={},
                    date_min=min(target_dates), date_max=max(target_dates),
                    seconds=round(time.time()-t0, 1), dry_run=True,
                    target_dates=target_dates)

    earliest = min(target_dates)
    idx = all_td.index(earliest)
    buf_start = max(0, idx - buffer_days)
    buffered = all_td[buf_start:]
    win_dates_sql = "(" + ",".join(f"'{d}'" for d in buffered) + ")"

    try:
        _TMP.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        con.execute(f"SET temp_directory='{_TMP.as_posix()}'")
        con.execute("SET threads=8")
        try:
            t1 = time.time()
            df = con.execute(_pool_sql(win_dates_sql)).df()
            logger.info("[c7_pool] 计算 %d 行耗时 %.1fs（buffer %d 日）",
                        len(df), time.time()-t1, len(buffered))
        finally:
            con.close()
    except Exception:
        logger.exception("[c7_pool] 计算失败")
        return dict(days=0, rows=0, source_counts={}, error=True,
                    seconds=round(time.time()-t0, 1))

    # 写盘类型统一：matched_concepts 一律 list<string>（消除 list/str 混合导致月合并失败）
    tset = set(target_dates)
    df = df[df["trade_date"].isin(tset)].copy()
    df["is_streak2"] = df["streak_days"].fillna(1) >= 2
    df["matched_concepts"] = df["matched_concepts"].map(
        lambda x: [str(s) for s in str(x).split(";") if s] if pd.notna(x) else [])

    schema = _template_schema()          # 可能为 None（极端：无任何正确分区）
    n_days = 0
    for d, g in df.groupby("trade_date", sort=False):
        out = C7_POOL_DIR / f"trade_date={d}"
        out.mkdir(parents=True, exist_ok=True)
        g = g[[c for c in schema.names if c in g.columns]] if schema else g
        if schema:
            pq.write_table(pa.Table.from_pandas(g, schema=schema, preserve_index=False),
                           out / "part.parquet")
        else:
            g.to_parquet(out / "part.parquet", index=False)
        n_days += 1

    # 目标日中池为空的也写空分区，且补齐完整列 schema（不再只写 trade_date/code）
    have = set(df["trade_date"].unique())
    for d in tset - have:
        out = C7_POOL_DIR / f"trade_date={d}"
        out.mkdir(parents=True, exist_ok=True)
        if schema:
            pq.write_table(pa.Table.from_pylist([], schema=schema), out / "part.parquet")
        else:
            pd.DataFrame({"trade_date": pd.Series([], dtype=str),
                          "code": pd.Series([], dtype=str)}).to_parquet(
                out / "part.parquet", index=False)
        n_days += 1

    src = df["source"].value_counts().to_dict() if len(df) else {}
    stat = dict(days=n_days, rows=len(df),
                source_counts={str(k): int(v) for k, v in src.items()},
                date_min=min(target_dates), date_max=max(target_dates),
                seconds=round(time.time()-t0, 1))
    logger.info("[c7_pool] 写入 %d 天 %d 行 来源=%s 耗时 %.1fs",
                n_days, len(df), stat["source_counts"], stat["seconds"])
    return stat
