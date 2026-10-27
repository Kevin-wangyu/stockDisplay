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

import argparse
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
import warnings
import logging
logger = logging.getLogger("serve")
warnings.filterwarnings("ignore")
sys.path.append(str(Path(__file__).parent))
from config import setup_logging
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
    p.add_argument("--no-reload", action="store_true", help="关闭 uvicorn 热重载")
    return p.parse_args()

# ─────────────── 15:30 后自动更新依赖 ───────────────

def _latest_expected_trading_day(n) -> date | None:
    """返回最近一个交易日（今天若是交易日就是今天，否则回退）。"""
    from trading_calendar import trading_days
    today = date.today()
    days = trading_days(today - timedelta(days=20), today)
    if not days:
        return None
    return days[-n]


def _has_quote_data(trade_date: date) -> bool:
    """quote 分区目录下是否已有该交易日的 parquet。"""
    from config import QUOTE_DIR
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


def ensure_data_ready() -> None:
    """15:30 后：若最近交易日无 quote 数据，提示输入 ASPSessionID 并写入 config，然后拉数据。"""
    now = datetime.now()
    if now.time() > datetime.strptime("09:30", "%H:%M").time() and now.time() < datetime.strptime("15:30", "%H:%M").time():
        print("[serve] 当前时间在9:30 到 15:30之间，跳过当日数据更新")
        return
    latest = _latest_expected_trading_day(1)
    if now.time() < datetime.strptime("09:30", "%H:%M").time():
        latest = _latest_expected_trading_day(2)

    if latest is None:
        print("[serve] 无法从交易日历获取最近交易日，跳过自动更新")
        return

    if _has_quote_data(latest):
        print(f"[serve] 最近交易日 {latest} 已有数据，跳过 cookie 输入")
    else:
        print()
        print("=" * 60)
        print(f"[serve] 未检测到最近交易日 {latest} 的行情数据")
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

        # 让后续 import 的模块看到新 cookie
        import importlib, config
        importlib.reload(config)
        # 强制重新 import xdget_daily，避免它缓存旧 COOKIE
        sys.modules.pop("xdget_daily", None)
        print("[serve] 已更新 config.COOKIE 并重载 config")

    print("[serve] 开始自动更新当日数据 …")
    try:
        import xdget_daily
        xdget_daily.run(latest)
        print("[serve] 当日数据更新完成")
    except Exception as e:
        print(f"[serve] 当日数据更新失败: {e}")
        print("[serve] 继续启动服务，请手动检查数据/日志")

def check_data_integrity() -> None:
    """检查三类 Parquet 数据的日期覆盖，对缺失的交易日 logger.warning。"""
    import duckdb
    from datetime import date
    from config import QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, CONCEPT_DATE_DIR, AMO_DIR
    from trading_calendar import trading_days

    con = duckdb.connect()
    items = [
        ("quote", QUOTE_DIR),
        ("industry_flow", INDUSTRY_DIR),
        ("concept_flow", CONCEPT_DIR),
        ("stock_concepts", CONCEPT_DATE_DIR),
    ]

    for name, dir_ in items:
        glob = str(dir_ / "**" / "*.parquet")
        try:
            r = con.execute(
                f"SELECT MIN(trade_date), MAX(trade_date) "
                f"FROM read_parquet('{glob}', hive_partitioning=true)"
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
            return date.fromisoformat(str(x)[:10])

        min_d, max_d = _to_date(r[0]), _to_date(r[1])

        expected = set(trading_days(min_d, max_d))
        rows = con.execute(
            f"SELECT DISTINCT trade_date FROM read_parquet('{glob}', hive_partitioning=true)"
        ).fetchall()
        actual = {_to_date(v[0]) for v in rows}

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
        else:
            logger.info("[check] %-14s 无缺失", name)

def main() -> None:
    args = parse_args()
    setup_logging()

    # ── 1. 当日 15:30 后自动更新数据 ──
    ensure_data_ready()

    # ── 2. 增量 ETL ──
    if args.skip_etl:
        print("[serve] 已跳过增量 ETL")
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
                print(f"[serve] 增量 ETL 完成: 处理 {p} 个文件, 失败 {f}")
        except Exception as e:
            print(f"[serve] 增量 ETL 失败: {e}")
            print("[serve] 继续启动服务（请手动检查日志 logs/etl.log）")

    # ── 3. 数据完整性检查 ──
    try:
        check_data_integrity()
    except Exception:
        logger.exception("[serve] 数据完整性检查失败（不影响启动）")

    # 启动时增量刷新两个汇总表
    try:
        from queries import (
            rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
            get_concept_date_top10, get_limit_up_ladder,
        )
        rebuild_market_amount()
        rebuild_daily_pct(force_recent_days=10)
        rebuild_limit_up_streak(limit_days=200)   # 2.8s 全量 streak

        # ── 预热最近 20 天的 lru_cache（第一次查询也 < 100ms）──
        print("[serve] 预热最近 20 天缓存…")
        from queries import _list_partition_dates
        from config import CONCEPT_DIR
        all_td = _list_partition_dates(CONCEPT_DIR)
        for d in all_td[-20:]:
            try:
                get_concept_date_top10(d)
                get_limit_up_ladder(d)
            except Exception:
                pass
        print("[serve] 预热完成")
    except Exception:
        logging.getLogger(__name__).exception("启动预热失败，继续启动")

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
            "data/*", "logs/*", "raw_data/*", "map/*",
            "*.parquet", "*.xlsx", "*.log",
        ],
    )


if __name__ == "__main__":
    main()