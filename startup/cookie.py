"""cookie 交互：ASPSessionID 输入 + 写入 config.py + 重载模块。"""
from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def update_cookie_in_config(new_value: str) -> None:
    """把 config.py 里 COOKIE = "..." 替换成新值。"""
    cfg_path = Path(__file__).parent.parent / "config.py"
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


def prompt_and_reload() -> None:
    """交互输入 ASPSessionID → 写 config.py → 重载 config + xdget_daily。

    输入为空 / Ctrl+C → sys.exit(1)。
    """
    print()
    print("=" * 60)
    print("[serve] 未检测到目标日期的行情数据")
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
        update_cookie_in_config(val)
    except Exception as e:
        print(f"[serve] 写入 config.py 失败: {e}")
        sys.exit(1)

    # 重载 config + 强制重新 import xdget_daily
    import config
    importlib.reload(config)
    sys.modules.pop("xdget_daily", None)
    print("[serve] 已更新 config.COOKIE 并重载 config")