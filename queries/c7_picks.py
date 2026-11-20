# -*- coding: utf-8 -*-
"""C7 策略选股（供 concept.html 「概念交集选股」列表替换使用）。

口径与研究阶段完全一致（当日可知、无未来信息）：
  C7 九条件（全部 T 日收盘后可知）：
    1) lu_cnt_20d       >= 1        近20日至少1次涨停（情绪/主力参与）
    2) main_net_norm    >  0        主力净额/成交额为正（资金聚集）
    3) vol20            >= 3.5238   20日波动率高分位（train q70）
    4) turnover_rate    >= 3.7      当日换手率（train q60）
    5) lu_gap           >= -3       距涨停不大于3个点（逼近日高/强势）
    6) close_hh20_ratio >= 0.95     收盘价在近20日高点95%以上（趋势向上）
    7) ret_5d           >= 4.8679   近5日涨幅（短期动量，train q80）
    8) ma5 > ma20 > ma60            多头排列
    9) n_leader_concepts >= 1       命中至少1个龙头概念（score>=0.8）

  规则1.1 排序：综合分 = z(vol20)+z(ret_5d)+z(turnover_rate)，全部↑，
              z 用 train 全样本基准常数（一次确定、固定后不随数据漂移）；
              禁用 main_net_norm 排序（双期反向，见操盘策略报告）。
  规则5.1 标注：streak = 截至当日连续满足 C7 的交易日数（grp=di-rn）；
              streak>=2 为「连续在池」，列表与详情高亮标注。

数据接入：从项目原始 parquet 实时重算（不依赖任何研究中间文件、不写任何文件）。
缓存策略：稳定日期（距今>=45交易日）用 lru_cache 内存缓存；近期日期每次重算，
          保证盘中数据更新可见；服务重启自然失效。纯内存，零落盘。
"""
from __future__ import annotations

import logging
from functools import lru_cache

import duckdb

from config import COMPACT_DIR

logger = logging.getLogger(__name__)

# ── C7 阈值（train 分位，与 c7_stock_screen.py / 研究报告一致）──
THR = dict(
    lu_cnt_20d=1.0,
    main_net_norm=0.0,
    vol20=3.5238,
    turnover_rate=3.7,
    lu_gap=-3.0,
    close_hh20_ratio=0.95,
    ret_5d=4.8679,
    n_leader_concepts=1.0,
)

# ── 规则1.1 综合分的 train 基准常数（train 有限值样本，见 ref_probe.py）──
# vol: mean=3.010096 std=1.639706 / ret5: 0.848288, 7.840214 / tor: 4.794629, 5.705012
ZREF = dict(
    vol_m=3.010096, vol_s=1.639706,
    ret_m=0.848288, ret_s=7.840214,
    tor_m=4.794629, tor_s=5.705012,
)

_QG = f"read_parquet('{(COMPACT_DIR / 'quote' / '*.parquet').as_posix()}', union_by_name=true)"
_DG = f"read_parquet('{(COMPACT_DIR / 'daily_pct' / '*.parquet').as_posix()}', union_by_name=true)"
_CG = f"read_parquet('{(COMPACT_DIR / 'concept_date' / '*.parquet').as_posix()}', union_by_name=true)"
_BARS = f"read_parquet('{(COMPACT_DIR / '_tops_cache' / 'v1' / 'bars.parquet').as_posix()}')"
_META = f"read_parquet('{(COMPACT_DIR / '_tops_cache' / 'v1' / 'meta.parquet').as_posix()}')"


def _c7_cond(alias: str = "") -> str:
    """C7 九条件 SQL 片段。alias 为空则直接用列名。"""
    p = f"{alias}." if alias else ""
    t = THR
    return f"""{p}lu_cnt_20d >= {t['lu_cnt_20d']}
      AND {p}main_net_norm > {t['main_net_norm']}
      AND {p}vol20 >= {t['vol20']}
      AND {p}turnover_rate >= {t['turnover_rate']}
      AND {p}lu_gap >= {t['lu_gap']}
      AND {p}close_hh20_ratio >= {t['close_hh20_ratio']}
      AND {p}ret_5d >= {t['ret_5d']}
      AND {p}ma5 > {p}ma20 AND {p}ma20 > {p}ma60
      AND {p}n_leader_concepts >= {t['n_leader_concepts']}"""


