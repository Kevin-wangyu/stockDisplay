"""通达信问小达客户端的配置参数。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    """通达信问小达 HTTP 客户端配置。"""

    # ── 端点 ──
    base_url: str = "https://wenda.tdx.com.cn/TQL"
    export_url: str = "https://wenda.tdx.com.cn/JMM/JNLPSE/exportExcel"
    entry_parse: str = "NLPSE.StockSelect"
    ri_query: str = "6C00"

    # ── 查询参数 ──
    rang: str = "ZS"                     # ZS=指数/板块，AG=个股
    order_field_prefix: str = "收盘价.前复权"
    tdxid: str = "R25021719184311011919XYTT"

    # ── 节奏（秒） ──
    pre_request_delay: tuple = (3.0, 5.0)
    day_delay: tuple = (5.0, 8.0)
    cooldown_on_503: tuple = (180.0, 240.0)

    # ── 重试 ──
    max_retries: int = 4
    request_timeout: tuple = (6, 10)

    user_agents: list = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    ])