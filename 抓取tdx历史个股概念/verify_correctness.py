# verify_correctness.py
import random
import pandas as pd
import pyarrow.parquet as pq
from eltdx import F10Client

XLSX_PATH = "raw_data/全部a股所属概念.xlsx"
IN_PATH = "data/raw_topics.parquet"
SAMPLE_SIZE = 10

df_xlsx = pd.read_excel(XLSX_PATH, dtype={"股票代码": str})
df_xlsx["code"] = df_xlsx["股票代码"].astype(str).str.strip().str.zfill(6)
sample_codes = random.sample(list(df_xlsx["code"]), SAMPLE_SIZE)
print(f"抽样股票: {sample_codes}\n")

df_p = pq.read_table(IN_PATH).to_pandas()
f10 = F10Client(timeout=8)
all_match = True

for code in sample_codes:
    p_rows = df_p[df_p["code"] == code]
    p_topics = {r["topic_name"]: r["selected_date"] for _, r in p_rows.iterrows()}

    try:
        resp = f10.hot_topics(code, section="zttzbkz")
        fresh = {}
        for r in resp.rows:
            rxsj = r.get("rxsj")
            if not rxsj:
                continue
            try:
                rxsj_int = int(rxsj)
            except (ValueError, TypeError):
                continue
            if rxsj_int > 0:
                fresh[r.get("ztmc")] = rxsj_int
    except Exception as e:
        print(f"[{code}] 抓取失败: {e}")
        continue

    # 只比较过滤后仍在白名单里的概念
    p_filtered = {k: v for k, v in p_topics.items()}
    fresh_filtered = {k: v for k, v in fresh.items() if k in p_filtered or k in p_topics}

    if p_filtered == fresh_filtered:
        print(f"[{code}] 一致 ({len(p_filtered)} 条)")
    else:
        all_match = False
        only_p = set(p_filtered) - set(fresh_filtered)
        only_f = set(fresh_filtered) - set(p_filtered)
        print(f"[{code}] 不一致: parquet={len(p_filtered)}, fresh={len(fresh_filtered)}")
        if only_p:
            print(f"    仅 parquet: {list(only_p)[:5]}")
        if only_f:
            print(f"    仅 fresh:   {list(only_f)[:5]}")

print()
print("全部一致" if all_match else "存在不一致")