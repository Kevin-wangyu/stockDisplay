"""每月首次启动时重建 compact（当月 + 上月）。"""
from __future__ import annotations

import logging
from datetime import date

from config import DATA_DIR

logger = logging.getLogger(__name__)


def maybe_rebuild_month_archive() -> None:
    """每次启动检查：若 data 里有比 compact 更新的当月文件，重建当月 compact。

    1 日启动会触发；1 日没启动，2 日首次启动也会触发。
    """
    from scripts.archive_month import archive_kind, KINDS
    import duckdb as _dd

    compact_root = DATA_DIR.parent / "data_compact"
    today = date.today()
    cur_month = today.strftime("%Y-%m")

    try:
        con = _dd.connect()
        for kind, kdir in KINDS.items():
            if not kdir.exists():
                continue
            compact_file = compact_root / kind / f"{cur_month}.parquet"

            need_rebuild = False
            if not compact_file.exists():
                need_rebuild = True
            else:
                compact_mtime = compact_file.stat().st_mtime
                for p in kdir.iterdir():
                    if p.is_dir() and p.name.startswith(f"trade_date={cur_month}"):
                        part = p / "part.parquet"
                        if part.exists() and part.stat().st_mtime > compact_mtime:
                            need_rebuild = True
                            break

            if need_rebuild:
                print(f"[serve] 重建 {kind} 当月 compact…")
                archive_kind(kind, kdir, con, rebuild_current=True, dry_run=False)
    except Exception:
        logger.exception("[serve] 月合并更新失败，继续启动")