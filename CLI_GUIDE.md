# CLI 使用指南（供 AI / 脚本调用）

> 所有命令基于 `python cli.py`，输出统一 JSON 结构 `{ok, data, meta}`。
> AI 应先用 `list` 探路，再构造具体查询；大输出用 `--output` 写文件。

---

## 1. 快速开始

```bash
# 查看所有命令
python cli.py --help

# 查看某组命令
python cli.py stock --help
python cli.py concept --help
```

**输出结构**：

```json
{
  "ok": true,
  "data": [],
  "meta": {
    "ts": "2026-11-04T10:30:00",
    "count": 49,
    "elapsed_ms": 45.2
  }
}
```

**失败时**：

```json
{
  "ok": false,
  "error": {
    "type": "ValueError",
    "message": "2024-10-05 无概念数据",
    "hint": ""
  },
  "meta": {
    "ts": "2026-11-04T10:30:00"
  }
}
```

AI 拿到后判断 `result["ok"]`，为 `false` 时处理 `result["error"]`。

---

## 2. 探路（先跑这些了解数据）

```bash
# 有哪些股票（前 100 只）
python cli.py list codes --limit 100

# 按代码或名称搜索
python cli.py list codes --search 平安

# 有哪些指标
python cli.py list indicators

# 只列 SQL 指标
python cli.py list indicators --type sql

# 只列个股可用指标
python cli.py list indicators --scope stock

# 交易日上下文（前后各 N 个交易日）
python cli.py trading days 2024-10-09 --before 5 --after 5

# 相邻交易日
python cli.py trading neighbor 2024-10-09 --direction prev
```

---

## 3. 命令清单

### 3.1 `list` — 列表

| 命令 | 说明 |
|---|---|
| `list codes` | 股票列表（支持 `--search`） |
| `list indicators` | 指标列表（支持 `--type` / `--scope`） |

### 3.2 `stock` — 个股 / 行业

| 命令 | 说明 |
|---|---|
| `stock kline <code>` | 个股 K 线 + 指标 |
| `stock sectors <code>` | 所属行业 + 概念 |
| `stock industry <code>` | 所属行业的 K 线 |
| `stock sector --kind concept --key <name>` | 概念 K 线 |
| `stock amo <code>` | AMO 买卖结构（8 类单子 + 汇总） |

### 3.3 `concept` — 概念

| 命令 | 说明 |
|---|---|
| `concept top10 <date>` | 三榜（归成比 / 涨跌幅 / 涨停数）+ 交集选股 TOP40 |
| `concept ladder <date>` | 涨停天梯（二板及以上） |
| `concept limitup <concept_code> <date>` | 某概念下近 N 日涨停股 |
| `concept gld <code> <concept>` | 关联度（1~5） |
| `concept ztnr <code> <concept>` | 入选原因（自然语言） |
| `concept colors <date>` | 当日概念颜色分类（红/黄/蓝/白，含涨停数/归成比） |

### 3.4 `trading` — 交易日

| 命令 | 说明 |
|---|---|
| `trading days <date>` | 前后 N 个交易日的起止 |
| `trading neighbor <date> --direction prev` | 相邻交易日（prev 或 next） |

---

## 4. 常用参数

| 参数 | 说明 | 示例 |
|---|---|---|
| `--start` | 起始日期 YYYY-MM-DD | `--start 2024-08-01` |
| `--end` | 结束日期 YYYY-MM-DD | `--end 2024-10-31` |
| `--indicators` | 逗号分隔的指标名 | `--indicators ma5,ma20,main_net_3d` |
| `--format` | 输出格式 | `json`（默认）/ `table` / `csv` |
| `--output` | 输出到文件 | `--output /tmp/result.json` |
| `--limit` | 限制行数 | `--limit 50` |
| `--columns` | 只返回指定列 | `--columns trade_date,close,ma5` |

**优先用 `--output`**：stdout 超过约 50 KB 容易被截断，写文件最稳。

### `concept top10` / `concept ladder` 的专用参数

