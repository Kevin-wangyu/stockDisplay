"""数据中转层准备工具：将 data_compact 镜像到 data_compact_sync。

原理：
  - 放弃不可靠的物理字节 Hash（Parquet metadata 会导致误判）。
  - 使用 DuckDB 的 EXCEPT ALL 逻辑比对两边的数据。
  - 如果逻辑数据完全一致，则不覆盖 dst 文件，保留其原有的修改时间（mtime）。
  - 如果逻辑数据有差异，则覆盖 dst 文件。

用法：
    python scripts/prepare_compact_sync.py            # 常规同步
    python scripts/prepare_compact_sync.py --dry-run  # 只打印，不实际操作
"""
import argparse
import shutil
import time
from pathlib import Path
import duckdb

# 根目录设定
BASE_DIR = Path(__file__).parent.parent
SRC_DIR = BASE_DIR / "data_compact"
DST_DIR = BASE_DIR / "data_compact_sync"


def is_data_identical(src: Path, dst: Path) -> bool:
    """使用 DuckDB 比对两个 Parquet 文件的逻辑数据是否完全一致。

    原理：双向求差集。如果 A 不在 B 中的记录有 1 条，或者 B 不在 A 中的记录有 1 条，
          就认为不一致。通过 LIMIT 1 可以做到极速短路。
    """
    con = duckdb.connect()
    try:
        s_str = str(src).replace("\\", "/")
        d_str = str(dst).replace("\\", "/")
        
        # 检查 S 有但 D 没有的行
        diff1 = con.execute(f"""
            SELECT * FROM read_parquet('{s_str}') 
            EXCEPT ALL 
            SELECT * FROM read_parquet('{d_str}') 
            LIMIT 1
        """).fetchone()
        
        if diff1 is not None:
            return False
            
        # 检查 D 有但 S 没有的行
        diff2 = con.execute(f"""
            SELECT * FROM read_parquet('{d_str}') 
            EXCEPT ALL 
            SELECT * FROM read_parquet('{s_str}') 
            LIMIT 1
        """).fetchone()
        
        return diff2 is None
    except Exception as e:
        print(f"  ⚠ 比对异常，默认视为不一致: {e}")
        return False
    finally:
        con.close()


def check_and_copy(rel_path: Path, dry_run: bool) -> str:
    """检查单个文件，按需复制。返回状态字符串。"""
    src = SRC_DIR / rel_path
    dst = DST_DIR / rel_path

    # 1. 目标不存在 → 需要新增
    if not dst.exists():
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return "created"

    # 2. 如果后缀是 .parquet，走逻辑数据比对
    if src.suffix == ".parquet":
        if is_data_identical(src, dst):
            # ⭐ 数据一致，不覆盖 dst，保留旧的 mtime
            return "skipped"
        else:
            if not dry_run:
                shutil.copy2(src, dst)
            return "updated"

    # 3. 非 parquet 文件（如 json, log 等），直接用物理大小比对
    # 对于普通文件，通常不用担心 metadata 导致 hash 变化
    if src.stat().st_size == dst.stat().st_size:
        return "skipped"
    else:
        if not dry_run:
            shutil.copy2(src, dst)
        return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只分析，不实际复制")
    args = ap.parse_args()

    if not SRC_DIR.exists():
        print(f"❌ 源目录不存在: {SRC_DIR}")
        return

    print(f"源目录: {SRC_DIR}")
    print(f"目标目录: {DST_DIR}")
    if args.dry_run:
        print("⚠ DRY-RUN 模式：不实际写入文件")
    print("-" * 60)

    # 收集所有文件相对路径
    files = [p.relative_to(SRC_DIR) for p in SRC_DIR.rglob("*") if p.is_file()]
    print(f"扫描到 {len(files)} 个文件，开始逻辑比对...\n")

    t0 = time.time()
    stats = {"created": 0, "updated": 0, "skipped": 0, "error": 0}

    for i, rel_path in enumerate(files, 1):
        try:
            status = check_and_copy(rel_path, args.dry_run)
            stats[status] += 1
            if status in ("created", "updated"):
                print(f"[{status.upper()}] {rel_path}")
        except Exception as e:
            stats["error"] += 1
            print(f"[ERROR] {rel_path}: {e}")

        # 每 20 个打印一次进度
        if i % 20 == 0 or i == len(files):
            print(f"--- 进度 {i}/{len(files)} ---")

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print(f"处理完成！耗时 {elapsed:.1f}s")
    print(f"  新增 (Created):  {stats['created']}")
    print(f"  更新 (Updated):  {stats['updated']}")
    print(f"  跳过 (Skipped):  {stats['skipped']}  （逻辑数据一致，保留旧 mtime）")
    if stats["error"]:
        print(f"  错误 (Error):    {stats['error']}")
    print("=" * 60)
    
    if not args.dry_run and (stats["created"] or stats["updated"]):
        print("\n提示：")
        print("  data_compact_sync 已更新。")
        print("  接下来请确保 GoodSync/Alist 同步的是 data_compact_sync 目录。")


if __name__ == "__main__":
    main()