"""抓取进度记录 + 断点续传。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from etl.field_mapping import parse_trade_date

logger = logging.getLogger(__name__)


class Progress:
    """记录每个交易日期的处理状态，支持断点续传。

    跳过规则：只要 xlsx 文件存在且内容非空，就跳过——不管 progress.json
    里怎么记录。progress.json 仅用于记录元信息（大小、耗时、失败原因），
    不参与是否跳过的决策。

    状态：
      done    — 已成功写入 xlsx
      failed  — 重试后仍失败
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
                logger.info(
                    "读取进度文件 %s，已有 %d 条记录。", path, len(self.data)
                )
            except Exception:
                logger.warning("进度文件损坏，重新开始。", exc_info=True)
                self.data = {}

    def get(self, date_str: str) -> Optional[dict]:
        return self.data.get(date_str)

    @staticmethod
    def is_xlsx_valid(parquet_path: Path, path: Path) -> bool:
        """检查 xlsx 是否存在、大小合理、能被 pandas 读出至少一行。"""
        date_standard = parse_trade_date(path.name)
        parquet = parquet_path / f"trade_date={date_standard}"
        if parquet.exists():
            return True
        if not path.exists():
            return False
        try:
            if path.stat().st_size < 5_000:
                logger.warning("文件 %s 过小，视为无效。", path.name)
                return False
        except OSError:
            return False

        # xlsx 一定是 zip，前 4 字节应为 PK\x03\x04
        try:
            with path.open("rb") as f:
                if f.read(4) != b"PK\x03\x04":
                    logger.warning("文件 %s 非合法 xlsx（魔数不符）。", path.name)
                    return False
        except OSError:
            return False

        try:
            df = pd.read_excel(path, nrows=5)
            if df.empty:
                logger.warning("文件 %s 为空表，视为无效。", path.name)
                return False
            return True
        except Exception:
            logger.warning("文件 %s 无法解析，视为无效。", path.name, exc_info=True)
            return False

    def should_skip(self, date_str: str, parquet_path: Path, xlsx: Path) -> bool:
        if self.is_xlsx_valid(parquet_path, xlsx):
            if date_str not in self.data:
                self.data[date_str] = {
                    "status": "done",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "existing_file",
                    "file": xlsx.name,
                }
                self._flush()
            return True
        return False

    def mark(self, date_str: str, status: str, **extra):
        self.data[date_str] = {
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            **extra,
        }
        self._flush()

    def _flush(self):
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def summary(self) -> str:
        if not self.data:
            return "（空）"
        counts = {}
        for rec in self.data.values():
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))