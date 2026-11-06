"""stockDisplay CLI —— 供 AI / 脚本调用。

用法：
    python cli.py --help
    python cli.py stock kline 002693 --start 2024-08-01 --end 2024-10-31
    python cli.py concept top10 2024-10-09
    python cli.py --format table stock sectors 002693

输出：
    默认 JSON：{"ok": true, "data": [...], "meta": {...}}
    可选 --format table / csv
    可选 --output file.json（推荐 AI 用，避免 stdout 截断）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 项目根目录加入 path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd


# ══════════════════════════════════════════════════════════════
# 输出层
# ══════════════════════════════════════════════════════════════

def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON 友好 records（NaN/NaT → None，日期 → str）。"""
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def _to_records(obj) -> list[dict] | dict:
    """任意返回（DataFrame / list / dict）→ JSON 友好。"""
    if isinstance(obj, pd.DataFrame):
        return _df_to_records(obj)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return obj
    return obj


def _output(data, fmt: str = "json", output_path: str | None = None,
            meta: dict | None = None, err: bool = False):
    """统一输出。"""
    payload = {
        "ok": not err,
        "data": data,
        "meta": {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **(meta or {}),
        },
    }

    if err:
        payload = {
            "ok": False,
            "error": data,
            "meta": payload["meta"],
        }

    # 写文件
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            p.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
        elif fmt == "csv":
            if isinstance(data, dict):
                print("# 批量查询（dict）不支持 csv 格式，请用 --format json", file=sys.stderr)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif isinstance(data, list) and data:
                pd.DataFrame(data).to_csv(sys.stdout, index=False)
            else:
                print("")
        elif fmt == "table":
            if isinstance(data, dict):
                print("# 批量查询（dict）不支持 table 格式，请用 --format json", file=sys.stderr)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_to_table(data))
        return

    # 写 stdout
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif fmt == "csv":
        if isinstance(data, list) and data:
            pd.DataFrame(data).to_csv(sys.stdout, index=False)
        else:
            print("")
    elif fmt == "table":
        print(_to_table(data))


def _to_table(data) -> str:
    """简单 ASCII 表格。"""
    if not isinstance(data, list) or not data:
        return "(empty)"
    df = pd.DataFrame(data)
    # 截断过宽列
    df = df.copy()
    for c in df.columns:
        df[c] = df[c].apply(
            lambda v: (str(v)[:30] + "…") if isinstance(v, str) and len(str(v)) > 30 else v
        )
    return df.to_string(index=False)


def _error(e: Exception, hint: str = ""):
    """统一错误输出。"""
    _output(
        {"type": type(e).__name__, "message": str(e), "hint": hint},
        err=True,
    )
    sys.exit(1)


# ══════════════════════════════════════════════════════════════
# 命令实现
# ══════════════════════════════════════════════════════════════
def _parse_codes(s: str) -> list[str]:
    """解析逗号分隔的 code 列表，自动 zfill(6)。"""
    if not s:
        return []
    return [c.strip().zfill(6) for c in str(s).split(",") if c.strip()]


def _maybe_batch(codes: list[str], fetch_fn):
    """单 code → 直接返回；多 code → 返回 {code: result}。

    fetch_fn(code) → data
    """
    if not codes:
        return []
    if len(codes) == 1:
        return fetch_fn(codes[0])
    out = {}
    for c in codes:
        try:
            out[c] = fetch_fn(c)
        except Exception as e:
            out[c] = {"_error": f"{type(e).__name__}: {e}"}
    return out
def _trim(data, limit, columns):
    # 批量结果（dict of list）
    if isinstance(data, dict):
        return {k: _trim(v, limit, columns) for k, v in data.items()}
    if not isinstance(data, list):
        return data
    if columns:
        cols = [c.strip() for c in columns.split(",")]
        data = [{k: r.get(k) for k in cols} for r in data]
    if limit and limit > 0:
        data = data[:limit]
    return data
# ─── 交集榜可排序字段（必须实际存在于 picks）───
PICKS_SORTABLE_FIELDS = {
    "code", "name", "match_count", "matched_concepts",
    "importance_index", "importance_pct", "rank_in_picks", "total_in_picks",
    "close", "pct_change", "turnover_rate", "hot_rank", "amount",
    "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
    "huge_order_net_norm", "big_order_net_norm",
    "medium_order_net_norm", "small_order_net_norm",
    "fwd_5d", "fwd_10d", "fwd_20d", "fwd_30d",
    "fwd_max_5d", "fwd_max_10d", "fwd_max_20d", "fwd_max_30d",
}


