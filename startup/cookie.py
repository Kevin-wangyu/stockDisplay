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


def prompt_and_reload(force_manual: bool = False) -> None:
    """获取 ASPSessionID → 写 config.py → 重载 config + xdget_daily。

    策略：
      1. 优先自动获取（TOUCH 端点）
      2. 自动失败 → 提示手动输入
      3. force_manual=True 时跳过自动获取（调试用）
    """
    val = None

    if not force_manual:
        print("[serve] 获取 ASPSessionID（优先缓存）…")
        try:
            from core.aspsession import get_aspsessionid_with_cache
            val = get_aspsessionid_with_cache()
        except Exception:
            logger.exception("[serve] 自动获取异常")
            val = None

        if val:
            print(f"[serve] ✅ 自动获取成功: {val[:8]}…")
        else:
            print("[serve] ❌ 自动获取失败，转手动输入")

    if not val:
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

    # 写 config + reload（原逻辑不变）
    try:
        update_cookie_in_config(val)
    except Exception as e:
        print(f"[serve] 写入 config.py 失败: {e}")
        sys.exit(1)

    import config
    importlib.reload(config)
    sys.modules.pop("xdget_daily", None)
    print("[serve] 已更新 config.COOKIE 并重载 config")