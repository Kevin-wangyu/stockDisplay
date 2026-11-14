# step1_fetch.py
import os
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from eltdx import F10Client
from config import COMPACT_DIR
from logger_setup import setup_logger
def _safe_float(v, default=None):
    """转 float，NaN / None / 空串 返回 default。"""
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:   # NaN
            return default
        return f
    except (ValueError, TypeError):
        return default
# ---------------- 配置 ----------------
XLSX_PATH = "raw_data/A股概念/2026年11月02日A股概念数据.xlsx"

OUT_PATH = COMPACT_DIR / "raw_topics.parquet"
PROGRESS_PATH = "data/fetch_progress.json"
ERROR_PATH = "data/fetch_errors.txt"

MAX_WORKERS = 8
MAX_QPS = 10
REQUEST_TIMEOUT = 8
MAX_RETRY = 2
RETRY_BACKOFF = [1, 3]
SAVE_EVERY = 200

log = setup_logger("fetch")


class RateLimiter:
    def __init__(self, qps):
        self.min_interval = 1.0 / qps
        self.lock = threading.Lock()
        self.last_time = 0.0

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self.last_time)
            if wait > 0:
                time.sleep(wait)
            self.last_time = time.monotonic()


rate_limiter = RateLimiter(MAX_QPS)


def get_stock_list():
    log.info(f"[main] 读取股票列表: {XLSX_PATH}")
    df = pd.read_excel(XLSX_PATH, dtype={"sec_code": str})
    codes = []
    skipped = []
    for raw in df["sec_code"].astype(str).str.strip():
        code = raw.zfill(6)
        if len(code) == 6 and code.isdigit():
            codes.append(code)
        else:
            skipped.append(raw)

    seen, unique = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if skipped:
        log.warning(f"[main] 跳过 {len(skipped)} 个非法代码: {skipped[:10]}")
    log.info(f"[main] 股票列表加载完成: {len(unique)} 只 (原始 {len(df)} 行)")
    return unique


def load_progress():
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            data = set(json.load(f))
        log.info(f"[main] 加载进度: 已完成 {len(data)} 只")
        return data
    log.info("[main] 无历史进度，从零开始")
    return set()


def save_progress(done_codes):
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(done_codes), f)
    os.replace(tmp, PROGRESS_PATH)


def fetch_one(client, code):
    thread_name = threading.current_thread().name
    for attempt in range(MAX_RETRY + 1):
        rate_limiter.acquire()
        t0 = time.monotonic()
        try:
            resp = client.hot_topics(code, section="zttzbkz")
            elapsed = time.monotonic() - t0
            rows = []
            for r in resp.rows:
                rxsj = r.get("rxsj")
                if not rxsj:
                    continue
                try:
                    rxsj_int = int(rxsj)
                except (ValueError, TypeError):
                    continue
                if rxsj_int <= 0:
                    continue
                rows.append({
                    "code": code,
                    "topic_id": r.get("id"),
                    "topic_name": r.get("ztmc"),
                    "selected_date": rxsj_int,
                    "gld":  _safe_float(r.get("gld"), default=None),
                    "ztnr": str(r.get("ztnr") or "").strip(),
                })
            log.debug(f"[{thread_name}] [{code}] OK {len(rows)}条 {elapsed:.2f}s 尝试{attempt+1}")
            return rows, None
        except Exception as e:
            elapsed = time.monotonic() - t0
            if attempt < MAX_RETRY:
                log.debug(f"[{thread_name}] [{code}] RETRY 第{attempt+1}次失败 {elapsed:.2f}s: {e}")
                time.sleep(RETRY_BACKOFF[attempt])
            else:
                log.warning(f"[{thread_name}] [{code}] FAIL 重试{MAX_RETRY}次仍失败: {e}")
                return [], f"{code}: {e}"


def _write_parquet(rows):
    if not rows:
        return
    tmp = OUT_PATH + ".tmp"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, OUT_PATH)


def main():
    start_ts = time.time()
    log.info("=" * 70)
    log.info("[main] Step 1: 抓取通达信 F10 热点题材")
    log.info(f"[main] 并发={MAX_WORKERS} QPS={MAX_QPS} 超时={REQUEST_TIMEOUT}s 重试={MAX_RETRY}")

    os.makedirs("data", exist_ok=True)

    codes = get_stock_list()
    done_codes = load_progress()
    todo = [c for c in codes if c not in done_codes]
    log.info(f"[main] 本次待抓取: {len(todo)} 只")

    if not todo:
        log.info("[main] 无待抓取股票，退出")
        return

    all_rows = []
    if os.path.exists(OUT_PATH) and done_codes:
        all_rows = pq.read_table(OUT_PATH).to_pylist()
        log.info(f"[main] 加载已有数据: {len(all_rows)} 条")

    errors = []
    fetch_count = 0
    t_start = time.time()

    client = F10Client(timeout=REQUEST_TIMEOUT)

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="w") as ex:
            futures = {ex.submit(fetch_one, client, c): c for c in todo}
            for fut in as_completed(futures):
                code = futures[fut]
                rows, err = fut.result()
                if err:
                    errors.append(err)
                    # 失败不加入 done_codes，下次自动重试
                else:
                    all_rows.extend(rows)
                    done_codes.add(code)
                fetch_count += 1

                if fetch_count % SAVE_EVERY == 0:
                    save_progress(done_codes)
                    _write_parquet(all_rows)
                    elapsed = time.time() - t_start
                    speed = fetch_count / elapsed if elapsed > 0 else 0
                    eta = (len(todo) - fetch_count) / speed if speed > 0 else 0
                    log.info(
                        f"[main] 进度 {fetch_count}/{len(todo)} | "
                        f"累计{len(all_rows)}条 | "
                        f"速度{speed:.1f}只/s | "
                        f"已用{elapsed/60:.1f}min | "
                        f"预计剩余{eta/60:.1f}min"
                    )
    except KeyboardInterrupt:
        log.warning("[main] 收到中断信号，保存当前进度...")
    finally:
        save_progress(done_codes)
        _write_parquet(all_rows)

    total_elapsed = time.time() - start_ts
    log.info("-" * 70)
    log.info(f"[main] 完成: {len(all_rows)} 条记录, 本次失败 {len(errors)} 只")
    log.info(f"[main] 总耗时: {total_elapsed/60:.2f} 分钟")
    log.info(f"[main] 输出: {OUT_PATH}")

    if errors:
        with open(ERROR_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(errors))
        log.warning(f"[main] 失败记录已写入 {ERROR_PATH}")
        log.warning("[main] 失败样例: " + " | ".join(errors[:5]))

    log.info("=" * 70)


if __name__ == "__main__":
    main()