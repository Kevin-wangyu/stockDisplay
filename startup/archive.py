"""每月首次启动时重建 compact（当月 + 上月）。"""
from __future__ import annotations

import logging
from datetime import date

from config import DATA_DIR

logger = logging.getLogger(__name__)


def maybe_rebuild_month_archive() -> None:
    """每次启动检查：如果 data/ 本月的日数据没有完全包含在 compact 中，则只重建本月。"""
    from datetime import date
    from scripts.archive_month import rebuild_month, KINDS
    from core.loaders import _list_partition_dates
    import duckdb as _dd

    compact_root = DATA_DIR.parent / "data_compact"
    today = date.today()
    cur_month = today.strftime("%Y-%m")

    # 概念交集选股已于 2026-11-20 停用：serve 不再重建 concept_picks 的月合并。
    # 仅影响 serve 启动时的自动重建；独立 scripts/archive_month.py 仍可按需手动处理。
    # 既有 concept_picks 日分区与 compact 文件保留不动（零删除）。
    skip_kinds = {"concept_picks"}

    try:
        con = _dd.connect()
        for kind, kdir in KINDS.items():
            if kind in skip_kinds:
                continue
            if not kdir.exists():
                continue

            # 1. 获取 data/ 下本月存在的所有日期
            all_days = _list_partition_dates(kdir)
            month_days = [d for d in all_days if d.startswith(cur_month + "-")]
            
            # 如果 data/ 里本月都没有数据，无需重建
            if not month_days:
                continue

            # 2. 获取 compact 里已有的日期
            compact_file = compact_root / kind / f"{cur_month}.parquet"
            compact_days = set()
            if compact_file.exists():
                try:
                    f_str = str(compact_file).replace("\\", "/")
                    rows = con.execute(
                        f"SELECT DISTINCT CAST(trade_date AS VARCHAR) FROM read_parquet('{f_str}')"
                    ).fetchall()
                    compact_days = {str(r[0])[:10] for r in rows if r[0]}
                except Exception:
                    pass  # 读失败视为空，会触发重建

            # 3. 检查 data/ 里的数据是否已全部合并进 compact
            need_rebuild = False
            for d in month_days:
                if d not in compact_days:
                    need_rebuild = True
                    break

            # 4. 只重建本月
            if need_rebuild:
                logger.info(f"[serve] {kind} 本月 compact 未包含最新数据，重建 {cur_month}…")
                # 直接调用 rebuild_month 只处理当月，不包含上月
                rebuild_month(kind, kdir, cur_month, con, dry_run=False)
                
    except Exception:
        logger.exception("[serve] 月合并更新失败，继续启动")