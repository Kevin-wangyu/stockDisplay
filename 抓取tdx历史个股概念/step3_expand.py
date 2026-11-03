# step2_expand.py（优化版）
import os
import time
from datetime import timedelta
import duckdb

from logger_setup import setup_logger
from datetime import date as _date
from trading_calendar import trading_days as _trading_days
RAW = "data/raw_topics.parquet"
OUT_DIR = "data/concept_date"
START_DATE = "2024-02-01"
COMPRESSION = "zstd"

log = setup_logger("expand")


def main():
    start_ts = time.time()
    log.info("=" * 70)
    log.info("[main] Step 2: DuckDB 展开每日概念数据（分批版）")
    log.info(f"[main] 输入: {RAW}")
    log.info(f"[main] 输出目录: {OUT_DIR}")
    log.info(f"[main] 起始日期: {START_DATE}")

    if not os.path.exists(RAW):
        log.error(f"[main] 未找到输入文件: {RAW}")
        return

    # 清空旧输出（避免残留脏数据）
    if os.path.exists(OUT_DIR):
        import shutil
        log.info(f"[main] 清理旧输出目录: {OUT_DIR}")
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    con = duckdb.connect()

    # 内存和线程配置
    con.execute("SET threads = 4")
    con.execute("SET memory_limit = '4GB'")
        # ⭐ 建立全量交易日临时表，替代 generate_series
    log.info("[main] 加载交易日历...")
    _start = _date.fromisoformat(START_DATE)
    _end = _date.today()
    _all_trading = _trading_days(_start, _end)
    log.info(f"[main]   交易日 {len(_all_trading)} 天（{_start} ~ {_end}）")

    con.execute("CREATE TABLE trading_days (trade_date DATE)")
    con.executemany(
        "INSERT INTO trading_days VALUES (?)",
        [(_d,) for _d in _all_trading],
    )

    # 1) 读取原始数据
    log.info("[main] 步骤 1/4: 读取原始数据...")
    con.execute(f"""
        CREATE TABLE raw AS
        SELECT
            code,
            topic_name,
            CAST(strptime(CAST(selected_date AS VARCHAR), '%Y%m%d') AS DATE) AS selected_date
        FROM read_parquet('{RAW}')
        WHERE selected_date > 0
    """)
    n_raw = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    log.info(f"[main]   原始记录: {n_raw} 条")

    # 2) 每只股票的最早入选日期
    log.info("[main] 步骤 2/4: 计算每只股票的最早入选日期...")
    con.execute("""
        CREATE TABLE stock_start AS
        SELECT code, MIN(selected_date) AS start_date
        FROM raw
        GROUP BY code
    """)
    n_stocks = con.execute("SELECT COUNT(*) FROM stock_start").fetchone()[0]
    log.info(f"[main]   股票数: {n_stocks} 只")

    # 3) 生成月份列表
    log.info("[main] 步骤 3/4: 生成月份列表...")
    con.execute(f"""
        CREATE TABLE month_list AS
        SELECT DISTINCT DATE_TRUNC('month', CAST(d AS DATE))::DATE AS month_start
        FROM generate_series(
            DATE '{START_DATE}',
            CURRENT_DATE,
            INTERVAL 1 DAY
        ) AS t(d)
        ORDER BY month_start
    """)
    months = [row[0] for row in con.execute("SELECT month_start FROM month_list").fetchall()]
    log.info(f"[main]   待处理月份数: {len(months)}")

    # 4) 按月循环写出
    log.info("[main] 步骤 4/4: 逐月展开并写出...")
    total_rows = 0
    for i, month_start in enumerate(months, 1):
        month_end = (month_start + timedelta(days=32)).replace(day=1)
        ms = month_start.strftime("%Y-%m-%d")
        me = month_end.strftime("%Y-%m-%d")

        t0 = time.time()
        con.execute(f"""
            COPY (
                SELECT
                    ds.trade_date,
                    r.code,
                    string_agg(r.topic_name, ';' ORDER BY r.selected_date, r.topic_name) AS concepts
                FROM raw r
                JOIN stock_start ss ON r.code = ss.code
                JOIN (
                    SELECT trade_date FROM trading_days
                    WHERE trade_date BETWEEN GREATEST(DATE '{ms}', DATE '{START_DATE}')
                                          AND DATE '{me}' - INTERVAL 1 DAY
                ) ds
                ON ds.trade_date BETWEEN
                    GREATEST(r.selected_date, ss.start_date, DATE '{START_DATE}')
                    AND DATE '{me}' - INTERVAL 1 DAY
                GROUP BY ds.trade_date, r.code
            )
            TO '{OUT_DIR}'
            (FORMAT PARQUET,
            PARTITION_BY (trade_date),
            OVERWRITE_OR_IGNORE,
            COMPRESSION '{COMPRESSION}')
        """)
        elapsed = time.time() - t0

        # 统计本月行数
        n_month = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT trade_date, code FROM (
                    SELECT ds.trade_date, r.code
                    FROM raw r
                    JOIN stock_start ss ON r.code = ss.code
                    JOIN (
                        SELECT trade_date FROM trading_days
                        WHERE trade_date BETWEEN GREATEST(DATE '{ms}', DATE '{START_DATE}')
                                              AND DATE '{me}' - INTERVAL 1 DAY
                    ) ds
                      ON ds.trade_date BETWEEN
                         GREATEST(r.selected_date, ss.start_date, DATE '{START_DATE}')
                         AND DATE '{me}' - INTERVAL 1 DAY
                )
            )
        """).fetchone()[0]
        total_rows += n_month

        log.info(f"[main]   [{i}/{len(months)}] {ms} ~ {me} | {n_month} 行 | {elapsed:.1f}s")

    # 统计输出
    part_count = 0
    total_size = 0
    for root, dirs, files in os.walk(OUT_DIR):
        for f in files:
            if f.endswith(".parquet"):
                part_count += 1
                total_size += os.path.getsize(os.path.join(root, f))

    elapsed = time.time() - start_ts
    log.info("-" * 70)
    log.info(f"[main] 完成: 约 {total_rows} 行, {part_count} 个分区, {total_size/1024/1024:.1f} MB")
    log.info(f"[main] 总耗时: {elapsed:.1f} 秒")
    log.info(f"[main] 输出: {OUT_DIR}/trade_date=*/")
    log.info("=" * 70)

    # 在 step2_expand.py 的 main() 末尾、con.close() 之前加：

    # 统一把 data_0.parquet 改名为 part.parquet
    log.info("[main] 统一重命名分区文件为 part.parquet...")
    renamed = 0
    for root, dirs, files in os.walk(OUT_DIR):
        for f in files:
            if f.endswith(".parquet") and f != "part.parquet":
                old_path = os.path.join(root, f)
                new_path = os.path.join(root, "part.parquet")
                if os.path.exists(new_path):
                    os.remove(old_path)   # 多余文件删除
                else:
                    os.rename(old_path, new_path)
                    renamed += 1
    log.info(f"[main] 重命名 {renamed} 个文件")

    # ══════════════════════════════════════════════════════════
    # 额外：生成 concept_meta.parquet（gld + ztnr 独立表）
    #
    # 同一 (code, topic_name) 只保留最早入选日那条，避免重复
    # ══════════════════════════════════════════════════════════
    META_PATH = "data/concept_meta.parquet"
    log.info(f"[main] 生成 {META_PATH}（code, topic_name, gld, ztnr, selected_date）...")

    con.execute(f"""
        COPY (
            SELECT code, topic_name, selected_date, gld, ztnr
            FROM (
                SELECT
                    code,
                    topic_name,
                    CAST(selected_date AS VARCHAR) AS selected_date,
                    COALESCE(gld, 1.0) AS gld,
                    COALESCE(ztnr, '') AS ztnr,
                    ROW_NUMBER() OVER (
                        PARTITION BY code, topic_name
                        ORDER BY selected_date
                    ) AS rn
                FROM read_parquet('{RAW}')
                WHERE selected_date > 0
            )
            WHERE rn = 1
            ORDER BY code, topic_name
        )
        TO '{META_PATH}'
        (FORMAT PARQUET, COMPRESSION 'zstd')
    """)

    n_meta = con.execute(f"SELECT COUNT(*) FROM read_parquet('{META_PATH}')").fetchone()[0]
    import os as _os
    meta_size = _os.path.getsize(META_PATH) / 1024 / 1024
    log.info(f"[main] concept_meta 生成完成: {n_meta} 条, {meta_size:.1f} MB")

    con.close()


if __name__ == "__main__":
    main()