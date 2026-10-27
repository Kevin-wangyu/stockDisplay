# -*- coding: utf-8 -*-
"""
通达信-问小达 概念指数(板块) 按交易日批量导出
============================================

核心流程：
  1. 加载 A 股交易日历，筛选出 [start_date, end_date] 内的交易日
  2. 对通用查询（不含日期）调用一次 StockSelect，得到 nlpse_id
  3. 对每个交易日调用 exportExcel，服务端直接返回 xlsx 二进制
  4. 保存为 "{YYYY年MM月DD日}概念指数.xlsx"

安全策略：
  - 每天仅 1 次请求（不再逐页拉取）
  - 交易日之间随机等待 40~90 秒
  - 命中 503/429 时全客户端冷却 3~5 分钟
  - 进度记录 + 断点续传（以 xlsx 文件本身为权威）
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import re
import akshare as ak
from akshare import pro
import browser_cookie3
import pandas as pd
import requests
from etl.field_mapping import INDUSTRY_OUTPUT_COLS, parse_trade_date
import warnings
from config import (COOKIE, QUOTE_DIR, INDUSTRY_DIR, CONCEPT_DIR, 
    RAW_QUOTE_DIR, RAW_CONCEPT_DIR, RAW_INDUSTRY_DIR, RAW_DIR,
    RAW_STOCK_CONCEPT_DIR, CONCEPT_DATE_DIR, RAW_AMO_DIR, AMO_DIR)

from trading_calendar import _load_calendar
warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)


# ────────────────────────── 配置 ──────────────────────────


@dataclass
class Config:
    # 端点
    base_url: str = "https://wenda.tdx.com.cn/TQL"
    export_url: str = "https://wenda.tdx.com.cn/JMM/JNLPSE/exportExcel"
    entry_parse: str = "NLPSE.StockSelect"
    ri_query: str = "6C00"

    # 查询参数
    rang: str = "ZS"                    # ZS=指数/板块，AG=个股
    order_field_prefix: str = "收盘价.前复权"
    tdxid: str = "R25021719184311011919XYTT"

    # 节奏控制（秒）
    pre_request_delay: tuple = (3.0, 5.0)    # 每次请求前
    day_delay: tuple = (5.0, 8.0)           # 交易日之间
    cooldown_on_503: tuple = (180.0, 240.0)   # 限流冷却

    # 重试
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

# ────────────────────────── 编码工具 ──────────────────────────

def decode_json(raw_bytes: bytes):
    """依次尝试 UTF-8 / GBK / GB18030 解码 JSON。

    实测：StockSelect 接口偏 GBK，NLPQuery/exportExcel 偏 UTF-8。
    因此不能硬编码任何一种编码。
    """
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]
    errors = []
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return json.loads(raw_bytes.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            errors.append(f"{enc}: {e}")
    raise ValueError("无法解码响应。尝试过的编码: " + "; ".join(errors))


# ────────────────────────── 交易日历 ──────────────────────────

class TradeCalendar:
    """基于 akshare 的 A 股交易日历。"""

    def __init__(self) -> None:
        logger.info("正在获取 A 股交易日历...")
        s = _load_calendar()
        self._dates = set(s.dt.date.tolist())
        logger.info("交易日历加载完成，共 %d 个交易日。", len(self._dates))

    def is_trading_day(self, d: datetime) -> bool:
        return d.date() in self._dates

    def trading_days(self, start: datetime, end: datetime) -> list:
        out, cur = [], start
        while cur <= end:
            if self.is_trading_day(cur):
                out.append(cur)
            cur += timedelta(days=1)
        return out[::-1]


# ────────────────────────── 进度记录 ──────────────────────────

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


# ────────────────────────── 问小达客户端 ──────────────────────────

class TDXXiaoda:
    """通达信问小达客户端。

    用法::

        xd = TDXXiaoda()
        nlpse_id = xd.parse_query("所有概念指数，收盘价,涨跌幅")
        xd.export_excel(nlpse_id, date_dot="2026.09.17",
                        save_path="2026年09月17日概念指数.xlsx")
    """

    def __init__(self, cookie: str = "", rang: str = "AG", config: Optional[Config] = None):
        self.cfg = config or Config()
        self.rang = rang
        self.session = requests.Session()
        self.cookie = cookie or self._auto_cookie()
        self._nlpse_cache: dict[str, str] = {}
        self._cooling_until: float = 0.0
        self._current_ri: str = self.cfg.ri_query     # ★ 动态 RI，初值用配置兜底

    # ── Cookie ──────────────────────────────────

    def _auto_cookie(self) -> str:
        for name, loader in [
            ("edge", browser_cookie3.edge),
            ("chrome", browser_cookie3.chrome),
            ("firefox", browser_cookie3.firefox),
        ]:
            try:
                cj = loader(domain_name="wenda.tdx.com.cn")
                for c in cj:
                    if c.name == "ASPSessionID":
                        logger.info("从 %s 读取到 ASPSessionID", name)
                        return f"ASPSessionID={c.value};"
            except Exception as exc:
                logger.debug("%s 读取失败: %s", name, exc)
        raise RuntimeError("所有浏览器都读不到 ASPSessionID，请手工传入 cookie=")

    # ── 请求头 / 冷却 ────────────────────────────

    def _common_headers(self) -> dict:
        return {
            "Cookie": self.cookie,
            "User-Agent": random.choice(self.cfg.user_agents),
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://wenda.tdx.com.cn",
            "Referer": "https://wenda.tdx.com.cn/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _respect_cooldown(self):
        remaining = self._cooling_until - time.time()
        if remaining > 0:
            logger.warning("客户端处于冷却中，还需等待 %.0f 秒...", remaining)
            time.sleep(remaining)

    def _trigger_cooldown(self, seconds: float):
        target = time.time() + seconds
        self._cooling_until = max(self._cooling_until, target)
        logger.warning(
            "触发全局冷却，所有后续请求暂停至 %s",
            datetime.fromtimestamp(self._cooling_until).strftime("%H:%M:%S"),
        )

    def _warmup_query(self, nlpse_id: str):
        """返回 (实际日期, 表头列表, FieldInfo) 。"""
        import re
        url = f"{self.cfg.base_url}?Entry=NLPSE.NLPQuery"
        params = {"RI": self._current_ri}
        payload = [{
            "nlpse_id": nlpse_id,
            "POS": 0,
            "COUNT": 1,
            "order_field": "",
            "dynamic_order": "",
            "order_flag": "",
            "timestamps": 0,
            "op_flag": 1,
            "screen_type": 1,
            "RANG": self.rang,
            "forward": "1",
        }]

        try:
            data = self._post(
                url, params=params,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                extra_headers={"Content-Type": "text/plain"},
            )
        except RuntimeError as exc:
            logger.warning("NLPQuery 预热请求失败：%s", exc)
            return None, [], []

        if not isinstance(data, list) or len(data) < 2:
            logger.warning("NLPQuery 预热响应异常")
            return None, [], []

        meta = data[0] if isinstance(data[0], list) else []
        if (meta[0] if meta else None) != 0:
            logger.warning("NLPQuery 预热错误: %s", meta)
            return None, [], []

        cols = data[1]
        # data[2] 是格式行，长度与 cols 相同
        fmt = data[2] if len(data) > 2 and isinstance(data[2], list) else []

        actual_date = None
        for c in cols:
            if isinstance(c, str):
                m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", c)
                if m:
                    actual_date = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
                    break

        logger.info("NLPQuery 服务端返回的实际日期: %s，表头 %d 列",
                    actual_date, len(cols))
        return actual_date, cols, fmt

    # ── 通用 POST（带重试/冷却） ───────────────────

    def _post(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        data: bytes = b"",
        extra_headers: Optional[dict] = None,
        return_binary: bool = False,
    ):
        headers = self._common_headers()
        if extra_headers:
            headers.update(extra_headers)

        last_exc = None
        for attempt in range(1, self.cfg.max_retries + 1):
            self._respect_cooldown()
            try:
                resp = self.session.post(
                    url,
                    params=params,               # ★ 传给 requests
                    headers=headers,
                    data=data,
                    timeout=self.cfg.request_timeout,
                )
                resp.raise_for_status()
                return resp.content if return_binary else decode_json(resp.content)

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                last_exc = exc
                if status in (429, 503):
                    cool = random.uniform(*self.cfg.cooldown_on_503)
                    logger.warning(
                        "服务端限流 %d，第 %d/%d 次", status, attempt,
                        self.cfg.max_retries,
                    )
                    self._trigger_cooldown(cool)
                else:
                    wait = 2 ** attempt
                    logger.warning(
                        "请求失败（%s），第 %d/%d 次，%ds 后重试",
                        status, attempt, self.cfg.max_retries, wait,
                    )
                    time.sleep(wait)

            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "请求异常 %s，第 %d/%d 次，%ds 后重试",
                    exc, attempt, self.cfg.max_retries, wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"POST {url} 重试 {self.cfg.max_retries} 次仍失败"
        ) from last_exc

    # ── 语义解析：自然语言 → nlpse_id ─────────────

    def parse_query(self, natural_language: str) -> str:
        if natural_language in self._nlpse_cache:
            cached = self._nlpse_cache[natural_language]
            logger.debug("nlpse_id 命中缓存: %s", cached)
            return cached

        payload = [{
            "message": natural_language,
            "TDXID": self.cfg.tdxid,        # ★ 不再空字符串
            "wdbk": "",
            "RANG": self.rang,
            "forward": "1",
            "rang_message": "",
        }]

        url = f"{self.cfg.base_url}?Entry={self.cfg.entry_parse}"
        time.sleep(random.uniform(*self.cfg.pre_request_delay))

        data = self._post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            extra_headers={"Content-Type": "text/plain"},
        )

        # ───── 诊断：完整 dump StockSelect 响应 ─────
        logger.debug("=" * 60)
        logger.debug("[DIAG] StockSelect 完整响应：")
        logger.debug(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
        logger.debug("=" * 60)

        if not isinstance(data, list):
            raise RuntimeError(f"StockSelect 响应不是 list: {type(data).__name__}")

        if len(data) < 4:
            raise RuntimeError(
                f"StockSelect 响应长度不足({len(data)})。完整响应: {data!r}"
            )

        meta = data[0]
        error_code = meta[0] if len(meta) > 0 else None
        error_info = meta[1] if len(meta) > 1 else ""
        if error_code not in (0, None):
            raise RuntimeError(
                f"StockSelect 返回错误码 {error_code}: {error_info}"
            )

        # ★ 提取动态 RI
        if len(meta) > 3 and meta[3]:
            new_ri = str(meta[3])
            if new_ri != self._current_ri:
                logger.info("会话 RI 更新：%s → %s", self._current_ri, new_ri)
                self._current_ri = new_ri

        cols = data[1]
        col_name = "nlpse_id"
        if col_name not in cols:
            candidates = [
                c for c in cols
                if isinstance(c, str) and "nlpse_id" in c
            ]
            if not candidates:
                raise RuntimeError(f"响应列名中找不到 nlpse_id。实际列名: {cols}")
            col_name = candidates[0]

        col_idx = cols.index(col_name)
        first_row = data[3]
        if not isinstance(first_row, list) or len(first_row) <= col_idx:
            raise RuntimeError(
                f"数据行结构与列名不匹配。列数={len(cols)}，"
                f"首行={'非列表' if not isinstance(first_row, list) else len(first_row)}"
            )

        nlpse_id = first_row[col_idx]
        if not nlpse_id:
            raise RuntimeError(
                f"第一行 nlpse_id 为空。首行前若干字段: {first_row[:5]}"
            )

        nlpse_id = str(nlpse_id)
        self._nlpse_cache[natural_language] = nlpse_id
        logger.info("条件解析为 nlpse_id=%s（已缓存）", nlpse_id)
        return nlpse_id


    def _activate_nlpse(self, nlpse_id: str, 
                    timeout: float = 60.0) -> bool:
        url = f"{self.cfg.base_url}?Entry=NLPSE.NLPCheckDelete"
        params = {"RI": self._current_ri}
        payload = [{
            "nlpse_id": nlpse_id,
            "op_flag": 1,
            "screen_type": 1,
            "RANG": self.rang,
            "forward": "1",
        }]

        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                data = self._post(
                    url,
                    params=params,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    extra_headers={"Content-Type": "text/plain"},
                )
            except RuntimeError as exc:
                logger.warning("NLPCheckDelete 请求失败: %s", exc)
                return False

            # ───── 诊断：完整 dump ─────
            logger.debug("=" * 60)
            logger.debug("[DIAG] NLPCheckDelete 第 %d 次响应：", attempt)
            logger.debug(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
            logger.debug("=" * 60)

            if not isinstance(data, list) or not data:
                logger.warning("NLPCheckDelete 响应异常: %r", data)
                return False

            meta = data[0] if isinstance(data[0], list) else []
            err = meta[0] if meta else None

            if err == 99812:
                logger.debug("NLPCheckDelete 处理中（第 %d 次）...", attempt)
                time.sleep(0.5)
                continue
            if err == 0:
                logger.info("nlpse_id 已激活（等待 %d 次）", attempt)
                if len(meta) > 3 and meta[3]:
                    new_ri = str(meta[3])
                    if new_ri != self._current_ri:
                        logger.info(
                            "激活响应更新 RI：%s → %s", self._current_ri, new_ri
                        )
                        self._current_ri = new_ri
                return True

            msg = meta[1] if len(meta) > 1 else ""
            logger.warning("NLPCheckDelete 错误 %s: %s", err, msg)
            return False

        logger.warning("NLPCheckDelete 等待超时（%.0fs）", timeout)
        return False

    # ── 导出：一次 POST 直接拿到 xlsx ──────────────

    def export_excel(self, nlpse_id, *, date_dot, save_path):
        params = {"RI": self._current_ri}

        if not self._activate_nlpse(nlpse_id):
            logger.error("nlpse_id=%s 激活失败，跳过导出", nlpse_id)
            return False

        actual_date, cols, fmt = self._warmup_query(nlpse_id)
        if actual_date and actual_date != date_dot:
            logger.warning("服务端实际日期(%s) ≠ 请求日期(%s)，改用服务端日期",
                        actual_date, date_dot)
            date_dot = actual_date

        # ── 构造 StoragePathName：用真实 TDXID 前缀
        if self.cfg.tdxid:
            import random as _rnd
            from datetime import datetime as _dt
            random_suffix = "".join(str(_rnd.randint(0, 9)) for _ in range(10))
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            date_dir = _dt.now().strftime("%Y-%m-%d")
            storage_path = (
                f"WenDaFile\\{date_dir}\\"
                f"{self.cfg.tdxid}{random_suffix}_{timestamp}.xlsx"
            )
        else:
            storage_path = ""
        logger.info("StoragePathName: %s", storage_path)

        # ── 构造 FieldName：从 NLPQuery 表头生成
        #    Key: 原始表头（含尾部 0# / 1# 排序标识）
        #    Name: 去掉尾部 '#<数字>' 后的显示名
        field_name = []
        if cols:
            for c in cols:
                if not isinstance(c, str):
                    continue
                # 去掉尾部形如 "0#"、"1#" 的标志
                display = re.sub(r"[01]#$", "", c)
                field_name.append({"Key": c, "Name": display})
        field_name_json = json.dumps(field_name, ensure_ascii=False) if field_name else ""

        # ── 内层 Option
        inner = [{
            "nlpse_id": nlpse_id,
            "order_field": f"{self.cfg.order_field_prefix}<br>{date_dot}",
            "order_flag": "0",
            "op_flag": 9,
            "export": 1,
            "timestamps": 0,
            "POS": "0",
            "COUNT": "-1",
            "RANG": self.rang,
            "forward": "1",
        }]

        # ── 外层
        payload = [{
            "ProName": "NLPSE.NLPQuery",
            "Option": json.dumps(inner, ensure_ascii=False),
            "FileType": "XLSX",
            "StoragePathName": storage_path,
            "StorageFlag": "2",
            "AddFieldName": "1",
            "FieldName": field_name_json,
        }]

        time.sleep(random.uniform(*self.cfg.pre_request_delay))

        body = self._post(
            self.cfg.export_url,
            params=params,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            extra_headers={"Content-Type": "text/plain"},
            return_binary=True,
        )
        is_xlsx_magic = body[:4] == b"PK\x03\x04"
        if not is_xlsx_magic:
            logger.error("exportExcel 未返回 xlsx，响应体长度=%d", len(body))
            _success = False
            try:
                logger.error("响应内容：%s", body.decode("utf-8")[:2000])
            except UnicodeDecodeError:
                logger.error("响应前 200 字节：%r", body[:200])
            return False

        _success = True
        save = Path(save_path)
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(body)
        logger.info("导出成功：%d 字节 → %s", len(body), save)
        return True

# ────────────────────────── 全局变量 ──────────────────────────

progress = Progress(RAW_DIR / "daily_data.json")
cal = TradeCalendar()
today = datetime.today()
if not cal.is_trading_day(today):
    logger.warning("非交易日仅获取最后数据")
while not cal.is_trading_day(today):
    today -= timedelta(days=1)

# 请求的字段列表（会被嵌入自然语言查询）
amo_components = "全部A股收盘价、超大单总买金额、超大单总卖金额、大单总买金额、大单总卖金额、中单总买金额、中单总卖金额、小单总买金额、小单总总卖金额"
stock_concept_words = "全部A股，收盘价，所属概念，所属通达信研究行业"
stock_words = "全部A股，开盘价，收盘价，最高价，最低价，成交量，成交额，换手率，实际换手率，筹码分布，内盘，外盘，热度，主动买入，主动卖出，超大单净额，大单净额，中单净额，小单净额"
industry_words = "通达信二级行业收盘价，涨跌幅，成交量，自由换手率，超大单净额，大单净额，中单净额，小单净额，涨停家数，上涨家数，下跌家数"
concept_words = "所有概念指数收盘价，涨跌幅，成交量，自由换手率，超大单净额，大单净额，中单净额，小单净额，涨停家数，上涨家数，下跌家数"
type_params = [
    {"type": "买卖金额组成", "rang": "AG", "words": amo_components, "raw_dir": RAW_AMO_DIR, "parquet_dir": AMO_DIR},
    {"type": "A股概念数据", "rang": "AG", "words": stock_concept_words, "raw_dir": RAW_STOCK_CONCEPT_DIR, "parquet_dir": CONCEPT_DATE_DIR},
    {"type": "个股数据", "rang": "AG", "words": stock_words, "raw_dir": RAW_QUOTE_DIR, "parquet_dir": QUOTE_DIR},
    {"type": "行业数据", "rang": "ZS", "words": industry_words, "raw_dir": RAW_INDUSTRY_DIR, "parquet_dir": INDUSTRY_DIR},
    {"type": "概念指数", "rang": "ZS", "words": concept_words, "raw_dir": RAW_CONCEPT_DIR, "parquet_dir": CONCEPT_DIR}
]


# ────────────────────────── 主流程 ──────────────────────────

def fetch_today_index(cookie="", date="", **kwargs):
    rang = kwargs.get("rang", "")
    words = kwargs.get("words", "")
    raw_dir = kwargs.get("raw_dir", "")
    parquet_dir = kwargs.get("parquet_dir", "")
    type = kwargs.get("type", "")

    out_dir = Path(raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = TDXXiaoda(cookie=cookie, rang=rang)

    date_cn = date.strftime("%Y年%m月%d日")
    date_dot = date.strftime("%Y.%m.%d")
    date_key = date.strftime("%Y-%m-%d")
    file_path = out_dir / f"{date_cn}{type}.xlsx"

    if progress.should_skip(date_key, parquet_dir, file_path):
        logger.info("%s 已有有效文件，跳过。", date_cn+type)
        return
    logger.info("正在导出 %s ...", date_cn+type)
    t0 = time.time()

    try:
        # ★ 关键：每天用带日期的 message 单独解析 nlpse_id
        logger.info("  查询词：%s", words)

        nlpse_id = client.parse_query(words)      # 每天一个新 nlpse_id

        ok = client.export_excel(
            nlpse_id,
            date_dot=date_dot,
            save_path=str(file_path),
        )
        elapsed = time.time() - t0

        if not ok or not Progress.is_xlsx_valid(parquet_dir, file_path):
            logger.warning("%s 导出无效。", date_cn)
            progress.mark(date_key, "failed", error="导出文件无效")
            return

        size = file_path.stat().st_size
        logger.info(
            "%s 完成，%d 字节，耗时 %.1fs",
            date_cn, size, elapsed,
        )
        progress.mark(
                date_key, "done",
                size=size, elapsed=round(elapsed, 1),
                file=file_path.name,
            )
        

    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(
            "%s 导出失败（耗时 %.1fs）：%s",
            date_cn, elapsed, exc, exc_info=True,
        )
        progress.mark(
                date_key, "failed",
                error=f"{type(exc).__name__}: {exc}",
                elapsed=round(elapsed, 1),
            )

logger.info("全部完成。")


# ────────────────────────── 入口 ──────────────────────────

def run(date):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    for kwargs in type_params:
        fetch_today_index(
            cookie=f"ASPSESSIONID={COOKIE}",
            date = date,
            **kwargs
        )

if __name__ == "__main__":
    run(today)