def _sort_picks(picks, sort_by, sort_dir="desc"):
    """按指定字段排序，null 永远排最后。"""
    from functools import cmp_to_key
    if not picks or not sort_by:
        return picks

    def _cmp(a, b):
        va = a.get(sort_by)
        vb = b.get(sort_by)
        an = va is None
        bn = vb is None
        if an and bn:
            return 0
        if an:
            return 1
        if bn:
            return -1
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            cmp = va - vb
        else:
            sa, sb = str(va), str(vb)
            cmp = 1 if sa > sb else (-1 if sa < sb else 0)
        return -cmp if sort_dir == "desc" else cmp

    return sorted(picks, key=cmp_to_key(_cmp))

def cmd_list_codes(args):
    from queries import list_codes
    df = list_codes()
    if args.search:
        df = df[df["code"].str.contains(args.search)
                | df["name"].str.contains(args.search, na=False)]
    data = _df_to_records(df)
    return _trim(data, args.limit, args.columns)


def cmd_list_indicators(args):
    from queries import list_available_indicators
    items = list_available_indicators()
    if args.type:
        items = [x for x in items if x.get("type") == args.type]
    if args.scope:
        items = [x for x in items if (x.get("scope") or "all") == args.scope]
    return _trim(items, args.limit, None)


def cmd_stock_kline(args):
    from queries import get_stock_chart
    codes = _parse_codes(args.code)
    inds = [x.strip() for x in (args.indicators or "").split(",") if x.strip()] or None

    def _fetch(c):
        df = get_stock_chart(c, args.start, args.end, indicators=inds)
        return _trim(_df_to_records(df), args.limit, args.columns)

    return _maybe_batch(codes, _fetch)


def cmd_stock_sectors(args):
    from queries import get_stock_sectors
    codes = _parse_codes(args.code)
    return _maybe_batch(codes, lambda c: get_stock_sectors(c))


def cmd_stock_industry(args):
    from queries import get_stock_industry_chart
    codes = _parse_codes(args.code)
    inds = [x.strip() for x in (args.indicators or "").split(",") if x.strip()] or None

    def _fetch(c):
        df = get_stock_industry_chart(c, args.start, args.end, indicators=inds)
        return {
            "industry_name": df.attrs.get("industry_name", ""),
            "industry_code": df.attrs.get("industry_code", ""),
            "rows": _trim(_df_to_records(df), args.limit, args.columns),
        }

    return _maybe_batch(codes, _fetch)

def cmd_stock_sector_chart(args):
    from queries import get_sector_chart
    inds = [x.strip() for x in (args.indicators or "").split(",") if x.strip()] or None
    key_col = "code" if (args.key.isdigit() and len(args.key) == 6) else "name"
    df = get_sector_chart(
        sector_key=args.key, start=args.start, end=args.end,
        kind=args.kind, indicators=inds, key_col=key_col)
    data = _df_to_records(df)
    return _trim(data, args.limit, args.columns)


def cmd_stock_amo(args):
    from queries import get_amo_chart
    codes = _parse_codes(args.code)

    def _fetch(c):
        r = get_amo_chart(c, args.start, args.end)
        return _trim(r.get("rows", []), args.limit, args.columns)

    return _maybe_batch(codes, _fetch)


