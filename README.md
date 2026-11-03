# PROJECT.md · stockDisplay v4

> 新对话开头先读 `PROJECT.md`，再动手。
> 本文档对应 2026-11 重构 + gld/ztnr 集成后的版本。

---

## 一、项目简介

A 股日行情 + 资金流指标可视化系统，服务于个人投研 / 板块轮动分析。

**三个核心能力**：

1. **个股页**（`index.html`）：K 线 + 3 个资金流指标 + AMO 买卖结构（可切换）+ 概念模式（左侧候选栏）
2. **概念页**（`concept.html`）：双界面
   - **界面 1**：归成比 TOP10 / 涨跌幅 TOP10 / 涨停数 TOP10 + 涨停天梯榜 + 概念交集选股 TOP40
   - **界面 2**：某概念下近 10 日涨停股候选 + 单股 K 线 + 3 指标
3. **每日 ETL**：Excel → Parquet，自动 cookie 更新，按月归档

**风格**：红涨绿跌，TradingView 暗色主题。

---

## 二、技术栈

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.11（conda env `stock`） | — |
| DuckDB | 1.0+ | 查询引擎 |
| Parquet | pyarrow 14+ | 列式存储 |
| FastAPI | 0.110+ | API 框架 |
| uvicorn | 0.27+ | ASGI 服务器 |
| Lightweight Charts | **v4.2.3**（CDN） | 前端图表 |
| akshare | 1.12+ | 交易日历 |
| eltdx | 最新 | K 线补全 + 概念元信息 |

**前端**：纯 HTML / CSS / JS，无构建工具。

---

## 三、目录结构

