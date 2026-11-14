"""生成概念别名映射表 data/concept_alias.parquet。

匹配优先级：
  1. exact      完全同名
  2. manual     人工覆盖词典
  3. fuzzy      SequenceMatcher >= 0.75
  4. auto_same  找不到 → cf_name = map_name（自愈，不留 none）

数据源：
  - cf_name：concept_flow.name ∪ concept_date.concepts（分号展开）
  - map_name：stock_sector_map.concepts ∪ concept_flow.name（同名字段）

用法：
    python scripts/build_concept_alias.py            # 全量
    python scripts/build_concept_alias.py --dry-run  # 只打印不写
"""
import argparse
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import pandas as pd

from config import MAP_FILE, DATA_DIR, CONCEPT_DIR, CONCEPT_ALIAS_FILE
from core.db import _q_df, CONCEPT_GLOB, COMPACT_ROOT
from core.loaders import _list_partition_dates

CONCEPT_DATE_DIR   = DATA_DIR / "concept_date"

# ─── 人工覆盖词典（cf_name → map_name）───
MANUAL_OVERRIDES = {
    "小米汽车概念": "小米汽车",
    "外骨骼机器人": "外骨骼",
    "跨境支付CIPS": "跨境支付",
    "机器人概念":   "智能机器",
}

FUZZY_THRESHOLD = 0.75


def _load_cf_names_from_flow() -> set[str]:
    """concept_flow.name（compact 优先）。"""
    df = _q_df(
        f"SELECT DISTINCT name FROM read_parquet({CONCEPT_GLOB}, "
        f"hive_partitioning=true) "
        f"WHERE name IS NOT NULL AND name <> ''"
    )
    return set(df["name"].astype(str).tolist())


def _load_cf_names_from_concept_date() -> set[str]:
    """concept_date.concepts 展开。

    分两批读（避免 compact 与 data 混读的 hive 冲突）：
      - 热数据 data/concept_date/trade_date=*/part.parquet（hive 分区）
      - 冷数据 data_compact/concept_date/*.parquet（普通 parquet）
    """
    names: set[str] = set()

    # 1. 热数据
    if CONCEPT_DATE_DIR.exists():
        glob = str(CONCEPT_DATE_DIR / "trade_date=*" / "part.parquet").replace("\\", "/")
        try:
            df = _q_df(
                f"SELECT DISTINCT trim(unnest(string_split(concepts, ';'))) AS name "
                f"FROM read_parquet('{glob}', hive_partitioning=true) "
                f"WHERE concepts IS NOT NULL AND concepts <> ''"
            )
            if not df.empty:
                names |= set(df["name"].astype(str).tolist())
            print(f"  concept_date 热数据: {len(names)} 个")
        except Exception as e:
            print(f"  ⚠ concept_date 热数据扫描失败: {e}")

    # 2. 冷数据
    compact_dir = COMPACT_ROOT / "concept_date"
    if compact_dir.exists():
        parquets = list(compact_dir.glob("*.parquet"))
        if parquets:
            glob = str(compact_dir / "*.parquet").replace("\\", "/")
            try:
                df = _q_df(
                    f"SELECT DISTINCT trim(unnest(string_split(concepts, ';'))) AS name "
                    f"FROM read_parquet('{glob}') "
                    f"WHERE concepts IS NOT NULL AND concepts <> ''"
                )
                if not df.empty:
                    names |= set(df["name"].astype(str).tolist())
                print(f"  concept_date 冷数据: 累计 {len(names)} 个")
            except Exception as e:
                print(f"  ⚠ concept_date 冷数据扫描失败: {e}")

    names.discard("")
    return names


def _load_map_names() -> set[str]:
    """stock_sector_map.concepts 展开（map_name）。"""
    df = _q_df(
        f"SELECT DISTINCT trim(unnest(string_split(concepts, ';'))) AS name "
        f"FROM read_parquet('{MAP_FILE}') "
        f"WHERE concepts IS NOT NULL AND concepts <> ''"
    )
    s = set(df["name"].astype(str).tolist())
    s.discard("")
    s.discard("--")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t0 = time.time()

    print("[alias] 加载 cf_name…")
    cf_from_flow = _load_cf_names_from_flow()
    print(f"  concept_flow: {len(cf_from_flow)} 个")
    cf_from_date = _load_cf_names_from_concept_date()
    print(f"  concept_date: {len(cf_from_date)} 个")

    cf_names = cf_from_flow | cf_from_date
    print(f"  cf_name 合计: {len(cf_names)} 个")

    print("[alias] 加载 map_name…")
    map_from_map = _load_map_names()
    print(f"  stock_sector_map: {len(map_from_map)} 个")
    # concept_flow 的 name 也可当 map_name（同名字段兼容）
    map_names = map_from_map | cf_from_flow
    print(f"  map_name 合计: {len(map_names)} 个")

    print("[alias] 匹配中…")
    rows = []
    exact = manual = fuzzy = auto_same = 0

    for cf in sorted(cf_names):
        if cf in map_names:
            rows.append({"cf_name": cf, "map_name": cf,
                         "match_type": "exact", "score": 1.0})
            exact += 1
            continue

        if cf in MANUAL_OVERRIDES:
            rows.append({"cf_name": cf, "map_name": MANUAL_OVERRIDES[cf],
                         "match_type": "manual", "score": 1.0})
            manual += 1
            continue

        best, best_sim = None, 0.0
        for m in map_names:
            s = SequenceMatcher(None, cf, m).ratio()
            if s > best_sim:
                best, best_sim = m, s
        if best is not None and best_sim >= FUZZY_THRESHOLD:
            rows.append({"cf_name": cf, "map_name": best,
                         "match_type": "fuzzy", "score": round(best_sim, 3)})
            fuzzy += 1
            continue

        # ⭐ 自愈：cf 自身作为 map_name，不留 none
        rows.append({"cf_name": cf, "map_name": cf,
                     "match_type": "auto_same", "score": 1.0})
        auto_same += 1

    df = pd.DataFrame(rows).sort_values(
        ["match_type", "cf_name"]).reset_index(drop=True)

    print()
    print("=" * 70)
    print(f"总计: {len(df)} 条")
    print(f"  exact      {exact:>4d}")
    print(f"  manual     {manual:>4d}")
    print(f"  fuzzy      {fuzzy:>4d}")
    print(f"  auto_same  {auto_same:>4d}  ← 自愈")
    print(f"耗时: {time.time() - t0:.1f}s")

    if fuzzy:
        print(f"\n=== fuzzy（共 {fuzzy} 条，建议人工核对）===")
        print(df[df["match_type"] == "fuzzy"].to_string(index=False))

    if auto_same:
        print(f"\n=== auto_same（共 {auto_same} 条，新概念自愈）===")
        print(df[df["match_type"] == "auto_same"].head(30).to_string(index=False))

    if args.dry_run:
        print("\n[alias] dry-run，未写盘")
        return

    df.to_parquet(CONCEPT_ALIAS_FILE, index=False)
    print(f"\n[alias] 已写入 {CONCEPT_ALIAS_FILE}")
    print(f"[alias] ⚠ 需重启 serve 让 lru_cache 生效")


if __name__ == "__main__":
    main()