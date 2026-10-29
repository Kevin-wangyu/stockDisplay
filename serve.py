"""stockDisplay 服务启动器：先跑增量 ETL，再启动 uvicorn。

用法
----
    python serve.py                         # ETL + 启动 (127.0.0.1:8001)
    python serve.py --skip-etl              # 只启动服务
    python serve.py --port 8080             # 指定端口
    python serve.py --host 0.0.0.0          # 局域网访问
    python serve.py --force-etl             # ETL 强制重跑
    python serve.py --etl-date 20260918     # ETL 只处理指定日期
    python serve.py --no-reload             # 关闭热重载（生产）
"""
from __future__ import annotations
import duckdb
from trading_calendar import trading_days
import argparse
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
import warnings
import logging
logger = logging.getLogger("serve")
warnings.filterwarnings("ignore")
sys.path.append(str(Path(__file__).parent))
from config import (
    DATA_DIR,          # <--- 加上这一行
    RAW_QUOTE_DIR, RAW_INDUSTRY_DIR, RAW_CONCEPT_DIR, RAW_STOCK_CONCEPT_DIR,
    QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR, RAW_AMO_DIR, AMO_DIR,
    MAP_FILE, MAP_RAW_FILE,CONCEPT_PICKS_DIR,
    setup_logging,
)
import re

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="stockDisplay 服务启动器（增量 ETL + Web）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host", default="127.0.0.1", help="监听地址")
    p.add_argument("--port", type=int, default=8001, help="监听端口")
    p.add_argument("--skip-etl", action="store_true", help="跳过增量 ETL")
    p.add_argument("--force-etl", action="store_true", help="ETL 强制重跑已存在分区")
    p.add_argument("--etl-date", default=None, help="ETL 只处理该日期（YYYYMMDD）")
    p.add_argument("--no-reload", action="store_true", default=True, help="关闭 uvicorn 热重载")
    return p.parse_args()

# ─────────────── 15:30 后自动更新依赖 ───────────────

def _has_quote_data(trade_date: date) -> bool:
    """quote 分区目录下是否已有该交易日的 parquet。"""
    p = QUOTE_DIR / f"trade_date={trade_date.isoformat()}"
    if not p.exists():
        return False
    return any(p.glob("*.parquet"))


def _update_cookie_in_config(new_value: str) -> None:
    """把 config.py 里 COOKIE = "..." 替换成新值。"""
    cfg_path = Path(__file__).parent / "config.py"
    text = cfg_path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'^COOKIE\s*=\s*(["\']).*?\1',
        f'COOKIE = "{new_value}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise RuntimeError("未能在 config.py 中找到 COOKIE 定义")
    cfg_path.write_text(new_text, encoding="utf-8")

def maybe_rebuild_month_archive() -> None:
    """每次启动检查：若 data 里有比 compact 更新的当月文件，重建当月 compact。"""
    from datetime import date
    from config import DATA_DIR
    COMPACT_ROOT = DATA_DIR.parent / "data_compact"

    today = date.today()
    cur_month = today.strftime("%Y-%m")

    try:
        from archive_month import archive_kind, KINDS
        import duckdb as _dd
        con_arc = _dd.connect()

        for kind, kdir in KINDS.items():
            if not kdir.exists():
                continue
            compact_file = COMPACT_ROOT / kind / f"{cur_month}.parquet"

            need_rebuild = False
            if not compact_file.exists():
                need_rebuild = True
            else:
                compact_mtime = compact_file.stat().st_mtime
                # data 下当月最新 part.parquet 的 mtime
                for p in kdir.iterdir():
                    if p.is_dir() and p.name.startswith(f"trade_date={cur_month}"):
                        part = p / "part.parquet"
                        if part.exists() and part.stat().st_mtime > compact_mtime:
                            need_rebuild = True
                            break

            if need_rebuild:
                logger.info(f"[serve] 重建 {kind} 当月 compact…")
                archive_kind(kind, kdir, con_arc,
                             rebuild_current=True, dry_run=False)
    except Exception:
        logging.getLogger(__name__).exception("[serve] 月合并更新失败，继续启动")

def _prev_trading_day(d: date) -> date | None:
    """严格小于 d 的最近交易日。"""
    days = trading_days(d - timedelta(days=20), d - timedelta(days=1))
    return days[-1] if days else None


