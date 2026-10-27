from pathlib import Path
import pandas as pd
import duckdb

# ⬇️ 改成你备份的 data 文件夹路径
BACKUP_ROOT = Path(r".\data")

con = duckdb.connect()
CHECKS = ["quote", "industry_flow", "concept_flow", "concept_date", "daily_pct"]

print("=" * 70)
print(f"备份路径: {BACKUP_ROOT}")
print(f"存在: {BACKUP_ROOT.exists()}")
print("=" * 70)

for kind in CHECKS:
    kind_dir = BACKUP_ROOT / kind
    if not kind_dir.exists():
        print(f"\n[{kind}] ❌ 目录不存在")
        continue

    dates = sorted([
        d.name.split("=")[1] for d in kind_dir.iterdir()
        if d.is_dir() and d.name.startswith("trade_date=") 
        and (d / "part.parquet").exists()
    ])
    
    print(f"\n[{kind}] 共 {len(dates)} 个交易日")
    if not dates:
        continue
    print(f"  最早: {dates[0]}  最新: {dates[-1]}")
    
    # 抽样检查最老的那一天
    sample_date = dates[0]
    p = kind_dir / f"trade_date={sample_date}" / "part.parquet"
    schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").df()
    
    td_row = schema[schema["column_name"] == "trade_date"]
    if not td_row.empty:
        td_type = td_row.iloc[0]["column_type"]
        flag = "✅ 标准日期" if td_type in ("DATE", "VARCHAR") else "⚠️ 脏类型"
        print(f"  抽样 {sample_date}: trade_date 类型 = {td_type}  {flag}")

print("\n" + "=" * 70)
print("交叉验证：000001 在 2024-02-01 的涨跌幅")
print("=" * 70)
try:
    q = con.execute(
        f"SELECT close, pct_change FROM read_parquet('{BACKUP_ROOT}/quote/trade_date=2024-02-02/part.parquet') WHERE code='000001'"
    ).fetchone()
    d = con.execute(
        f"SELECT pct_change FROM read_parquet('{BACKUP_ROOT}/daily_pct/trade_date=2024-02-02/part.parquet') WHERE code='000001'"
    ).fetchone()
    print(f"quote:     close={q[0]}, pct_change={q[1]}")
    print(f"daily_pct: pct_change={d[0] if d else '❌ 缺失'}")
    if q and d:
        diff = abs(float(q[1]) - float(d[0]))
        if diff < 0.2:
            print(f"差异 {diff:.3f}%  ✅ daily_pct 与 quote 口径一致")
        else:
            print(f"差异 {diff:.3f}%  ⚠️ daily_pct 可能是旧算法（不致命，恢复后重建即可）")
except Exception as e:
    print(f"检查失败（可能缺某日）: {e}")