```
stockDisplay/
├── PROJECT.md                      本文档
├── requirements.txt
├── start.bat                       Windows 双击启动
│
├── config.py                       【必读】路径 + COOKIE + setup_logging()
├── indicators.py                   指标注册表（SQL / Python）
├── trading_calendar.py             交易日历（akshare + parquet 缓存）
├── main.py                         FastAPI 入口：所有 API 路由
├── serve.py                        【必读】启动器（~150 行）
├── xdget_daily.py                  兼容层（15 行）
│
├── core/                           ⭐ 数据层
│   ├── db.py                       DuckDB 连接 + 线程锁 + _q_df + glob 常量
│   ├── helpers.py                  纯函数 + gld 映射（_gld_weight）
│   ├── loaders.py                  带 lru_cache 的加载（pct/成分/alias/concept_meta）
│   ├── rebuild.py                  增量构建（market_amount/daily_pct/streak）
│   ├── picks_cache.py              交集榜 parquet 缓存
│   └── tops_cache.py               ⭐ _tops_cache（三榜 + concept_scores 持久化）
│
├── queries/                        ⭐ 查询层
│   ├── __init__.py                 re-export 全部对外函数
│   ├── stock.py                    个股 K 线 / sectors / date_range / missing_days
│   ├── sector.py                   行业/概念 K 线
│   ├── concept.py                  概念页：三榜 / 天梯 / 交集选股 / 涨停股
│   └── amo.py                      AMO 买卖结构
│
├── startup/                        ⭐ 启动逻辑
│   ├── cookie.py                   ASPSessionID 输入 + 写入 config
│   ├── integrity.py                启动时数据覆盖检查
│   ├── archive.py                  每月首次启动重建 compact
│   └── data_ready.py               按 09:15 / 15:30 时间窗判断是否拉数据
│
├── etl/                            数据管线
│   ├── field_mapping.py            【必读】列名映射 + ingest_xlsx()
│   ├── daily_update.py             增量 ETL（xlsx → parquet）
│   ├── etl_amo.py / etl_concept.py / etl_industry.py / etl_quote.py
│   ├── elt_stock_concept.py        个股概念 ETL
│   ├── compact.py                  月合并
│   ├── run_all.py                  批量 ETL
│   ├── registry.py                 ⭐ 数据类型注册表（DATA_TYPES）
│   ├── tdx_config.py               通达信客户端配置
│   ├── tdx_calendar.py             抓取用交易日历
│   ├── tdx_progress.py             断点续传
│   ├── tdx_client.py               TDXXiaoda HTTP 客户端
│   └── tdx_fetch.py                抓取主流程
│
├── scripts/                        一次性 / 批量脚本
│   ├── precompute.py               【兼容】批量预生成（旧流程）
│   ├── rebuild_tops_cache.py       ⭐ 重建 _tops_cache
│   ├── recompute_picks.py          ⭐ 只重算 picks（1~3 分钟）
│   ├── archive_month.py            按月归档 data → data_compact
│   ├── build_concept_alias.py      生成概念别名映射表
│   ├── clean_concept_date_nontrading.py  ⭐ 清理 concept_date 非交易日
│   ├── check_backup.py             数据完整性检查
│   ├── fetch_history.py            历史数据批量抓取
│   ├── backfill_kline.py           ⭐ eltdx 补全缺失 K 线
│   ├── detect_missing_kline.py     ⭐ 检测缺失 K 线
│   ├── recompute_target.py         单日全量重算（调试用）
│   └── test_duckdb.py              调试查询
│
├── data/                           ETL 输出（日分区，全保留）
├── data_compact/                   按月合并（查询加速）
│   ├── quote/YYYY-MM.parquet
│   ├── concept_flow/YYYY-MM.parquet
│   ├── industry_flow/YYYY-MM.parquet
│   ├── concept_date/YYYY-MM.parquet
│   ├── daily_pct/YYYY-MM.parquet
│   └── concept_picks/YYYY-MM.parquet
├── data/_tops_cache/v1/            ⭐ 三榜缓存
│   ├── bars.parquet
│   └── meta.parquet
├── data/concept_meta.parquet       ⭐ gld + ztnr 独立表
├── raw_data/                       原始 xlsx
├── map/                            概念/行业映射快照
├── logs/etl.log
├── frontend/                       纯静态
│   ├── index.html / concept.html
│   ├── css/{style,concept}.css
│   └── js/{config,chart,load_data,drawline,boot,
│           concept_view1,concept_view2,concept_boot}.js
├── docs/etl_extension.md           ETL 扩展指南
├── PROJECT/                        历史版本文档
└── 抓取tdx历史个股概念/            独立工具集（step1/step2 等）
```

---

## 四、依赖图（单向无环）

```
config.py                        ← 无内部依赖
indicators.py                    ← pandas, numpy
trading_calendar.py              ← akshare, config

core/db.py                       ← config
core/helpers.py                  ← core.db, indicators
core/loaders.py                  ← config, core.db
core/rebuild.py                  ← config, core.db, core.helpers, core.loaders
core/picks_cache.py              ← config, core.db
core/tops_cache.py               ← config, core.db, queries.concept（延迟 import）

queries/stock.py                 ← core.*, indicators, trading_calendar
queries/sector.py                ← core.*, queries.stock
queries/concept.py               ← core.*, core.tops_cache
queries/amo.py                   ← core.db

startup/*                        ← config, core.*, etl.daily_update, queries
etl/tdx_*                        ← config, etl.field_mapping, trading_calendar

serve.py                         ← startup.*, etl.daily_update, core.*, queries
main.py                          ← queries, config
scripts/*.py                     ← 任意（顶层脚本）
```

**关键规则**：
- `core/` **不能** import `queries/`
- `queries/__init__.py` **re-export** 所有对外函数
- `core/tops_cache.py` 对 `queries.concept` 的 import 是**延迟 import**（函数内部），避免循环依赖

---

## 五、快速上手

### 启动

```bat
conda activate stock
python serve.py                     # 完整启动
python serve.py --skip-etl          # 只启动服务
python serve.py --port 8001         # 换端口
python serve.py --no-reload         # 生产模式
```

