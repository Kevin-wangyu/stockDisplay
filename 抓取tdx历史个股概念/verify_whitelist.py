# verify_whitelist.py
import pandas as pd
import duckdb

XLSX_WHITELIST = "raw_data/概念数据/2026年10月13日概念指数.xlsx"
OUT_PATH = "data/raw_topics.parquet"

df_w = pd.read_excel(XLSX_WHITELIST, dtype={"sec_name": str})
whitelist = set(
    df_w["sec_name"].astype(str).str.strip()
) - {"sec_name", "nan", "NaN", "", "None"}

print(f"白名单概念数: {len(whitelist)}")

con = duckdb.connect()
r = con.execute(f"SELECT DISTINCT topic_name FROM read_parquet('{OUT_PATH}')").fetchall()
all_topics = set(row[0] for row in r if row[0])

matched = all_topics & whitelist
unmatched = all_topics - whitelist

print(f"parquet 中 topic_name 数: {len(all_topics)}")
print(f"能匹配白名单: {len(matched)}")
print(f"无法匹配:     {len(unmatched)}")

print("\n无法匹配的 topic_name（前 30 个）:")
for name in sorted(unmatched)[:30]:
    n = con.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{OUT_PATH}')
        WHERE topic_name = ?
    """, [name]).fetchone()[0]
    print(f"  {name:<25} {n:>6} 条")