| 参数 | 作用 | 示例 |
|---|---|---|
| `--only` | 只返回 picks / lists / all（仅 top10） | `--only picks` |
| `--limit` | 交集榜 / 天梯榜的条数上限 | `--limit 20` |
| `--columns` | 交集榜 / 天梯榜的字段裁剪 | `--columns code,name,importance_index` |

**典型场景**：

```bash
# 只要交集榜前 20，字段精简
python cli.py concept top10 2024-10-09 \
  --only picks --limit 20 \
  --columns code,name,importance_index,matched_concepts \
  --output /tmp/picks.json

# 天梯榜前 10
python cli.py concept ladder 2024-10-09 \
  --limit 10 \
  --columns code,name,streak_label,importance_index \
  --output /tmp/ladder.json

---

## 5. 批量查询

**多个 code 用逗号分隔**，返回 `{code: result}` 结构：

```bash
# 多股 K 线
python cli.py stock kline 002693,000001,600519 \
  --start 2024-08-01 --end 2024-10-31 \
  --indicators ma5,ma20 \
  --columns trade_date,close,ma5,ma20 \
  --output /tmp/batch.json
```

**多股多概念 gld（笛卡尔积）**：

```bash
python cli.py concept gld 002693,000001 芯片,AI,新能源
```

输出：

```json
{
  "ok": true,
  "data": {
    "002693": {"芯片": 5.0, "AI": 3.0, "新能源": 0.0},
    "000001": {"芯片": 0.0, "AI": 0.0, "新能源": 0.0}
  }
}
```

**单 code 时返回 list**（向后兼容），**多 code 时返回 dict**。

---

## 6. 常见工作流

### 6.1 个股全量分析

```bash
# 1. 探路：所属概念
python cli.py stock sectors 002693

# 2. 拿 K 线 + 指标
python cli.py stock kline 002693 --start 2024-08-01 --end 2026-11-03 \
  --indicators ma5,ma10,ma20,boll_up,boll_low,main_net_3d,huge_order_net_1d \
  --output /tmp/kline.json

# 3. 拿关联度
python cli.py concept gld 002693 芯片,AI,汽车芯片
```

### 6.2 板块轮动

```bash
# 1. 当日三榜 + 交集
python cli.py concept top10 2024-10-09 --output /tmp/top10.json

# 2. 涨停天梯
python cli.py concept ladder 2024-10-09 --min-days 2 --output /tmp/ladder.json

# 3. 某概念的具体涨停股
python cli.py concept limitup 880xxx 2024-10-09 --window 10
```

### 6.3 多股对比

```bash
python cli.py stock kline 002693,000001,600519,300750,688981 \
  --start 2024-09-01 --end 2024-10-31 \
  --indicators ma5,ma20,main_net_3d \
  --columns trade_date,close,ma5,ma20,main_net_3d \
  --output /tmp/compare.json
```

### 6.4 找强势股的关联概念

```bash
# 1. 交集榜 TOP40
python cli.py concept top10 2024-10-09 --output /tmp/top10.json

# 2. 提取前 10 只的所属概念
python -c "import json, subprocess; d = json.load(open('/tmp/top10.json')); codes = [p['code'] for p in d['data']['intersection_picks'][:10]]; out = subprocess.check_output(['python', 'cli.py', 'stock', 'sectors', ','.join(codes)]); open('/tmp/sectors.json', 'wb').write(out)"
```

### 6.5 快速定位当日主线（红 + 黄）

```bash
# 拉颜色分类
python cli.py concept colors 2024-10-09 --output /tmp/colors.json

# 看红色概念（可能最强主线）
python -c "import json; d=json.load(open('/tmp/colors.json'))['data']; print('红色:', d['counts']['red'], '个'); [print(f\"  {c['name']}: 归成比{c['norm_amount_ratio']}, 涨停{c['limit_up_count']}\") for c in d['red_concepts']]"

# 看黄色概念（次强，两榜共有）
python -c "import json; d=json.load(open('/tmp/colors.json'))['data']; print('黄色:', d['counts']['yellow'], '个'); [print(f\"  {c['name']}: 涨停{c['limit_up_count']}\") for c in d['yellow_concepts']]"
---