def _is_trading_day(d: date) -> bool:
    return bool(trading_days(d, d))


def _prompt_cookie_and_fetch(target_date: date) -> None:
    """统一流程：提示输入 cookie → 写入 config → 重载 → 拉数据。"""
    print()
    print("=" * 60)
    print(f"[serve] 未检测到 {target_date} 的行情数据")
    print("[serve] 请登录行情网站 → F12 → Application → Cookies")
    print("[serve] 找到 ASPSessionID 并复制其值")
    print("=" * 60)
    try:
        val = input("[serve] 请输入 ASPSessionID（直接回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[serve] 输入被取消")
        sys.exit(1)
    if not val:
        print("[serve] 未输入 ASPSessionID，退出")
        sys.exit(1)

    try:
        _update_cookie_in_config(val)
    except Exception as e:
        print(f"[serve] 写入 config.py 失败: {e}")
        sys.exit(1)

    import importlib, config
    importlib.reload(config)
    sys.modules.pop("xdget_daily", None)
    print("[serve] 已更新 config.COOKIE 并重载 config")

    print(f"[serve] 开始拉取 {target_date} 数据 …")
    try:
        import xdget_daily
        xdget_daily.run(target_date)
        print(f"[serve] {target_date} 数据拉取完成")
    except Exception as e:
        print(f"[serve] {target_date} 数据拉取失败: {e}")
        print("[serve] 继续启动服务，请手动检查数据/日志")


def ensure_data_ready() -> None:
    """按时间窗口处理数据更新。

    ┌──────────────┬────────────────────────────────────────────┐
    │ < 09:15      │ 允许补"上一交易日"数据（需要 ASPSessionID）│
    │ 09:15~15:30  │ 拒绝补上一交易日数据；跳过当日数据          │
    │ >= 15:30     │ 更新"当日"数据（今日须是交易日）            │
    └──────────────┴────────────────────────────────────────────┘
    """
    now = datetime.now()
    now_t = now.time()
    today = now.date()

    T_0915 = datetime.strptime("09:15", "%H:%M").time()
    T_1530 = datetime.strptime("15:30", "%H:%M").time()

    # ── 窗口 1：9:15 前，可补上一交易日 ──
    if now_t < T_0915:
        prev_d = _prev_trading_day(today)
        if prev_d is None:
            print("[serve] 无法确定上一交易日，跳过")
            return
        if _has_quote_data(prev_d):
            print(f"[serve] 上一交易日 {prev_d} 已有数据，跳过 cookie 输入")
            return
        print(f"[serve] 检测到上一交易日 {prev_d} 无数据，开始补拉…")
        _prompt_cookie_and_fetch(prev_d)
        return

    # ── 窗口 2：9:15 ~ 15:30，盘中，拒绝补数据 ──
    if now_t < T_1530:
        prev_d = _prev_trading_day(today)
        if prev_d and not _has_quote_data(prev_d):
            print(f"[serve] ⚠ 上一交易日 {prev_d} 数据未更新，且已超过 09:15")
            print("[serve]   无法自动获取（行情源仅支持补拉至 09:15），"
                  "请手动获取后再运行 serve.py")
        else:
            logger.info("[serve] 盘中时段（09:15~15:30），跳过数据更新")
        return

    # ── 窗口 3：15:30 后，更新当日数据 ──
    if not _is_trading_day(today):
        print(f"[serve] 今日（{today}）非交易日，跳过当日数据更新")
        return
    if _has_quote_data(today):
        print(f"[serve] 今日（{today}）已有数据，跳过 cookie 输入")
        return
    print(f"[serve] 检测到今日（{today}）无数据，开始拉取…")
    _prompt_cookie_and_fetch(today)

