import logging
import sys
import time

logger = logging.getLogger(__name__)

def prompt_and_reload(force_manual: bool = False) -> None:
    """获取 ASPSessionID → 写入本地缓存。不再修改 config.py。"""
    val = None

    if not force_manual:
        logger.info("[serve] 尝试自动获取 ASPSessionID…")
        try:
            from core.aspsession import get_aspsessionid_with_cache
            val = get_aspsessionid_with_cache()
        except Exception:
            logger.exception("[serve] 自动获取异常")
            val = None

        if val:
            logger.info(f"[serve] ✅ 自动获取成功: {val[:8]}…")
        else:
            logger.info("[serve] ❌ 自动获取失败，转手动输入")

    if not val:
        print()
        print("=" * 60)
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
            from core.aspsession import save_aspsessionid_manual
            save_aspsessionid_manual(val)
            print("[serve] ✅ 已保存至本地缓存文件")
        except Exception as e:
            print(f"[serve] 写入缓存失败: {e}")
            sys.exit(1)