def _is_stable_date(trade_date: str, con: duckdb.DuckDBPyConnection) -> bool:
    """稳定日 = 距今 >= 45 个交易日的历史日期（历史数据不再变动，可安全缓存）。"""
    try:
        row = con.execute(
            f"SELECT max(trade_date) AS m, count(DISTINCT trade_date) AS nd FROM {_QG}"
        ).fetchone()
        if not row or not row[0]:
            return False
        # 用交易日序号差判断，避免自然日口径漂移
        r2 = con.execute(f"""
            WITH d AS (SELECT DISTINCT CAST(trade_date AS VARCHAR) AS d FROM {_QG}),
            x AS (SELECT d, row_number() OVER (ORDER BY d) di FROM d)
            SELECT max(di) - (SELECT di FROM x WHERE d = substr(?,1,10)) FROM x
        """, [trade_date]).fetchone()
        return bool(r2 and r2[0] is not None and r2[0] >= 45)
    except Exception:
        return False


# 计算窗口：C7 最长依赖 ma60（60 交易日），留足余量取 125 日；
# 避免对全量 669 日 / 360 万行做窗口计算（原版单步 ~2.1s，实测 2.5x 提速且结果完全一致）。
_LOOKBACK = 125
_FWD_MAX_N = (5, 10)   # 复盘列：未来 N 日最高涨幅（与研究 build_matrix.py 口径一致）