**浏览器**：
- 个股页：`http://127.0.0.1:8001/`
- 概念页：`http://127.0.0.1:8001/concept.html`
- API 文档：`http://127.0.0.1:8001/docs`

### 常用命令

```bat
rem ⭐ 重建 _tops_cache（三榜 + concept_scores，6~8 分钟）
python scripts\rebuild_tops_cache.py
python scripts\rebuild_tops_cache.py --force
python scripts\rebuild_tops_cache.py --since 2025-01-01

rem ⭐ 只重算 picks（1~3 分钟，最常用）
python scripts\recompute_picks.py --since 2025-01-01
python scripts\recompute_picks.py --date 2024-10-09
python scripts\recompute_picks.py --last 30

rem 按月归档
python scripts\archive_month.py
python scripts\archive_month.py --rebuild-current

rem 清理非交易日（concept_date）
python scripts\clean_concept_date_nontrading.py --dry-run
python scripts\clean_concept_date_nontrading.py --yes

rem 补全缺失 K 线（eltdx）
python scripts\detect_missing_kline.py
python scripts\backfill_kline.py --only-main
python scripts\backfill_kline.py --only-bj --workers 12

rem 数据完整性检查
python scripts\check_backup.py
python scripts\check_backup.py --verify-pct
python scripts\check_backup.py --fix-calendar
```

---

## 六、缓存体系（三层）

```
┌──────────────────────────────────────────────────────────┐
│ 数据源（ETL 输出）                                       │
│  data/{kind}/trade_date=YYYY-MM-DD/part.parquet          │
└──────────────────────┬───────────────────────────────────┘
                       │ 增量重建
                       ↓
┌──────────────────────────────────────────────────────────┐
│ 月合并（查询加速）                                       │
│  data_compact/{kind}/YYYY-MM.parquet                     │
│  （startup/archive.py 或 scripts/archive_month.py 生成） │
└──────────────────────┬───────────────────────────────────┘
                       │ 首次 / 榜单公式变更
                       ↓
┌──────────────────────────────────────────────────────────┐
│ 三榜缓存（概念页用）                                     │
│  data/_tops_cache/v1/bars.parquet  ← 三榜原始数据        │
│  data/_tops_cache/v1/meta.parquet  ← concept_scores      │
│  （scripts/rebuild_tops_cache.py 生成）                  │
└──────────────────────┬───────────────────────────────────┘
                       │ picks 公式变更 / 每日增量
                       ↓
┌──────────────────────────────────────────────────────────┐
│ 交集榜缓存                                               │
│  data/concept_picks/trade_date=YYYY-MM-DD/part.parquet   │
│  （scripts/recompute_picks.py 或 serve.py 增量）         │
└──────────────────────────────────────────────────────────┘
```

**独立表**：

```
data/concept_meta.parquet
  code, topic_name, selected_date, gld, ztnr
  （step2_expand.py 生成，全局一份，不随交易日膨胀）

data/market_amount.parquet
  trade_date, total_amount
  （rebuild_market_amount 增量生成）

data/daily_pct/trade_date=YYYY-MM-DD/part.parquet
  code, trade_date, pct_change（前复权自算）
```

### 使用场景对照

| 场景 | 命令 | 耗时 |
|---|---|---|
| **改 picks 公式**（gld 权重、importance 权重） | `recompute_picks.py --since X` | 1~3 min |
| **改榜单公式**（归成比、涨停数榜逻辑） | `rebuild_tops_cache.py --force` + `recompute_picks.py` | 10~12 min |
| **数据源修正**（concept_flow 重跑某天） | `invalidate_tops_cache(day)` + 重算 | 秒级/天 |
| **每日增量** | `serve.py` 自动 | 0.6s/天 |
| **单日调试** | `recompute_picks.py --date YYYY-MM-DD` | 0.3s |

---

## 七、核心业务逻辑