## 7. 错误处理

**错误输出**：

```json
{
  "ok": false,
  "error": {
    "type": "ValueError",
    "message": "2024-10-05 无概念数据",
    "hint": ""
  }
}
```

**常见错误**：

| error.type | 原因 | 处理 |
|---|---|---|
| `ValueError` | 数据不存在（该日期无概念数据等） | 换日期 |
| `KeyError` | 指标名未注册 | 先 `list indicators` 查可用指标 |
| `SystemExit` | 参数错误（缺 `--start` 等） | 检查 argparse 错误提示 |
| `FileNotFoundError` | 输出路径父目录不存在 | 加 `--output` 时确认父目录存在 |

**AI 处理建议**：

- 看到 `ok: false` 时重试前先修正参数
- 看到 `type: SystemExit` 时检查必填参数
- 看到 `type: ValueError` 时换日期或换 code 重试
- 连续失败 3 次后报告用户

---

## 8. 数据字段速查

### 个股 K 线（`stock kline`）

```
trade_date, code, name, pct_change, industry,
open, high, low, close, volume, amount,
turnover_rate, hot_rank,
huge_order_net, big_order_net, medium_order_net, small_order_net,
ma5, ma10, ma20, ma30, ma60, ma120, ma250,
boll_mid, boll_up, boll_low,
main_net_3d, huge_order_net_1d, ...
```

实际字段取决于 `--indicators` 参数。

### 概念三榜（`concept top10`）

```json
{
  "market_total_amount": 1.55e12,
  "market_avg_5d": 1.6e12,
  "market_avg_20d": 1.62e12,
  "top_amount": [
    {
      "name": "存储芯片",
      "amount_pct": 12.14,
      "norm_amount_ratio": 2.55,
      "is_strong": true
    }
  ],
  "top_pct": [],
  "top_limit_up": [],
  "intersection_picks": [
    {
      "code": "603019",
      "name": "中科曙光",
      "match_count": 3,
      "matched_concepts": ["芯片", "AI", "算力"],
      "matched_concepts_detail": [
        {"name": "芯片", "gld": 5.0}
      ],
      "importance_index": 1.05,
      "importance_pct": 1.0,
      "rank_in_picks": 1,
      "total_in_picks": 40,
      "close": 42.5,
      "pct_change": 5.2
    }
  ]
}
```

### AMO 数据（`stock amo`）

```json
{
  "trade_date": "2024-10-09",
  "huge_buy": 2.49e8,
  "huge_sell": 2.31e8,
  "big_buy": 2.15e8,
  "big_sell": 2.46e8,
  "medium_buy": 4.63e8,
  "medium_sell": 4.68e8,
  "small_buy": 6.37e8,
  "small_sell": 6.20e8,
  "total_buy": 1.56e9,
  "total_sell": 1.56e9
}
```
### 未来涨幅字段（`fwd_5d` / `fwd_10d` / `fwd_20d` / `fwd_30d`）

**含义**：以查询日 `close` 为基准，之后第 N 个交易日的涨跌幅（百分比）。

**计算公式**：

    fwd_Nd = (close_{T+N} / close_T - 1) * 100

**可用范围**：

| 出现位置 | 说明 |
|---|---|
| `concept top10` → `intersection_picks[]` | 交集选股，每只股票都有 4 个字段 |
| `concept ladder` → `stocks[]` | 涨停天梯，每只股票都有 4 个字段 |
| `stock kline` → 每行数据 | （如已启用）个股 K 线，每行都有 4 个字段 |

**关键：`null` 语义**

未来涨幅依赖"未来数据"，**数据未到时返回 `null`，不能当作 0 处理**：