def cmd_concept_top10(args):
    """概念三榜 + 交集选股。

    --only picks       只返回交集榜
    --only lists       只返回三榜
    --only all         全部（默认）

    --limit N          排序后取前 N 条；--limit 0 或不传 → 全量
    --sort-by FIELD    排序字段（默认 importance_index）
    --sort-dir asc/desc 排序方向（默认 desc）
    --columns a,b,c    字段裁剪（可叠加 --limit）
    """
    from queries import (
        get_concept_date_top10,
        _get_tops_and_scores,
        _build_intersection_picks,
    )

    only = args.only or "all"
    sort_by = getattr(args, "sort_by", None) or "importance_index"
    sort_dir = getattr(args, "sort_dir", None) or "desc"
    limit = args.limit or 0

    # ⭐ 校验排序字段
    if sort_by not in PICKS_SORTABLE_FIELDS:
        raise ValueError(
            f"不支持的排序字段: {sort_by}\n"
            f"可选: {sorted(PICKS_SORTABLE_FIELDS)}"
        )

    # 三榜基础数据（lru_cache，快）
    r = get_concept_date_top10(args.date)

    if only == "lists":
        return {
            k: r.get(k)
            for k in ("market_total_amount", "market_avg_5d",
                      "market_avg_20d", "top_amount",
                      "top_pct", "top_limit_up")
        }

    # ⭐ 判断是否需要全量重算：
    #    - 需要超过 40 条（limit > 40 或 limit=0 全量）
    #    - 或按非 importance_index 字段排序
    need_full = (
        limit <= 0 or limit > 40 or sort_by != "importance_index"
    )

    if need_full:
        # 全量重算（实时，约 0.3~1s）
        mt, ta, tp, tl, cs = _get_tops_and_scores(args.date)
        picks = _build_intersection_picks(
            ta, tp, tl, args.date, mt, cs, topn=None)
    else:
        # 复用缓存（parquet，~毫秒级）
        picks = r.get("intersection_picks") or []

    # 排序（--sort-by / --sort-dir）
    picks = _sort_picks(picks, sort_by, sort_dir)

    # 截断（--limit）
    if limit > 0:
        picks = picks[:limit]

    # 字段裁剪（--columns）
    if args.columns:
        cols = [c.strip() for c in args.columns.split(",") if c.strip()]
        picks = [{k: p.get(k) for k in cols} for p in picks]

    if only == "picks":
        return {"intersection_picks": picks}

    # only == all
    result = dict(r)
    result["intersection_picks"] = picks
    return result

def cmd_concept_ladder(args):
    """涨停天梯。

    --limit:   作用于 stocks 的条数（如 --limit 20）
    --columns: 作用于 stocks 的字段（如 --columns code,name,importance_index）
    """
    from queries import get_limit_up_ladder
    r = get_limit_up_ladder(args.date, min_days=args.min_days)

    stocks = r.get("stocks") or []
    if args.limit and args.limit > 0:
        stocks = stocks[:args.limit]
    if args.columns:
        cols = [c.strip() for c in args.columns.split(",") if c.strip()]
        stocks = [{k: s.get(k) for k in cols} for s in stocks]

    return {
        "trade_date": r.get("trade_date"),
        "cached": r.get("cached", True),
        "stocks": stocks,
    }


def cmd_concept_limitup(args):
    from queries import get_concept_limit_up_stocks
    r = get_concept_limit_up_stocks(
        args.concept_code, args.date, window_days=args.window)
    return r

def cmd_concept_colors(args):
    from queries import get_concept_colors_summary
    return get_concept_colors_summary(args.date)

def cmd_concept_gld(args):
    from core.loaders import get_concept_gld
    codes = _parse_codes(args.code)
    concepts = [c.strip() for c in args.concept.split(",") if c.strip()]

    if len(codes) == 1 and len(concepts) == 1:
        return {"code": codes[0], "concept": concepts[0],
                "gld": get_concept_gld(codes[0], concepts[0], default=0.0)}

    # 批量：{code: {concept: gld}}
    out = {}
    for c in codes:
        out[c] = {con: get_concept_gld(c, con, default=0.0) for con in concepts}
    return out


def cmd_concept_ztnr(args):
    from core.loaders import get_concept_ztnr
    codes = _parse_codes(args.code)
    concepts = [c.strip() for c in args.concept.split(",") if c.strip()]

    if len(codes) == 1 and len(concepts) == 1:
        return {"code": codes[0], "concept": concepts[0],
                "ztnr": get_concept_ztnr(codes[0], concepts[0], default="")}

    out = {}
    for c in codes:
        out[c] = {con: get_concept_ztnr(c, con, default="") for con in concepts}
    return out


def cmd_trading_days(args):
    from queries import get_trading_days_around
    return get_trading_days_around(args.date, args.before, args.after)


def cmd_trading_neighbor(args):
    from queries import get_trading_day_neighbor
    return get_trading_day_neighbor(args.date, args.direction)