### 1. 涨跌幅口径

**统一用 `daily_pct`**（前复权 close 自算）：

```
pct_change = (close_T / close_{T-1} - 1) × 100
```

- `T-1` 缺失（停牌/新股）→ NaN
- **禁用** quote 表原始 `pct_change`（东财口径，除权日不一致）

### 2. 涨停阈值

```python
def _limit_up_threshold(code: str) -> float:
    p = str(code)[:2]
    if p in ("30", "68"):       return 19.8   # 创业板 / 科创
    if p in ("8", "4", "92"):   return 29.8   # 北交所
    return 9.8                                 # 主板
```

### 3. 归成比（成交额榜新口径）

```
A = 概念成交额 / 市场总成交额 × 100%
B = 概念自由流通市值 / 市场总流通市值 × 100%
归成比 = A / B
```

- **市场总流通市值**：`industry_flow` 当日 `SUM(total_market_cap)`（约 3932 万亿）
- **强概念**：`A > 10% AND 归成比 > 1.1`
- **榜单逻辑**：
  - 强概念 ≥ 10 个 → 强概念按归成比降序取 TOP10
  - 强概念 < 10 个 → 强概念优先，剩余按归成比补齐（补齐的 `is_strong=false`）

### 4. 概念名两套命名

| 命名 | 来源 | 示例 |
|---|---|---|
| **cf_name** | concept_flow / concept_date | "消费电子概念"、"DeepSeek概念" |
| **map_name** | stock_sector_map | "消费电子"、"DeepSeek" |

**别名表**：`data/concept_alias.parquet`（269 条）

**按日成分查询**：`core/loaders.py::_load_stock_index_full(trade_date)`

查找链：
1. 当日 `concept_date` 分区
2. 当月 `concept_date` compact
3. ⭐ 往前找最近 5 个交易日的 concept_date（新概念自愈）
4. map + alias 反向（极老数据兜底）

### 5. gld / ztnr（关联度 + 入选原因）

**数据源**：eltdx `F10Client.hot_topics(code, section='zttzbkz')`

**存储**：`data/concept_meta.parquet`

```
code, topic_name, selected_date, gld, ztnr
```

- 同一 `(code, topic_name)` 只保留**最早入选日**那条
- **gld 分级**：1（弱）~ 5（强）
- **ztnr**：自然语言入选原因（~100 字）

**查询接口**：

```python
from core.loaders import get_concept_gld, get_concept_ztnr

gld = get_concept_gld('000001', '跨境支付CIPS')   # → 3.0
ztnr = get_concept_ztnr('000001', '跨境支付CIPS') # → "公司是..."
```

**gld 加权映射**（`core/helpers.py::_gld_weight`）：

| gld | 权重 |
|---|---|
| 5（强） | 0.8 |
| 4（较强） | 0.7 |
| 3（一般） | 0.5 |
| 2（弱） | 0.3 |
| 1（很弱） | 0.2 |

非线性映射，缩小 4/5 和 1/2 的差距。

### 6. 概念重要性

```
concept_score = (0.40 × 归成比分 + 0.35 × 涨停数分 + 0.25 × 涨跌幅分)
              × 多榜命中加成（1榜=1.0, 2榜=1.2, 3榜=1.4）
              → 归一化 max=1.0 → ^0.8 拉开中低部

stock_importance = mean(concept_score × gld_weight) × n^0.4
                 × 龙头加成（命中 score>=0.8 概念 → ×1.2）

importance_pct = stock_importance / max(stock_importance)  （当日相对强度）
```

**交集榜**：按 `importance_index` 降序 **TOP40**

**前端颜色分档**：用 `importance_pct`（相对分档，跨日期可比）
- ≥ 0.85 → 金
- ≥ 0.60 → 蓝
- else → 灰

### 7. 三榜颜色规则（前端）