def check_data_integrity() -> None:
    """检查数据的日期覆盖，对缺失的交易日 logger.warning。"""
    import duckdb
    from datetime import date
    from trading_calendar import trading_days
    import queries

    con = duckdb.connect()
    
    # 直接使用 queries 里组装好的 glob，concept_date 单独组装
    items = [
        ("quote", queries.QUOTE_GLOB),
        ("industry_flow", queries.INDUSTRY_GLOB),
        ("concept_flow", queries.CONCEPT_GLOB),
        ("stock_concepts", queries._get_globs(queries.CONCEPT_DATE_DIR, "concept_date")),
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
            # 兼容脏数据（比如纯年份整数）
            s = str(x).split(" ")[0].replace("-", "")
            if len(s) == 8:
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            return date.fromisoformat(str(x)[:10])

        min_d, max_d = _to_date(r[0]), _to_date(r[1])

        expected = set(trading_days(min_d, max_d))
        rows = con.execute(
            f"SELECT DISTINCT trade_date FROM read_parquet({glob_expr}, hive_partitioning=true, union_by_name=true)"
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
def main() -> None:
    args = parse_args()
    setup_logging()

    # ── 1. 按时间窗口更新数据 ──
    ensure_data_ready()

    # ── 2. 增量 ETL ──
    if args.skip_etl:
        logger.info("[serve] 已跳过增量 ETL")
    else:
        target = None
        if args.etl_date:
            try:
                target = datetime.strptime(args.etl_date, "%Y%m%d").date()
            except ValueError:
                print(f"[serve] --etl-date 格式错误: {args.etl_date}（应为 YYYYMMDD）")
                sys.exit(1)
        try:
            from etl import daily_update
            stats = daily_update.run(force=args.force_etl, target_date=target)
            # 简要状态输出（详细日志已进 etl.log）
            if isinstance(stats, dict) and "single" not in stats:
                p = sum(len(s.get("processed", [])) for s in stats.values()
                        if isinstance(s, dict) and "processed" in s)
                f = sum(len(s.get("failed", [])) for s in stats.values()
                        if isinstance(s, dict) and "failed" in s)
                logger.info(f"[serve] 增量 ETL 完成: 处理 {p} 个文件, 失败 {f}")
        except Exception as e:
            logger.info(f"[serve] 增量 ETL 失败: {e}")
            logger.info("[serve] 继续启动服务（请手动检查日志 logs/etl.log）")

    # ── 3. 数据完整性检查 ──
    try:
        check_data_integrity()
    except Exception:
        logger.exception("[serve] 数据完整性检查失败（不影响启动）")

    # 启动时只增量最近 3 天（< 5s）
    try:
        from queries import (
            rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
            _get_tops_and_scores, _build_intersection_picks, _save_picks_cache,
            _list_partition_dates,
        )
        n_ma = rebuild_market_amount()
        n_dp = rebuild_daily_pct(force_recent_days=3)
        n_st = rebuild_limit_up_streak(limit_days=3, force_recent_days=3)

        # ⭐ compact 重建必须在所有查询之前（否则 _get_tops_and_scores 查不到当天）
        maybe_rebuild_month_archive()

        # 补最新日期的 picks（此时 compact 已包含当天数据）
        all_td = _list_partition_dates(CONCEPT_DIR)
        for d in all_td[-1:]:
            p = CONCEPT_PICKS_DIR / f"trade_date={d}" / "part.parquet"
            if not p.exists():
                try:
                    mt, ta, tp, tl, _ = _get_tops_and_scores(d)
                    picks = _build_intersection_picks(ta, tp, tl, d, mt)
                    _save_picks_cache(d, picks)
                    logger.info(f"[serve] {d} picks 已生成")
                except Exception:
                    logging.getLogger(__name__).exception(
                        "[serve] %s picks 生成失败", d)# ETL + 增量完成后再重建月合并（保证 compact 覆盖最新数据）
    except Exception:
        logging.getLogger(__name__).exception("增量更新失败，继续启动")



    # ── 4. 启动 uvicorn ──
    import uvicorn
    url = f"http://{args.host}:{args.port}"
    print()
    print("=" * 60)
    print(f"  服务地址:   {url}")
    print(f"  前端页面:   {url}/")
    print(f"  概念页面:   {url}/concept.html")
    print(f"  API 文档:   {url}/docs")
    print(f"  ETL 日志:   logs/etl.log")
    print("=" * 60)
    print()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_excludes=[
            "data/*", "data_compact/*", "logs/*", "raw_data/*", "map/*",
            "*.parquet", "*.xlsx", "*.log",
        ],
    )


if __name__ == "__main__":
    main()