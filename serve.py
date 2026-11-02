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
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.append(str(Path(__file__).parent))

from config import setup_logging, CONCEPT_DIR, CONCEPT_PICKS_DIR

logger = logging.getLogger("serve")


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


def main() -> None:
    args = parse_args()
    setup_logging()

    # ── 1. 按时间窗更新数据 ──
    from startup.data_ready import ensure_data_ready
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
        from startup.integrity import check_data_integrity
        check_data_integrity()
    except Exception:
        logger.exception("[serve] 数据完整性检查失败（不影响启动）")

    # ── 4. 增量汇总表 + compact + picks ──
    try:
        from core.rebuild import (
            rebuild_market_amount, rebuild_daily_pct, rebuild_limit_up_streak,
        )
        from core.loaders import _list_partition_dates
        from queries import (
            _get_tops_and_scores, _build_intersection_picks,
        )
        from core.picks_cache import _save_picks_cache
        from startup.archive import maybe_rebuild_month_archive

        n_ma = rebuild_market_amount()
        logger.info(f"[serve] market_amount 新增 {n_ma} 天")

        n_dp = rebuild_daily_pct(force_recent_days=3)
        logger.info(f"[serve] daily_pct 写入 {n_dp} 天")

        n_st = rebuild_limit_up_streak(limit_days=3, force_recent_days=3)
        logger.info(f"[serve] limit_up_streak 写入 {n_st} 天")

        # compact 重建必须在查询之前
        maybe_rebuild_month_archive()

        # 补最新日期的 picks
        all_td = _list_partition_dates(CONCEPT_DIR)
        for d in all_td[-1:]:
            p = CONCEPT_PICKS_DIR / f"trade_date={d}" / "part.parquet"
            if not p.exists():
                try:
                    mt, ta, tp, tl, cs = _get_tops_and_scores(d)
                    picks = _build_intersection_picks(ta, tp, tl, d, mt, cs)
                    _save_picks_cache(d, picks)
                    print(f"[serve] {d} picks 已生成")
                except Exception:
                    logger.exception("[serve] %s picks 生成失败", d)
    except Exception:
        logger.exception("增量更新失败，继续启动")

    # ── 5. 启动 uvicorn ──
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