# ══════════════════════════════════════════════════════════════
# argparse
# ══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    # ── 全局参数 ──
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["json", "table", "csv"],
                        default="json", help="输出格式（默认 json）")
    common.add_argument("--output", default=None,
                        help="输出到文件（推荐 AI 用）")
    common.add_argument("--limit", type=int, default=None,
                        help="限制返回条数")
    common.add_argument("--columns", default=None,
                        help="只返回指定列（逗号分隔）")

    # ── 主解析器 ──
    p = argparse.ArgumentParser(
        prog="stockcli",
        description="stockDisplay CLI · 供 AI / 脚本调用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 股票列表
    python cli.py list codes --limit 20
    python cli.py list codes --search 平安

    # 指标列表
    python cli.py list indicators --type sql

    # 个股 K 线
    python cli.py stock kline 002693 --start 2024-08-01 --end 2024-10-31

    # 个股所属概念/行业
    python cli.py stock sectors 002693

    # 概念三榜 + 交集选股
    python cli.py concept top10 2024-10-09 --output /tmp/top10.json

    # 涨停天梯
    python cli.py concept ladder 2024-10-09 --min-days 2

    # 概念下涨停股
    python cli.py concept limitup 880xxx 2024-10-09 --window 10
    # 概念颜色分类
    python cli.py concept colors 2024-10-09
    # gld / ztnr
    python cli.py concept gld 002693 芯片
    python cli.py concept ztnr 002693 芯片

    # 交易日
    python cli.py trading days 2024-10-09 --before 5 --after 5
    python cli.py trading neighbor 2024-10-09 --direction prev
""",
    )
    p.add_argument("--format", choices=["json", "table", "csv"],
                   default="json", help="全局输出格式")
    p.add_argument("--output", default=None, help="全局输出文件")
    # 在 build_parser 中添加
    p.add_argument("--read-only", action="store_true", 
                help="只读模式，禁止任何写操作（适合 AI 调用）")

    sub = p.add_subparsers(dest="group", required=True)

    # ── list 组 ──
    g = sub.add_parser("list", help="列表类")
    sub2 = g.add_subparsers(dest="cmd", required=True)

    p_codes = sub2.add_parser("codes", parents=[common], help="股票列表")
    p_codes.add_argument("--search", default=None, help="按代码/名称搜索")
    p_codes.set_defaults(func=cmd_list_codes)

    p_ind = sub2.add_parser("indicators", parents=[common], help="指标列表")
    p_ind.add_argument("--type", choices=["sql", "python"], default=None)
    p_ind.add_argument("--scope", choices=["all", "stock", "industry"], default=None)
    p_ind.set_defaults(func=cmd_list_indicators)

    # ── stock 组 ──
    g = sub.add_parser("stock", help="个股 / 行业")
    sub2 = g.add_subparsers(dest="cmd", required=True)

    p_k = sub2.add_parser("kline", parents=[common], help="个股 K 线")
    p_k.add_argument("code", help="股票代码，可逗号分隔（如 002693,000001）")
    p_k.add_argument("--start", required=True)
    p_k.add_argument("--end", required=True)
    p_k.add_argument("--indicators", default="", help="逗号分隔的指标名")
    p_k.set_defaults(func=cmd_stock_kline)

    p_s = sub2.add_parser("sectors", parents=[common],
                          help="个股所属行业/概念")
    p_s.add_argument("code")
    p_s.set_defaults(func=cmd_stock_sectors)

    p_ind_chart = sub2.add_parser("industry", parents=[common],
                                   help="个股所属行业的 K 线")
    p_ind_chart.add_argument("code")
    p_ind_chart.add_argument("--start", required=True)
    p_ind_chart.add_argument("--end", required=True)
    p_ind_chart.add_argument("--indicators", default="")
    p_ind_chart.set_defaults(func=cmd_stock_industry)

    p_sec = sub2.add_parser("sector", parents=[common],
                             help="行业/概念 K 线（按 code 或 name）")
    p_sec.add_argument("--kind", choices=["industry", "concept"],
                       required=True)
    p_sec.add_argument("--key", required=True, help="code 或 name")
    p_sec.add_argument("--start", required=True)
    p_sec.add_argument("--end", required=True)
    p_sec.add_argument("--indicators", default="")
    p_sec.set_defaults(func=cmd_stock_sector_chart)

    p_amo = sub2.add_parser("amo", parents=[common],
                             help="AMO 买卖结构")
    p_amo.add_argument("code")
    p_amo.add_argument("--start", required=True)
    p_amo.add_argument("--end", required=True)
    p_amo.set_defaults(func=cmd_stock_amo)

    # ── concept 组 ──
    g = sub.add_parser("concept", help="概念")
    sub2 = g.add_subparsers(dest="cmd", required=True)

    p_t = sub2.add_parser("top10", parents=[common],
                          help="概念三榜 + 交集选股")
    p_t.add_argument("date", help="YYYY-MM-DD")
    p_t.add_argument("--only", choices=["picks", "lists", "all"],
                     default="all",
                     help="只返回 picks（交集）/ lists（三榜）/ all（默认）")
    p_t.add_argument("--sort-by", default="importance_index",
                     help=f"排序字段（默认 importance_index），可选: "
                          f"{', '.join(sorted(PICKS_SORTABLE_FIELDS))}")
    p_t.add_argument("--sort-dir", choices=["asc", "desc"], default="desc",
                     help="排序方向（默认 desc）")
    p_t.set_defaults(func=cmd_concept_top10)

    p_l = sub2.add_parser("ladder", parents=[common], help="涨停天梯")
    p_l.add_argument("date")
    p_l.add_argument("--min-days", type=int, default=2)
    p_l.set_defaults(func=cmd_concept_ladder)

    p_lu = sub2.add_parser("limitup", parents=[common],
                            help="某概念下涨停股")
    p_lu.add_argument("concept_code")
    p_lu.add_argument("date")
    p_lu.add_argument("--window", type=int, default=10)
    p_lu.set_defaults(func=cmd_concept_limitup)

    p_col = sub2.add_parser("colors", parents=[common],
                             help="当日概念颜色分类（红/黄/蓝/白）")
    p_col.add_argument("date", help="YYYY-MM-DD")
    p_col.set_defaults(func=cmd_concept_colors)

    p_gld = sub2.add_parser("gld", parents=[common],
                             help="某股票某概念的关联度")
    p_gld.add_argument("code", help="股票代码，可逗号分隔")
    p_gld.add_argument("concept", help="概念名，可逗号分隔")
    p_gld.set_defaults(func=cmd_concept_gld)

    p_z = sub2.add_parser("ztnr", parents=[common],
                           help="某股票某概念的入选原因")
    p_z.add_argument("code")
    p_z.add_argument("concept")
    p_z.set_defaults(func=cmd_concept_ztnr)

    # ── trading 组 ──
    g = sub.add_parser("trading", help="交易日")
    sub2 = g.add_subparsers(dest="cmd", required=True)

    p_td = sub2.add_parser("days", parents=[common], help="前后 N 交易日")
    p_td.add_argument("date")
    p_td.add_argument("--before", type=int, default=200)
    p_td.add_argument("--after", type=int, default=100)
    p_td.set_defaults(func=cmd_trading_days)

    p_tn = sub2.add_parser("neighbor", parents=[common],
                            help="相邻交易日")
    p_tn.add_argument("date")
    p_tn.add_argument("--direction", choices=["prev", "next"],
                      required=True)
    p_tn.set_defaults(func=cmd_trading_neighbor)

    return p


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════

def main():
    p = build_parser()
    args = p.parse_args()
    # 设置全局只读标志
    import builtins
    builtins.STOCK_CLI_READ_ONLY = args.read_only
    # 在 cli.py 的 main() 中
    import json
    from datetime import datetime

    audit_entry = {
        "ts": datetime.now().isoformat(),
        "command": " ".join(sys.argv),
        "read_only": getattr(builtins, "STOCK_CLI_READ_ONLY", False)
    }
    with open("logs/cli_audit.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")



    t0 = time.time()
    try:
        result = args.func(args)
    except Exception as e:
        _error(e)
        return

    # 处理 handler 返回 (data, meta) 或 data
    if isinstance(result, tuple) and len(result) == 2:
        data, meta_extra = result
    else:
        data, meta_extra = result, {}

    meta = {
        **meta_extra,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }
    if isinstance(data, list):
        meta["count"] = len(data)

    _output(data, fmt=args.format, output_path=args.output, meta=meta)


if __name__ == "__main__":
    main()