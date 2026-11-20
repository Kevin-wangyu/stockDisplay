# -*- coding: utf-8 -*-
"""C6/C7 合并策略池的按日本地化缓存读取层。

写入由工作目录的 prebuild_c7_pool.py 后台一次性完成（data/c7_picks/...），
本模块只读：页面渲染按 trade_date 直接查分区 parquet，不再做全量窗口运算。

缓存目录 data/c7_picks 是新增的派生缓存（与 data/concept_picks 同级），
不覆盖、不修改任何原始行情/概念/榜单数据。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from config import DATA_DIR

logger = logging.getLogger(__name__)

C7_POOL_DIR = DATA_DIR / "c7_picks"


def load_c7_pool(trade_date: str) -> list[dict] | None:
    """读某日 C6/C7 合并池缓存。命中返回 records（按 c7_score 降序），未命中 None。"""
    p = C7_POOL_DIR / f"trade_date={str(trade_date)[:10]}" / "part.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return None
    except Exception:
        logger.exception("读取 c7_picks 缓存失败 %s", p)
        return None

    recs = df.to_dict(orient="records")
    out = []
    for r in recs:
        # numpy ndarray / None → python list
        mc = r.get("matched_concepts")
        if mc is None:
            mc = []
        elif hasattr(mc, "tolist"):
            mc = mc.tolist()
        elif isinstance(mc, str):
            mc = [x for x in mc.split(";") if x]
        else:
            mc = list(mc)
        # NaN → None（JSON 序列化安全）
        clean = {}
        for k, v in r.items():
            if k == "matched_concepts":
                clean[k] = [str(x) for x in mc]
                continue
            if v is None:
                clean[k] = None
            elif isinstance(v, float) and pd.isna(v):
                clean[k] = None
            elif hasattr(v, "item"):          # numpy scalar
                try:
                    vv = v.item()
                    clean[k] = None if (isinstance(vv, float) and pd.isna(vv)) else vv
                except Exception:
                    clean[k] = v
            else:
                clean[k] = v
        # 规整类型
        if clean.get("is_streak2") is not None:
            clean["is_streak2"] = bool(clean["is_streak2"])
        for ik in ("lu_cnt_20d", "n_leader_concepts", "streak_days", "n_concepts"):
            if clean.get(ik) is not None:
                try:
                    clean[ik] = int(clean[ik])
                except (ValueError, TypeError):
                    pass
        if not clean.get("source"):
            clean["source"] = "C7"          # 兼容旧缓存（无 source 列）
        out.append(clean)

    out.sort(key=lambda x: (x.get("c7_score") is None, -(x.get("c7_score") or 0)))
    return out


def pool_dir() -> Path:
    return C7_POOL_DIR
