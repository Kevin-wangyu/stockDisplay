"""生成概念别名映射表 data/concept_alias.parquet（最终版）。

匹配优先级：
  1. 完全同名
  2. 人工覆盖词典（MANUAL_OVERRIDES）
  3. 模糊匹配（SequenceMatcher >= 0.75）
  4. 匹配失败（match_type = "none"）
"""
import duckdb
import pandas as pd
from difflib import SequenceMatcher
from pathlib import Path

# ─── 人工覆盖词典（cf_name → map_name）───────────────────────
MANUAL_OVERRIDES = {
    "小米汽车概念": "小米汽车",       # 避免误配到"小米概念"
    "外骨骼机器人": "外骨骼",
    "跨境支付CIPS": "跨境支付",
    "机器人概念":   "智能机器",       # map 里 "智能机器" 即传统机器人概念
}

FUZZY_THRESHOLD = 0.75


def main():
    con = duckdb.connect()

    cf = con.execute("""
        SELECT DISTINCT name FROM read_parquet(
            'data/concept_flow/**/*.parquet', hive_partitioning=true)
        WHERE name IS NOT NULL
    """).df()
    cf_names = sorted(set(cf["name"].astype(str)) - {""})

    mp = con.execute("""
        SELECT DISTINCT trim(unnest(string_split(concepts, ';'))) AS name
        FROM read_parquet('data/stock_sector_map.parquet')
    """).df()
    mp_names = sorted(set(mp["name"].astype(str)) - {"", "--"})
    mp_set = set(mp_names)

    rows = []
    for c in cf_names:
        if c in mp_set:
            rows.append({"cf_name": c, "map_name": c,
                         "match_type": "exact", "score": 1.0})
            continue
        if c in MANUAL_OVERRIDES:
            rows.append({"cf_name": c, "map_name": MANUAL_OVERRIDES[c],
                         "match_type": "manual", "score": 1.0})
            continue
        best, best_sim = None, 0.0
        for m in mp_names:
            s = SequenceMatcher(None, c, m).ratio()
            if s > best_sim:
                best, best_sim = m, s
        if best_sim >= FUZZY_THRESHOLD:
            rows.append({"cf_name": c, "map_name": best,
                         "match_type": "fuzzy", "score": round(best_sim, 3)})
        else:
            rows.append({"cf_name": c, "map_name": None,
                         "match_type": "none", "score": round(best_sim, 3)})

    df = pd.DataFrame(rows).sort_values(["match_type", "cf_name"]).reset_index(drop=True)

    print("=" * 70)
    print(f"总计: {len(df)}")
    print(df["match_type"].value_counts().to_string())

    n_none = (df["match_type"] == "none").sum()
    if n_none:
        print(f"\n⚠ 还有 {n_none} 项未匹配：")
        print(df[df["match_type"] == "none"].to_string(index=False))
    else:
        print("\n✅ 全部匹配成功，none = 0")

    out = Path("data/concept_alias.parquet")
    df.to_parquet(out, index=False)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()