# step1_5_filter.py
import os
import time
import pandas as pd
import duckdb

from logger_setup import setup_logger

# ---------------- 配置 ----------------
XLSX_WHITELIST = "raw_data/概念数据/2026年10月13日概念指数.xlsx"
IN_PATH = "data/raw_topics.parquet"
OUT_PATH = "data/raw_topics_filtered.parquet"
BACKUP_PATH = "data/raw_topics.parquet.bak"

log = setup_logger("filter")


def load_whitelist():
    df = pd.read_excel(XLSX_WHITELIST, dtype={"sec_name": str})
    # 过滤表头残留和空值
    raw_set = set(df["sec_name"].astype(str).str.strip())
    whitelist = raw_set - {"sec_name", "nan", "NaN", "", "None"}
    log.info(f"[main] 白名单概念数: {len(whitelist)}")
    log.info(f"[main] 样例: {sorted(whitelist)[:8]}")
    return whitelist


def main():
    start_ts = time.time()
    log.info("=" * 70)
    log.info("[main] Step 1.5: 按白名单过滤概念数据")
    log.info(f"[main] 输入: {IN_PATH}")
    log.info(f"[main] 白名单: {XLSX_WHITELIST}")

    if not os.path.exists(IN_PATH):
        log.error(f"[main] 输入文件不存在: {IN_PATH}")
        return

    whitelist = load_whitelist()

    con = duckdb.connect()

    # 注册白名单为临时表
    con.register("whitelist_df", pd.DataFrame({"topic_name": sorted(whitelist)}))

    # 过滤前统计
    r = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT topic_name)
        FROM read_parquet('{IN_PATH}')
    """).fetchone()
    log.info(f"[main] 过滤前: {r[0]} 条记录, {r[1]} 只股票, {r[2]} 个题材")

    # 执行过滤
    log.info("[main] 正在过滤...")
    con.execute(f"""
        COPY (
            SELECT r.code, r.topic_id, r.topic_name, r.selected_date
            FROM read_parquet('{IN_PATH}') r
            INNER JOIN whitelist_df w ON r.topic_name = w.topic_name
        )
        TO '{OUT_PATH}'
        (FORMAT PARQUET, COMPRESSION 'zstd')
    """)

    # 过滤后统计
    r = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT topic_name)
        FROM read_parquet('{OUT_PATH}')
    """).fetchone()
    log.info(f"[main] 过滤后: {r[0]} 条记录, {r[1]} 只股票, {r[2]} 个题材")

    # 被过滤掉的题材（信息输出，便于检查）
    r = con.execute(f"""
        SELECT topic_name, COUNT(*) AS n
        FROM read_parquet('{IN_PATH}') r
        WHERE r.topic_name NOT IN (SELECT topic_name FROM whitelist_df)
        GROUP BY topic_name
        ORDER BY n DESC
    """).fetchall()
    log.info(f"[main] 被过滤的题材数: {len(r)}")
    log.info(f"[main] 被过滤 TOP 10: {[(x[0], x[1]) for x in r[:10]]}")

    # 替换原文件
    if os.path.exists(BACKUP_PATH):
        log.warning(f"[main] 备份已存在，跳过备份: {BACKUP_PATH}")
    else:
        os.replace(IN_PATH, BACKUP_PATH)
        log.info(f"[main] 原文件已备份: {BACKUP_PATH}")

    os.replace(OUT_PATH, IN_PATH)
    log.info(f"[main] 过滤结果已替换: {IN_PATH}")

    elapsed = time.time() - start_ts
    log.info(f"[main] 耗时: {elapsed:.1f} 秒")
    log.info("=" * 70)

    con.close()


if __name__ == "__main__":
    main()