| 查询日期 | `fwd_5d` | `fwd_10d` | `fwd_20d` | `fwd_30d` |
|---|---|---|---|---|
| 距今 ≥ 30 交易日（如 2024-10-09） | 有值 | 有值 | 有值 | 有值 |
| 距今 10~20 交易日 | 有值 | 有值 | 可能 `null` | `null` |
| 距今 5~10 交易日 | 有值 | 可能 `null` | `null` | `null` |
| 距今 < 5 交易日（今天/昨天） | `null` | `null` | `null` | `null` |

**未来最高涨幅**：

| 字段 | 含义 |
|---|---|
| `fwd_max_5d` | 未来 5 日内最高价 vs 当日 close 的涨幅 |
| `fwd_max_10d` | 未来 10 日内最高价 vs 当日 close 的涨幅 |
| `fwd_max_20d` | 未来 20 日内最高价 vs 当日 close 的涨幅 |
| `fwd_max_30d` | 未来 30 日内最高价 vs 当日 close 的涨幅 |

- 起点 T+1，终点 T+N（不含当日）
- 恒有 `fwd_max_Nd >= fwd_Nd`
- 未来数据不足 N 天 → `null`
- 未来全跌 → 可能为负


**AI 处理规则**：

- ✅ 统计/排序时，先过滤 `fwd_* != null` 再算
- ❌ 不要用 `fwd_5d or 0`（把 null 当 0 会污染均值）
- ❌ 不要跨日期比较 fwd（不同查询日的"未来"窗口不同）


### 概念颜色分类（`concept colors`）

```json
{
  "counts": {"red": 2, "yellow": 3, "blue": 6},
  "red_concepts": [
    {"name": "芯片", "norm_amount_ratio": 2.55, "limit_up_count": 5,
     "rank_a": 1, "rank_p": null, "rank_l": 3}
  ],
  "yellow_concepts": [...],
  "blue_concepts": [...]
}

---

## 9. 性能提示

| 场景 | 耗时 |
|---|---|
| 单股 K 线（冷启动） | 约 300 ms |
| 单股 K 线（热缓存） | 小于 20 ms |
| 概念 top10（缓存命中） | 小于 20 ms |
| 概念 top10（缓存 miss） | 约 1.2 s |
| 批量 10 只 K 线 | 约 1 到 3 s |

**大范围查询**（如 662 天全量 K 线）建议分片：

- 按 code 分批（每批不超过 10 只）
- 或用 `--start` / `--end` 缩小范围

---

## 10. 给 AI 的建议

1. **先探路再查询**：不确定股票代码或指标名时，先跑 `list`
2. **优先 `--output`**：大输出（超过 500 行）写文件
3. **用 `--columns` 裁剪**：只要关心的字段，减少传输量
4. **批量用逗号**：一次查多 code，避免多次进程启动（每次约 200 ms 开销）
5. **时间用 `trading days` 对齐**：日期可能不是交易日，先查最近交易日
6. **`ok: false` 时不要盲目重试**：先看 `error.message`
7. **未来涨幅 `fwd_*` 的 null 必须显式过滤**：查询近期日期时 fwd_10d 及以上的字段常为 `null`（未来数据未到）。计算均值/排序前必须过滤 `null`，不得当作 0；详见第 8.4 节。
---

## 11. 快速索引

```bash
# 探路
python cli.py list codes --limit 100
python cli.py list indicators

# 单股（含未来涨幅）
python cli.py stock sectors 002693
python cli.py stock kline 002693 --start 2024-08-01 --end 2024-08-31 --indicators ma5,ma20 --columns trade_date,close,ma5,fwd_5d,fwd_10d,fwd_20d,fwd_30d --output /tmp/k.json

# 多股
python cli.py stock kline 002693,000001,600519 --start 2024-09-01 --end 2024-09-30 --output /tmp/kb.json

# 概念
python cli.py concept top10 2024-10-09 --output /tmp/top10.json
python cli.py concept ladder 2024-10-09
python cli.py concept limitup 880xxx 2024-10-09

# 关联度 / 入选原因
python cli.py concept gld 002693 芯片,AI
python cli.py concept ztnr 002693 芯片

# 未来涨幅（历史日期才有完整数据）
python cli.py concept top10 2024-10-09 --output /tmp/t.json
```