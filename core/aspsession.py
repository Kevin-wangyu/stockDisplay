"""自动获取通达信 ASPSessionID + 带 TTL 的本地缓存。

获取方式：TOUCH 端点匿名获取（不依赖浏览器登录状态）。
缓存策略：30 分钟 TTL，过期自动刷新。
存储位置：data/_aspsession_cache.json（被 .gitignore 忽略）。

用法：
    from core.aspsession import get_aspsessionid_with_cache

    sid = get_aspsessionid_with_cache()        # 命中缓存或自动获取
    sid = get_aspsessionid_with_cache(force_refresh=True)   # 强制刷新
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from config import DATA_DIR

logger = logging.getLogger(__name__)


TOUCH_URL = (
    "https://wenda.tdx.com.cn/TOUCH?"
    "Device=Browser&Ip=0.0.0.0&Mac=00-00-00-00-00-00-00-00"
    "&Build=WEB&Type=41&Ver=1.0.0&EP=0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive",
}

CACHE_FILE = DATA_DIR / "_aspsession_cache.json"
CACHE_TTL_SECONDS = 1800  # 30 分钟


# ══════════════════════════════════════════════════════════════
# 缓存读写
# ══════════════════════════════════════════════════════════════

def _read_cache() -> dict | None:
    """读缓存文件。返回 {"value": str, "ts": float} 或 None。"""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if "value" not in data or "ts" not in data:
            return None
        val = str(data["value"] or "").strip()
        if not val:
            return None
        ts = float(data["ts"])
        return {"value": val, "ts": ts}
    except Exception as e:
        logger.warning("[aspsession] 缓存读取失败: %s", e)
        return None


def _write_cache(value: str) -> None:
    """写缓存（原子写，避免写一半崩）。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"value": value, "ts": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(CACHE_FILE)
        logger.info("[aspsession] 缓存已写入 (value=%s…)", value[:8])
    except Exception:
        logger.exception("[aspsession] 写缓存失败")


def invalidate_cache() -> None:
    """手动删缓存（调试用）。"""
    if CACHE_FILE.exists():
        try:
            CACHE_FILE.unlink()
            logger.info("[aspsession] 缓存已删除")
        except Exception:
            logger.exception("[aspsession] 删缓存失败")


# ══════════════════════════════════════════════════════════════
# 自动获取
# ══════════════════════════════════════════════════════════════

def fetch_aspsessionid_auto(timeout: int = 15) -> str | None:
    """通过 TOUCH 端点匿名获取 ASPSessionID。

    返回纯 value（不带 "ASPSessionID=" 前缀），失败返回 None。
    """
    session = requests.Session()
    try:
        resp = session.get(TOUCH_URL, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("[aspsession] TOUCH 请求失败: %s", e)
        return None

    # 方法 1：精确获取
    aspsessionid = session.cookies.get("ASPSessionID")
    if aspsessionid:
        logger.info("[aspsession] TOUCH 获取成功: %s…", aspsessionid[:8])
        return aspsessionid

    # 方法 2：遍历兜底（大小写不敏感）
    for cookie in session.cookies:
        if "ASPSESSIONID" in cookie.name.upper():
            logger.info("[aspsession] 遍历匹配: %s = %s…",
                        cookie.name, cookie.value[:8])
            return cookie.value

    logger.warning("[aspsession] TOUCH 响应无 ASPSessionID cookie")
    return None


# ══════════════════════════════════════════════════════════════
# 对外入口（带缓存）
# ══════════════════════════════════════════════════════════════

def get_aspsessionid_with_cache(force_refresh: bool = False) -> str | None:
    """获取 ASPSessionID（优先缓存）。

    - 缓存存在且 age < 30 分钟 → 直接返回缓存值（无网络请求）
    - 缓存过期 / 无缓存 / force_refresh=True → 重新获取
    - 获取失败时：若缓存存在（即使过期）也返回旧值兜底
    """
    cached = None if force_refresh else _read_cache()

    if cached:
        age = time.time() - cached["ts"]

        # 时钟回退（系统时间被调回）→ 视为过期
        if age < 0:
            logger.warning("[aspsession] 检测到时钟回退，重新获取")
        elif age < CACHE_TTL_SECONDS:
            logger.info(
                "[aspsession] 命中缓存 (age=%.0fs / %ds, value=%s…)",
                age, CACHE_TTL_SECONDS, cached["value"][:8])
            return cached["value"]
        else:
            logger.info(
                "[aspsession] 缓存过期 (age=%.0fs / %ds)，重新获取",
                age, CACHE_TTL_SECONDS)

    # 重新获取
    val = fetch_aspsessionid_auto(timeout=15)
    if val:
        _write_cache(val)
        return val

    # 兜底：新值拿不到，若还有旧缓存则用旧值（避免流程中断）
    if cached:
        logger.warning(
            "[aspsession] 新值获取失败，退回过期缓存 (age=%.0fs)",
            time.time() - cached["ts"])
        return cached["value"]

    return None