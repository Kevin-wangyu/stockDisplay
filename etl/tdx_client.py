"""通达信问小达 HTTP 客户端。"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import browser_cookie3
import requests

from etl.tdx_config import Config

logger = logging.getLogger(__name__)


def decode_json(raw_bytes: bytes):
    """依次尝试 UTF-8 / GBK / GB18030 解码 JSON。

    实测：StockSelect 接口偏 GBK，NLPQuery/exportExcel 偏 UTF-8。
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


class TDXXiaoda:
    """通达信问小达客户端。

    用法::

        xd = TDXXiaoda()
        nlpse_id = xd.parse_query("所有概念指数，收盘价,涨跌幅")
        xd.export_excel(nlpse_id, date_dot="2026.09.17",
                        save_path="2026年09月17日概念指数.xlsx")
    """

    def __init__(self, cookie: str = "", rang: str = "AG",
                 config: Optional[Config] = None):
        self.cfg = config or Config()
        self.rang = rang
        self.session = requests.Session()
        self.cookie = cookie or self._auto_cookie()
        self._nlpse_cache: dict[str, str] = {}
        self._cooling_until: float = 0.0
        self._current_ri: str = self.cfg.ri_query

    # ── Cookie ──
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

    # ── 请求头 / 冷却 ──
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

    # ── NLPQuery 预热 ──
    def _warmup_query(self, nlpse_id: str):
        url = f"{self.cfg.base_url}?Entry=NLPSE.NLPQuery"
        params = {"RI": self._current_ri}
        payload = [{
            "nlpse_id": nlpse_id, "POS": 0, "COUNT": 1,
            "order_field": "", "dynamic_order": "", "order_flag": "",
            "timestamps": 0, "op_flag": 1, "screen_type": 1,
            "RANG": self.rang, "forward": "1",
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

    # ── 通用 POST ──
    def _post(self, url: str, *, params=None, data: bytes = b"",
              extra_headers=None, return_binary: bool = False, timeout=None):
        headers = self._common_headers()
        if extra_headers:
            headers.update(extra_headers)

        last_exc = None
        for attempt in range(1, self.cfg.max_retries + 1):
            self._respect_cooldown()
            try:
                resp = self.session.post(
                    url, params=params, headers=headers, data=data,
                    timeout=timeout or self.cfg.request_timeout,
                )
                resp.raise_for_status()
                return resp.content if return_binary else decode_json(resp.content)

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                last_exc = exc
                if status in (429, 503):
                    cool = random.uniform(*self.cfg.cooldown_on_503)
                    logger.warning("服务端限流 %d，第 %d/%d 次", status, attempt,
                                   self.cfg.max_retries)
                    self._trigger_cooldown(cool)
                else:
                    wait = 2 ** attempt
                    logger.warning("请求失败（%s），第 %d/%d 次，%ds 后重试",
                                   status, attempt, self.cfg.max_retries, wait)
                    time.sleep(wait)

            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning("请求异常 %s，第 %d/%d 次，%ds 后重试",
                               exc, attempt, self.cfg.max_retries, wait)
                time.sleep(wait)

        raise RuntimeError(
            f"POST {url} 重试 {self.cfg.max_retries} 次仍失败"
        ) from last_exc

    # ── 语义解析 → nlpse_id ──
    def parse_query(self, natural_language: str) -> str:
        if natural_language in self._nlpse_cache:
            cached = self._nlpse_cache[natural_language]
            logger.debug("nlpse_id 命中缓存: %s", cached)
            return cached

        payload = [{
            "message": natural_language,
            "TDXID": self.cfg.tdxid,
            "wdbk": "", "RANG": self.rang,
            "forward": "1", "rang_message": "",
        }]
        url = f"{self.cfg.base_url}?Entry={self.cfg.entry_parse}"
        time.sleep(random.uniform(*self.cfg.pre_request_delay))

        data = self._post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            extra_headers={"Content-Type": "text/plain"},
        )

        logger.debug("=" * 60)
        logger.debug("[DIAG] StockSelect 完整响应：")
        logger.debug(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
        logger.debug("=" * 60)

        if not isinstance(data, list):
            raise RuntimeError(f"StockSelect 响应不是 list: {type(data).__name__}")
        if len(data) < 4:
            raise RuntimeError(
                f"StockSelect 响应长度不足({len(data)})。完整响应: {data!r}")

        meta = data[0]
        error_code = meta[0] if len(meta) > 0 else None
        error_info = meta[1] if len(meta) > 1 else ""
        if error_code not in (0, None):
            raise RuntimeError(f"StockSelect 返回错误码 {error_code}: {error_info}")

        if len(meta) > 3 and meta[3]:
            new_ri = str(meta[3])
            if new_ri != self._current_ri:
                logger.info("会话 RI 更新：%s → %s", self._current_ri, new_ri)
                self._current_ri = new_ri

        cols = data[1]
        col_name = "nlpse_id"
        if col_name not in cols:
            candidates = [c for c in cols if isinstance(c, str) and "nlpse_id" in c]
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
                f"第一行 nlpse_id 为空。首行前若干字段: {first_row[:5]}")

        nlpse_id = str(nlpse_id)
        self._nlpse_cache[natural_language] = nlpse_id
        logger.info("条件解析为 nlpse_id=%s（已缓存）", nlpse_id)
        return nlpse_id

    # ── 激活 nlpse ──
    def _activate_nlpse(self, nlpse_id: str, timeout: float = 60.0) -> bool:
        url = f"{self.cfg.base_url}?Entry=NLPSE.NLPCheckDelete"
        params = {"RI": self._current_ri}
        payload = [{
            "nlpse_id": nlpse_id, "op_flag": 1, "screen_type": 1,
            "RANG": self.rang, "forward": "1",
        }]

        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                data = self._post(
                    url, params=params,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    extra_headers={"Content-Type": "text/plain"},
                )
            except RuntimeError as exc:
                logger.warning("NLPCheckDelete 请求失败: %s", exc)
                return False

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
                        logger.info("激活响应更新 RI：%s → %s",
                                    self._current_ri, new_ri)
                        self._current_ri = new_ri
                return True

            msg = meta[1] if len(meta) > 1 else ""
            logger.warning("NLPCheckDelete 错误 %s: %s", err, msg)
            return False

        logger.warning("NLPCheckDelete 等待超时（%.0fs）", timeout)
        return False

    # ── 导出 xlsx ──
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

        if self.cfg.tdxid:
            random_suffix = "".join(str(random.randint(0, 9)) for _ in range(10))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            date_dir = datetime.now().strftime("%Y-%m-%d")
            storage_path = (f"WenDaFile\\{date_dir}\\"
                            f"{self.cfg.tdxid}{random_suffix}_{timestamp}.xlsx")
        else:
            storage_path = ""
        logger.info("StoragePathName: %s", storage_path)

        field_name = []
        if cols:
            for c in cols:
                if not isinstance(c, str):
                    continue
                display = re.sub(r"[01]#$", "", c)
                field_name.append({"Key": c, "Name": display})
        field_name_json = json.dumps(field_name, ensure_ascii=False) if field_name else ""

        inner = [{
            "nlpse_id": nlpse_id,
            "order_field": f"{self.cfg.order_field_prefix}<br>{date_dot}",
            "order_flag": "0", "op_flag": 9, "export": 1,
            "timestamps": 0, "POS": "0", "COUNT": "-1",
            "RANG": self.rang, "forward": "1",
        }]

        payload = [{
            "ProName": "NLPSE.NLPQuery",
            "Option": json.dumps(inner, ensure_ascii=False),
            "FileType": "XLSX",
            "StoragePathName": storage_path,
            "StorageFlag": "2", "AddFieldName": "1",
            "FieldName": field_name_json,
        }]

        time.sleep(random.uniform(*self.cfg.pre_request_delay))

        body = self._post(
            self.cfg.export_url, params=params,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            extra_headers={"Content-Type": "text/plain"},
            return_binary=True,
            timeout=(10, 20), 
        )
        if body[:4] != b"PK\x03\x04":
            logger.error("exportExcel 未返回 xlsx，响应体长度=%d", len(body))
            try:
                logger.error("响应内容：%s", body.decode("utf-8")[:2000])
            except UnicodeDecodeError:
                logger.error("响应前 200 字节：%r", body[:200])
            return False

        save = Path(save_path)
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(body)
        logger.info("导出成功：%d 字节 → %s", len(body), save)
        return True