| 颜色 | 条件 |
|---|---|
| 🔴 红 | **归成比榜前 5**（只含 `is_strong`）**且**（涨跌幅榜前 5 **或** 涨停数榜前 5） |
| 🟡 黄 | **任意两榜共有**（不满足红） |
| 🔵 蓝 | 某**单榜前 5**（不满足红黄） |
| ⚪ 白 | 其它 |

**`v1-row-weak`（补齐的）**：强制白色 + "补"标记，不参与任何颜色判断

### 8. 归一化值（净额）

```
norm = net × turnover_rate / amount
```

用 canvas overlay 层（`#chartOverlay` / `#v2ChartOverlay`）标注在柱状图上：
- **累计 N 日指标**：用 `r[indName]`（累计值本身）
- **阈值筛选**：`|norm| < 阈值` 不显示
- ⚠️ `createChart(el)` 会**清空 el 内容** → overlay canvas 必须是 el 的**兄弟节点**

### 9. AMO 买卖结构

**指标定义**（左买右卖，都从 0 向上）：
- **主买**：超大 / 大 / 中 / 小四类堆叠（红 / 橙 / 金 / 紫）
- **主卖**：同四类堆叠（蓝紫 / 蓝 / 青 / 浅青）
- **净主买折线**：`(总买 - 总卖) / (总买 + 总卖) × 100`（范围 -100 ~ +100）

**两种模式**：
- `ratio`（占比）：柱高 = `单类 / 总成交 × 100`（最大 100）
- `amount`（金额）：柱高 = 亿元

**布局**：K 线缩到 42%，AMO 占 58%

---

## 八、API 清单

### 个股

```
GET /api/codes
GET /api/indicators
GET /api/indicator_groups
GET /api/stock/{code}?start=&end=&indicators=a,b
GET /api/stock/{code}/date_range
GET /api/stock/{code}/sectors
GET /api/stock/{code}/industry_chart
GET /api/sector/{kind}/{code}
GET /api/amo/chart?code=&start=&end=
```

### 概念

```
GET /api/concept/date_top10?trade_date=
    → {market_total_amount, market_avg_5d, market_avg_20d,
       top_amount:[归成比榜，含 is_strong],
       top_pct, top_limit_up,
       intersection_picks:[{code,name,match_count,matched_concepts,
                            matched_concepts_detail:[{name,gld}],
                            importance_index, importance_pct,
                            rank_in_picks, total_in_picks,
                            close, pct_change,...}],
       picks_cached, concept_colors}

GET /api/concept/limit_up_ladder?trade_date=&min_days=2
    → {stocks:[{code,name,streak_days,streak_label,concepts,
                matched_cfs_detail:[{name,gld}],
                importance_index, importance_pct,...}],
       cached}

GET /api/concept/limit_up_stocks?concept_code=&trade_date=&window_days=10
```

### 交易日

```
GET /api/trading_days_around?date=&before=&after=
GET /api/trading_day_neighbor?date=&direction=prev|next
```

---

## 九、启动流程（`serve.py`）

### 时间窗判断

| 时间 | 动作 |
|---|---|
| `< 09:15` | 允许补**上一交易日**数据（需 ASPSessionID） |
| `09:15 ~ 15:30` | 拒绝补上一日；跳过当日（盘中） |
| `>= 15:30` | 更新**当日**数据（今日须是交易日） |

### 启动顺序

```
1. ensure_data_ready()            时间窗判断 → 拉数据（可选）
2. ETL                            xlsx → data/
3. check_data_integrity()         校验数据覆盖
4. rebuild_market_amount()        data/industry_flow → market_amount.parquet
5. rebuild_daily_pct()            data/quote → data/daily_pct/
6. rebuild_limit_up_streak()      data/daily_pct → data/limit_up_streak/
7. maybe_rebuild_month_archive()  data/ → data_compact/（mtime 检测）
8. ⭐ 增量补 _tops_cache          缺失日期 rebuild_tops_cache
9. 补最新 1 天 picks              data_compact/ → concept_picks/
10. uvicorn.run(...)              reload_excludes=["data/*","data_compact/*",...]
```