def _compute(trade_date: str) -> list[dict]:
    """从项目原始 parquet 实时计算某日 C7 池（含 streak、综合分与复盘用 fwd_max 列）。

    优化：先定目标日在交易日历中的序号，仅取前 _LOOKBACK 个交易日的数据算窗口，
    而非全量历史；结果与原全量版逐股一致（A/B 实测 4 日期 100% 相同）。
    """
    con = duckdb.connect()
    con.execute("SET threads=8")
    try:
        z = ZREF
        # 目标日序号 → 限定窗口起点的日期集合
        tdi = con.execute(f"""
            WITH d AS (SELECT DISTINCT substr(CAST(trade_date AS VARCHAR),1,10) d FROM {_QG}),
            x AS (SELECT d, row_number() OVER (ORDER BY d) di FROM d)
            SELECT di FROM x WHERE d = substr(?,1,10)
        """, [trade_date]).fetchone()
        if tdi is None:
            return []
        lo = max(1, int(tdi[0]) - _LOOKBACK)
        win_dates = f"""(
            SELECT d FROM (
              SELECT DISTINCT substr(CAST(trade_date AS VARCHAR),1,10) d FROM {_QG}
              ORDER BY d
            ) LIMIT {_LOOKBACK} OFFSET {lo}
        )"""
        sql = f"""
        WITH
        qx AS (
          SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code, name, close, high,
                 amount, turnover_rate, pct_change, huge_order_net, big_order_net
          FROM {_QG}
          WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates}
        ),
        dx AS (
          SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code,
                 CASE WHEN isfinite(pct_change) THEN pct_change END AS pct_change
          FROM {_DG}
          WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates}
        ),
        cal AS (SELECT d, row_number() OVER (ORDER BY d) AS di
                FROM (SELECT DISTINCT d FROM qx)),
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
          WHERE substr(CAST(trade_date AS VARCHAR),1,10) IN {win_dates}
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
            st.close, st.amount,
            st.lu_cnt_20d,
            (coalesce(st.huge_order_net,0)+coalesce(st.big_order_net,0))/NULLIF(st.amount,0) AS main_net_norm,
            st.vol20, st.turnover_rate,
            st.daily_pct - st.lu_thr AS lu_gap,
            st.close/NULLIF(st.hh20,0) AS close_hh20_ratio,
            (st.close/NULLIF(st.close_5d_ago,0)-1)*100 AS ret_5d,
            st.ma5, st.ma20, st.ma60,
            coalesce(ld.n_leader_concepts,0) AS n_leader_concepts
          FROM st LEFT JOIN lead ld ON st.d=ld.d AND st.code=ld.code
        ),
        flag AS (
          SELECT *, CAST(({_c7_cond()}) AS INT) AS c7 FROM feat
        ),
        run AS (
          SELECT *, di - row_number() OVER (PARTITION BY code ORDER BY di) AS grp
          FROM flag WHERE c7=1
        ),
        streak AS (
          SELECT *, row_number() OVER (PARTITION BY code, grp ORDER BY di) AS streak_days
          FROM run
        ),
        concepts AS (
          SELECT substr(CAST(trade_date AS VARCHAR),1,10) AS d, code,
                 string_agg(DISTINCT t.x, ';' ORDER BY t.x) AS cfs,
                 count(DISTINCT t.x) AS n_cfs
          FROM {_CG}, unnest(string_split(concepts,';')) AS t(x)
          WHERE substr(CAST(trade_date AS VARCHAR),1,10) = substr(?,1,10)
          GROUP BY substr(CAST(trade_date AS VARCHAR),1,10), code
        ),
        fwd_dates AS (
          SELECT d FROM (
            SELECT DISTINCT substr(CAST(trade_date AS VARCHAR),1,10) d FROM {_QG}
            WHERE substr(CAST(trade_date AS VARCHAR),1,10) > substr(?,1,10)
            ORDER BY d LIMIT 10
          )
        ),
        fwd AS (
          SELECT q.code,
                 max(CASE WHEN f.rn<=5  THEN q.high END) AS hi5,
                 max(CASE WHEN f.rn<=10 THEN q.high END) AS hi10,
                 count(CASE WHEN f.rn<=5  THEN 1 END) AS n5,
                 count(CASE WHEN f.rn<=10 THEN 1 END) AS n10
          FROM (SELECT d, row_number() OVER (ORDER BY d) rn FROM fwd_dates) f
          JOIN {_QG} q ON substr(CAST(q.trade_date AS VARCHAR),1,10)=f.d
          GROUP BY q.code
        )
        SELECT s.trade_date, s.code, s.name, s.close, s.pct_change, s.turnover_rate,
               s.vol20, s.ret_5d, s.lu_cnt_20d, s.main_net_norm, s.n_leader_concepts,
               s.lu_gap, s.close_hh20_ratio, s.ma5, s.ma20, s.ma60, s.streak_days,
               coalesce(c.cfs,'') AS matched_concepts, coalesce(c.n_cfs,0) AS n_concepts,
               CASE WHEN fw.n5=5  THEN (fw.hi5 /NULLIF(s.close,0)-1)*100 END AS fwd_max_5d,
               CASE WHEN fw.n10=10 THEN (fw.hi10/NULLIF(s.close,0)-1)*100 END AS fwd_max_10d,
               (
                 (s.vol20 - {z['vol_m']}) / {z['vol_s']}
               ) + (
                 (s.ret_5d - {z['ret_m']}) / {z['ret_s']}
               ) + (
                 (s.turnover_rate - {z['tor_m']}) / {z['tor_s']}
               ) AS c7_score
        FROM streak s
        LEFT JOIN concepts c ON c.d=s.trade_date AND c.code=s.code
        LEFT JOIN fwd fw ON fw.code=s.code
        WHERE s.trade_date = substr(?,1,10)
        ORDER BY c7_score DESC
        """
        df = con.execute(sql, [trade_date, trade_date, trade_date]).df()

        out = []
        for r in df.itertuples(index=False):
            out.append({
                "code": str(r.code),
                "name": None if r.name is None else str(r.name),
                "trade_date": str(r.trade_date),
                "close": None if r.close is None else round(float(r.close), 3),
                "pct_change": None if r.pct_change is None else round(float(r.pct_change), 3),
                "turnover_rate": None if r.turnover_rate is None else round(float(r.turnover_rate), 3),
                "vol20": None if r.vol20 is None else round(float(r.vol20), 4),
                "ret_5d": None if r.ret_5d is None else round(float(r.ret_5d), 4),
                "lu_cnt_20d": int(r.lu_cnt_20d) if r.lu_cnt_20d is not None else 0,
                "main_net_norm": None if r.main_net_norm is None else round(float(r.main_net_norm), 5),
                "n_leader_concepts": int(r.n_leader_concepts) if r.n_leader_concepts is not None else 0,
                "lu_gap": None if r.lu_gap is None else round(float(r.lu_gap), 3),
                "close_hh20_ratio": None if r.close_hh20_ratio is None else round(float(r.close_hh20_ratio), 4),
                "ma_bull": bool(r.ma5 > r.ma20 > r.ma60),
                "streak_days": int(r.streak_days) if r.streak_days is not None else 1,
                "is_streak2": bool(r.streak_days is not None and r.streak_days >= 2),
                "c7_score": None if r.c7_score is None else round(float(r.c7_score), 4),
                # 复盘列：未来 N 日最高涨幅%（研究 build_matrix.py 口径；不足 N 交易日为 null）
                "fwd_max_5d": None if r.fwd_max_5d is None else round(float(r.fwd_max_5d), 2),
                "fwd_max_10d": None if r.fwd_max_10d is None else round(float(r.fwd_max_10d), 2),
                "matched_concepts": (r.matched_concepts or "").split(";") if r.matched_concepts else [],
                "n_concepts": int(r.n_concepts) if r.n_concepts is not None else 0,
            })
        return out
    finally:
        con.close()