⚠️ **`reload_excludes` 必须排除 data/**，否则写 parquet 会触发 uvicorn 重启。

---

## 十、指标注册

### SQL 指标

```python
register_sql("main_net", "huge_order_net + big_order_net",
             "主力净额", scope="all")
register_sql("hot_rank", "hot_rank", "热度", scope="stock")
```

**累计净额族**：`_CUMULATIVE_DAYS = [1,3,5,10,20,30,60]` × 5 家族 = 35 个

**均线/BOLL**：ma5~250、boll_mid/up/low

**scope**：`all`（默认）/ `stock` / `industry`

### Python 指标

```python
def _macd(df, ...): ...
register_python("macd", _macd, "MACD")
```

---

## 十一、前端约定

### 配色

- 背景 `#131722`，面板 `#1e222d`，边框 `#2a2e39`
- **涨 `#ef5350`，跌 `#26a69a`**

### Pane 布局

**个股页（4 pane）**：
```js
const PANES = {
  price: { top: 0.00, bottom: 0.55 },
  ind1:  { top: 0.47, bottom: 0.37 },
  ind2:  { top: 0.65, bottom: 0.19 },
  ind3:  { top: 0.83, bottom: 0.01 },
};
```

**AMO 模式**（K 线 42% + AMO 58%）：
```js
const AMO_PANES = {
  price: { top: 0.02, bottom: 0.60 },
  amo:   { top: 0.45, bottom: 0.02 },
};
```

### 红绿柱状

`HISTOGRAM_REDGREEN` + `CUM_NET_RE` 正则
- `volume` 按 `close >= open` 着色
- 其它按 `v >= 0`

### 数据一次性拉齐

首屏拉全部指标，缓存在 `state.rows`；切下拉零网络请求。

### localStorage（概念页）

记忆：`v1Date / v2Code / v2Date / v2Start / v2End / 3 个指标 / tab`

### 概念模式（index.html）

- 左侧 `#conceptSidebar`（220px）
- 右侧 `#chartArea`（flex 自适应）
- 概念模式通过 `#chartWrap.concept-mode` class 切换

---

## 十二、已踩过的坑

### 后端

1. **DuckDB 单连接非线程安全** → 所有查询走 `_q_df` / `_q_fetchone`（带 `_con_lock`）
2. **`hive_partitioning` 不能混读** compact（普通 parquet）+ data（hive 分区）→ Binder Error
3. **`union_by_name=true`**：跨分区读 quote 必加
4. **`iterrows` 遍历百万行慢 50~100 倍** → 向量化（`pivot` + `numpy`）
5. **`pct_change = inf`**（`prev_close=0`）会崩 JSON → `_json_safe` 或 `math.isfinite` 兜底
6. **`rebuild_daily_pct` 时序问题**：分区乱序写入 → `force_recent_days=10` 兜底
7. **`rebuild_market_amount` 不写 0 值**：data/ 缺分区时跳过
8. **FastAPI 路由顺序**：`/api/stock/{code}/date_range` 必须在 `/api/stock/{code}` 之前
9. **`reload_excludes` 漏 data/** → uvicorn 频繁重启 → `lru_cache` 清空
10. **`_tops_cache` 读回时不能按 code 排序**，必须按 `rank` 排序
11. **DuckDB 新版移除 `FILE_NAME_PATTERN`**：`COPY TO ... (FORMAT PARQUET, PARTITION_BY ..., OVERWRITE_OR_IGNORE, COMPRESSION ...)` 不带该参数
12. **`concept_date` 会包含非交易日**：`scripts/clean_concept_date_nontrading.py` 清理
13. **多进程同时跑会锁冲突**：批量重算前必须关 serve

### 前端

14. **`createChart(el)` 清空 el 内容** → overlay canvas 必须是 el 的兄弟节点
15. **`Number(null) === 0`** → 过滤 OHLC 显式检查 `!= null` 且 `> 0`
16. **lightweight-charts `Value is null`** → 数据有 `open: null` / `open: 0` / 非 `YYYY-MM-DD` time
17. **`setData` 前必须去重 + 排序**
18. **`setMarkers` 的 time 必须在 data 里存在**
19. **`.view-panel` 控制 display** → `#view1` / `#view2` 不要写 `display: flex`
20. **事件绑定用 `document` 委托** → 防止按钮重绘后绑定丢失
21. **概念 tag 的 hover 颜色优先用 `!important`** 压过 id 选择器
22. **`#legend` / `#missingWarning` 在概念模式下要放 `#chartArea` 内**（否则被侧栏遮挡）

### 口径一致性

23. **`pct_change` 统一走 `daily_pct`**，不用 quote 原始值
24. **概念名 key 统一用 cf_name**
25. **累计 N 日指标的归一化用 `r[indName]`**（累计值本身）
26. **gld 用非线性映射**（5→0.8, 4→0.7, 3→0.5, 2→0.3, 1→0.2），不要简单 `gld/5`
27. **归成比榜的 `is_strong` = A>10% AND 归成比>1.1**，补齐的 `is_strong=false`，不参与颜色判断

---

## 十三、调试速查

### 数据完整性

```bat
python scripts\check_backup.py
python scripts\check_backup.py --verify-pct
python scripts\check_backup.py --fix-calendar
```

### 缓存状态

```bat
python -c "
from core.tops_cache import cache_stats
print(cache_stats())
"
```

**预期**：
```
{'days': 662, 'bars_rows': 19860, 'scores_days': 662, 'market_days': 662,
 'bars_size_mb': 2.1, 'meta_size_mb': 3.5}
```

### 性能验证

```bat
python -c "
import time
from core.tops_cache import get_tops_and_scores_cached

d = '2024-10-09'
t = time.time(); r = get_tops_and_scores_cached(d)
print(f'缓存命中: {(time.time()-t)*1000:.1f}ms')
"
```

**预期**：< 1ms（缓存命中）/ 620ms（缓存 miss）

### gld 验证

```bat
python -c "
from core.loaders import get_concept_gld, get_concept_ztnr
from core.helpers import _gld_weight

print('gld → weight 映射:')
for g in [1, 2, 3, 4, 5]:
    print(f'  {g}: {_gld_weight(g):.2f}')

# 抽样
print()
print('gld 样例:')
for code, cname in [('000001', '跨境支付CIPS'), ('000002', '体育概念')]:
    print(f'  {code} {cname}: gld={get_concept_gld(code, cname)}, ztnr={get_concept_ztnr(code, cname)[:30]}')
"
```

### 单日重建（调试）

```bat
python scripts\recompute_target.py 2026-10-30
```

### 概念名对齐

```bat
python -c "
from core.loaders import _load_stock_index_by_date
info, cf2codes = _load_stock_index_by_date('2026-10-30')
print(f'{len(info)} 只股票，{len(cf2codes)} 个概念')
print(f'存储芯片: {len(cf2codes.get(\"存储芯片\", []))} 只')
"
```

---

## 十四、当前状态

- **数据**：2024-02-01 ~ 2026-11-02（660+ 交易日 × 5400+ 只股票 × 250+ 概念）
- **双结构**：data/（日分区全保留）+ data_compact/（按月合并查询加速）
- **三榜缓存**：`data/_tops_cache/v1/`（662 天，bars + meta）
- **概念元信息**：`data/concept_meta.parquet`（47043 条，gld + ztnr 100% 覆盖）
- **概念别名表**：269 条，none=0
- **概念交集榜**：TOP40，gld 加权重要性 + 归一化相对强度
- **归成比榜**：强概念（A>10% AND 归成比>1.1）优先 + 补齐
- **AMO 买卖结构**：左右并排柱 + 净线（±100），ratio / amount 双模式
- **性能**：
  - 冷启动（重启后首次查某日期）~1.2s
  - 热缓存 < 20ms
  - `recompute_picks` 全量 ~3 分钟，`--since` 更快

**重构状态**：4 阶段全部完成
- ✅ 阶段 1：`scripts/` 归位
- ✅ 阶段 2：`queries.py` → `core/` + `queries/`
- ✅ 阶段 3：`serve.py` → `startup/`
- ✅ 阶段 4：`xdget_daily.py` → `etl/tdx_*.py` + 兼容层

**gld/ztnr 集成状态**：
- ✅ 阶段 1+2：抓取 gld/ztnr + `concept_meta` 独立表
- ✅ 阶段 3：gld 加权重要性 + `importance_pct` 相对强度
- ⏸ 阶段 4（未做）：前端 tooltip 展示 ztnr 入选原因

---

## 十五、下一步候选

- **阶段 4 · ztnr 展示**：交集榜/天梯榜概念 tag hover 显示入选原因
- **天梯榜基础分**：没命中三榜的连板股给基础重要性分
- **多股同列对比**
- **Python 指标（MACD 等）进前端**
- **Parquet 小文件合并**（数据到 2000+ 天）
- **单元测试**（`core/rebuild` 向量化函数、`core/tops_cache` 序列化）
- **`_tops_cache` 版本管理**（榜单公式改时自动失效）
- **K 线缺口自动检测**（每日 ETL 后跑 `detect_missing_kline`）

---

## 十六、快速命令速查表

```bat
rem ═══ 启动 ═══
python serve.py --skip-etl

rem ═══ 只改 picks 公式（1~3 分钟）═══
python scripts\recompute_picks.py --since 2025-01-01

rem ═══ 改榜单公式（10~12 分钟）═══
python scripts\rebuild_tops_cache.py --force
python scripts\recompute_picks.py

rem ═══ 单日调试（0.3 秒）═══
python scripts\recompute_picks.py --date 2024-10-09

rem ═══ 数据完整性 ═══
python scripts\check_backup.py

rem ═══ 补 K 线缺口 ═══
python scripts\detect_missing_kline.py
python scripts\backfill_kline.py --only-main

rem ═══ 按月归档 ═══
python scripts\archive_month.py
```

---

## 十七、历史文档

`PROJECT/` 目录保留 `PROJECT v2.md` / `PROJECT v3.md` / `PROJECT v4.md`。

**新对话以本文件（`PROJECT.md`）为准。**

---

## 十八、附录 · gld/ztnr 数据链

```
eltdx F10Client.hot_topics(code, section='zttzbkz')
    ↓ 返回 rows: [{id, ztmc, rxsj, gld, ztnr}, ...]
    ↓
step1_fetch.py
    ↓ raw_topics.parquet: code, topic_id, topic_name, selected_date, gld, ztnr
    ↓
step1_5_filter.py（按 concept_flow 白名单过滤）
    ↓ raw_topics.parquet（过滤后）
    ↓
step2_expand.py
    ├─→ data/concept_date/trade_date=xxx/part.parquet
    │    code, concepts = "芯片;AI;CPO概念"（旧格式，不含 gld）
    │
    └─→ data/concept_meta.parquet
         code, topic_name, selected_date, gld, ztnr
         （同一 code+topic 只保留最早入选日）

查询时：
    _load_stock_index_by_date(day) → code → frozenset(概念)
    get_concept_gld(code, concept) → gld（O(1) 查字典）
    get_concept_ztnr(code, concept) → ztnr（O(1) 查字典）
```

**关键设计**：`concept_date` 保持旧格式（**不装 gld/ztnr**），`gld`/`ztnr` 独立存 `concept_meta.parquet`，避免每次查日期都要读大文件。