@lru_cache(maxsize=64)
def _compute_cached(trade_date: str) -> tuple:
    """稳定日缓存（返回 tuple 以便 lru_cache 存储不可变对象）。"""
    return tuple(_compute(trade_date))


def get_c7_picks(trade_date: str) -> dict:
    """对外接口：返回 {ok, data, meta} 风格的 C6/C7 合并策略池。

    数据源优先级：
      1) 本地化按日缓存 data/c7_picks/trade_date=.../part.parquet（后台一次性预计算，
         含 C7 严选 + C6 补充池，毫秒级查询，页面渲染零窗口运算）；
      2) 缓存未命中（如最新数据尚未预计算）→ 回退实时 _compute（仅 C7），保证不空。
    所有字段当日可知；fwd_max_* 为复盘列、不参与筛选。
    """
    from core.c7_pool_cache import load_c7_pool

    pool = load_c7_pool(trade_date)
    if pool is not None:
        n_c7 = sum(1 for p in pool if p.get("source") == "C7")
        n_c6 = sum(1 for p in pool if p.get("source") == "C6")
        n_streak = sum(1 for p in pool if p.get("is_streak2"))
        return {
            "ok": True,
            "data": {
                "trade_date": trade_date,
                "strategy": "C6+C7",
                "thresholds": THR,
                "sort_rule": "c7_score=z(vol20)+z(ret_5d)+z(turnover_rate) desc",
                "picks": pool,
            },
            "meta": {
                "n": len(pool), "n_c7": n_c7, "n_c6": n_c6, "n_streak2": n_streak,
                "stable": True, "cache": "local_parquet",
                "source": "prebuilt_local", "future_info": False,
            },
        }

    # 缓存未命中 → 实时回退（仅 C7，口径不变）
    con = duckdb.connect()
    try:
        stable = _is_stable_date(trade_date, con)
    finally:
        con.close()

    if stable:
        picks = list(_compute_cached(trade_date))
        cache_mode = "stable_lru"
    else:
        picks = _compute(trade_date)
        cache_mode = "fresh"
    for p in picks:
        p.setdefault("source", "C7")

    return {
        "ok": True,
        "data": {
            "trade_date": trade_date,
            "strategy": "C7",
            "thresholds": THR,
            "sort_rule": "c7_score=z(vol20)+z(ret_5d)+z(turnover_rate) desc",
            "picks": picks,
        },
        "meta": {
            "n": len(picks), "n_c7": len(picks), "n_c6": 0,
            "n_streak2": sum(1 for p in picks if p.get("is_streak2")),
            "stable": stable, "cache": cache_mode,
            "source": "project_parquet_realtime", "future_info": False,
        },
    }


def clear_cache():
    _compute_cached.cache_clear()
