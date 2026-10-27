// ─────────── 界面 2 主逻辑 ───────────
// ─── 指标级联下拉 ───

const v2IndState = {
  indicators: [],
  cumulativeGroups: [],
};

const v2IndCurrent = {
  ind1: null, ind2: null, ind3: null,
};

const v2IndDds = {
  ind1: null, ind2: null, ind3: null,
};

function v2BuildGroupsForDropdown() {
  const groups = [];
  for (const g of v2IndState.cumulativeGroups) {
    const items = [];
    for (const d of g.days) {
      const value = `${g.key}_${d}d`;
      const meta = v2IndState.indicators.find(i => i.name === value);
      items.push({
        value,
        label:     `${d}日`,
        fullLabel: meta ? meta.description : `${d}日`,
      });
    }
    groups.push({ type: "cascade", label: g.label, items });
  }

  const cascadeValues = new Set();
  for (const g of groups) {
    if (g.type === "cascade") for (const it of g.items) cascadeValues.add(it.value);
  }

  // 界面 2 是个股 → 只列 scope=all / stock 的指标
  for (const it of v2IndState.indicators) {
    if (OVERLAY_ONLY.has(it.name))   continue;
    if (cascadeValues.has(it.name))  continue;
    const s = it.scope || "all";
    if (s !== "all" && s !== "stock") continue;
    groups.push({ type: "flat", value: it.name, label: it.description });
  }
  return groups;
}

function v2FindLabel(value) {
  for (const g of v2BuildGroupsForDropdown()) {
    if (g.type === "flat" && g.value === value) return g.label;
    if (g.type === "cascade") {
      for (const it of g.items) if (it.value === value) return it.fullLabel;
    }
  }
  return value;
}

function initV2CascadeDropdown(root, getGroups, onSelect) {
  const trigger = root.querySelector(".dd-trigger");
  const labelEl = root.querySelector(".dd-label");
  const panel   = root.querySelector(".dd-panel");

  let rendered = false;

  function render() {
    panel.innerHTML = "";
    const ul = document.createElement("ul");
    ul.className = "dd-list";

    for (const g of getGroups()) {
      const li = document.createElement("li");
      li.className = "dd-item";

      if (g.type === "cascade") {
        li.classList.add("has-sub");
        const span = document.createElement("span");
        span.textContent = g.label;
        li.appendChild(span);

        const arrow = document.createElement("span");
        arrow.className = "dd-arrow-right";
        arrow.textContent = "›";
        li.appendChild(arrow);

        const sub = document.createElement("ul");
        sub.className = "dd-submenu";
        for (const it of g.items) {
          const subLi = document.createElement("li");
          subLi.className = "dd-subitem";
          subLi.textContent = it.label;
          subLi.title = it.fullLabel;
          subLi.addEventListener("click", (e) => {
            e.stopPropagation();
            select(it.value, it.fullLabel);
          });
          sub.appendChild(subLi);
        }
        li.appendChild(sub);
      } else {
        li.textContent = g.label;
        li.addEventListener("click", (e) => {
          e.stopPropagation();
          select(g.value, g.label);
        });
      }

      ul.appendChild(li);
    }
    panel.appendChild(ul);
    rendered = true;
  }

  function select(value, label) {
    labelEl.textContent = label;
    onSelect(value, label);
    // panel 由 CSS 的 :hover 控制，无需手动关闭
  }

  // 鼠标进入时：先判断翻转方向，再确保内容已渲染
  root.addEventListener("mouseenter", () => {
    const rect = trigger.getBoundingClientRect();
    root.classList.toggle(
      "flip-sub", rect.left + rect.width + 220 > window.innerWidth);
    if (!rendered) render();
  });

  // 防止点击按钮时冒泡触发其它监听
  trigger.addEventListener("click", (e) => e.stopPropagation());

  return {
    setValue(value, label) { labelEl.textContent = label; },
    refresh() { rendered = false; if (root.matches(":hover")) render(); },
  };
}

function initV2IndicatorDropdowns() {
  const defaults = {
    ind1: "huge_order_net_1d",
    ind2: "medium_order_net_1d",
    ind3: "small_order_net_1d",
  };

  for (const key of ["ind1", "ind2", "ind3"]) {
    const n = key.slice(-1);
    const root = document.getElementById(`v2IndDd${n}`);
    const dd = initV2CascadeDropdown(
      root,
      v2BuildGroupsForDropdown,
      (value) => {
        v2IndCurrent[key] = value;
        if (v2State.code) v2LoadStock(v2State.code, v2State.name);
      },
    );
    v2IndDds[key] = dd;
    v2IndCurrent[key] = defaults[key];
    dd.setValue(defaults[key], v2FindLabel(defaults[key]));
  }
}
// ─── 列定义 ───
const V2_COLUMNS = [
  { key: "code",             label: "代码", w: 56, align: "left"  },
  { key: "name",             label: "名称", w: 76, align: "left"  },
  { key: "pct_change",       label: "涨幅", w: 58, align: "right" },
  { key: "close",            label: "收盘", w: 60, align: "right" },
  { key: "turnover_rate",    label: "换手", w: 48, align: "right" },
  { key: "hot_rank",         label: "热度", w: 42, align: "right" },
  { key: "huge_order_net",   label: "超大", w: 60, align: "right" },
  { key: "big_order_net",    label: "大",   w: 60, align: "right" },
  { key: "medium_order_net", label: "中",   w: 60, align: "right" },
  { key: "small_order_net",  label: "小",   w: 60, align: "right" },
];

const v2ListState = {
  expanded: false,
  prevWidth: null,
  sortKey: "pct_change",
  sortDir: "desc",
};

function v2GridTemplate() {
  return V2_COLUMNS.map(c => `${c.w}px`).join(" ");
}

function v2FmtNum(v, d = 2) {
  return v == null ? "-" : v.toFixed(d);
}
function v2FmtPct(v) {
  return v == null ? "-" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}
function v2FmtBig(v) {
  if (v == null) return "-";
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(0);
}
function v2Cls(v) {
  return v == null ? "" : (v >= 0 ? "up" : "down");
}

function v2SortStocks(stocks) {
  const { key, dir } = v2ListState;
  const arr = stocks.slice();
  arr.sort((a, b) => {
    const va = a[key], vb = b[key];
    const an = va == null || (typeof va === "number" && Number.isNaN(va));
    const bn = vb == null || (typeof vb === "number" && Number.isNaN(vb));
    if (an && bn) return String(a.code).localeCompare(String(b.code));
    if (an) return 1;
    if (bn) return -1;
    let cmp;
    if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
    else cmp = String(va).localeCompare(String(vb), "zh");
    return dir === "desc" ? -cmp : cmp;
  });
  return arr;
}

const v2State = {
  chart: null,
  candle: null,
  panes: {
    ind1: { series: null, name: null },
    ind2: { series: null, name: null },
    ind3: { series: null, name: null },
  },
  rows: [],
  code: null,
  name: null,
  resizeObs: null,
  allStocks: [],     // ← 新增：未过滤的全量候选
  filterOn: true,    // ← 新增：是否自动加载第一只
  conceptName: "",
};

const v2El = {
  chartEl:     document.getElementById("v2Chart"),
  title:       document.getElementById("v2ChartTitle"),
  stockList:   document.getElementById("v2StockList"),
  sidebarHead: document.getElementById("v2SidebarHeader"),
  codeInput:   document.getElementById("v2Code"),
  dateInput:   document.getElementById("v2Date"),
  startInput:  document.getElementById("v2Start"),
  endInput:    document.getElementById("v2End"),
  queryBtn:    document.getElementById("v2Query"),
};

const V2_PANES = {
  price: { top: 0.00, bottom: 0.55 },   // 高度 ~0.45
  ind1:  { top: 0.47, bottom: 0.37 },   // 高度 ~0.16
  ind2:  { top: 0.65, bottom: 0.19 },   // 高度 ~0.16
  ind3:  { top: 0.83, bottom: 0.01 },   // 高度 ~0.16
};

function v2SetStatus(m, err) {
  const el = document.getElementById("status");
  el.textContent = m || "";
  el.classList.toggle("error", !!err);
}

// ─── 图表生命周期 ───

function v2DestroyChart() {
  if (v2State.resizeObs) {
    v2State.resizeObs.disconnect();
    v2State.resizeObs = null;
  }
  if (v2State.chart) {
    try { v2State.chart.remove(); } catch (e) {}
    v2State.chart = null;
  }
  v2State.candle = null;
  v2State.panes = {
    ind1: { series: null, name: null },
    ind2: { series: null, name: null },
    ind3: { series: null, name: null },
  };
  v2El.chartEl.innerHTML = "";
}

function v2CreateChart() {
  v2DestroyChart();
  const chart = LightweightCharts.createChart(v2El.chartEl, {
    layout: {
      background: { color: COLORS.bg },
      textColor:  COLORS.text,
      fontSize:   11,
    },
    grid: {
      vertLines: { color: COLORS.grid },
      horzLines: { color: COLORS.grid },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: {
      borderColor:  COLORS.border,
      scaleMargins: V2_PANES.price,
      minimumWidth: 58,
    },
    timeScale: { borderColor: COLORS.border },
    localization: { locale: "zh-CN" },
  });
  v2State.chart = chart;

  v2State.candle = chart.addCandlestickSeries({
    upColor: COLORS.up, downColor: COLORS.down,
    borderUpColor: COLORS.up, borderDownColor: COLORS.down,
    wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    priceLineVisible: false, lastValueVisible: false,
  });

  for (const k of ["ind1", "ind2", "ind3"]) {
    chart.priceScale(k).applyOptions({
      scaleMargins: V2_PANES[k],
      visible: false,
    });
  }
    // 缩放/滚动时重绘归一化标注
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
    v2ScheduleDrawNorm(80);
  });

  const obs = new ResizeObserver(() => {
    chart.applyOptions({
      width:  v2El.chartEl.clientWidth,
      height: v2El.chartEl.clientHeight,
    });
    v2ScheduleDrawNorm(120);
  });
  obs.observe(v2El.chartEl);
  v2State.resizeObs = obs;
}

function v2SetPane(paneKey, name) {
  const chart = v2State.chart;
  if (!chart) return;
  const ref = v2State.panes[paneKey];

  if (ref.series) {
    try { chart.removeSeries(ref.series); } catch (e) {}
    ref.series = null;
  }
  if (!name) { ref.name = null; return; }

  const isHist = isHistogramRedGreen(name);
  const series = isHist
    ? chart.addHistogramSeries({
        priceScaleId: paneKey,
        priceLineVisible: false, lastValueVisible: true,
      })
    : chart.addLineSeries({
        priceScaleId: paneKey,
        color: COLORS.line, lineWidth: 1.5,
        priceLineVisible: false, lastValueVisible: true,
      });

  chart.priceScale(paneKey).applyOptions({
    scaleMargins: V2_PANES[paneKey],
    visible: false,
  });

  const data = v2State.rows.map(r => {
    const v = r[name];
    if (v == null) return null;
    if (isHist) {
      return { time: r.trade_date, value: v,
               color: v >= 0 ? COLORS.up : COLORS.down };
    }
    return { time: r.trade_date, value: v };
  }).filter(Boolean);

  series.setData(data);
  ref.series = series;
  ref.name = name;

  // 数据更新后重绘归一化标注
  v2ScheduleDrawNorm(150);
}

// ─── 加载单只股票 ───

async function v2LoadStock(code, name) {
  if (!v2State.chart) v2CreateChart();
  v2State.code = code;
  v2State.name = name;
  v2El.title.textContent = `${code} ${name} · 加载中…`;

  const start = v2El.startInput.value;
  const end   = v2El.endInput.value;
  const inds = [v2IndCurrent.ind1, v2IndCurrent.ind2, v2IndCurrent.ind3]
                .filter(Boolean);
  const allInds = Array.from(new Set(inds));

  try {
    const url = `/api/stock/${encodeURIComponent(code)}`
      + `?start=${start}&end=${end}&indicators=${allInds.join(",")}`;
    const res = await fetch(url);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    v2State.rows = data.rows || [];
    if (!v2State.rows.length) {
      v2DestroyChart();
      v2CreateChart();
      v2El.title.textContent =
        `${code} ${name} · ${start} ~ ${end} 无数据`;
      v2SetStatus("");
      return;
    }

    v2State.candle.setData(v2State.rows.map(r => ({
      time: r.trade_date, open: r.open, high: r.high,
      low: r.low, close: r.close,
    })));

    // ── 查询日期标记 ──
    const queryDate = v2El.dateInput.value;
    if (queryDate && v2State.rows.length) {
      // 查询日可能是非交易日 → 取 <= 查询日的最近交易日
      let markDate = null;
      for (let i = v2State.rows.length - 1; i >= 0; i--) {
        if (v2State.rows[i].trade_date <= queryDate) {
          markDate = v2State.rows[i].trade_date;
          break;
        }
      }
      if (markDate) {
        v2State.candle.setMarkers([{
          time: markDate,
          position: "aboveBar",
          color: "#ffb454",
          shape: "arrowDown",
          text: "查询日",
          size: 1.5,
        }]);
      } else {
        v2State.candle.setMarkers([]);
      }
    } else {
      v2State.candle.setMarkers([]);
    }

    v2SetPane("ind1", v2IndCurrent.ind1);
    v2SetPane("ind2", v2IndCurrent.ind2);
    v2SetPane("ind3", v2IndCurrent.ind3);

    v2State.chart.timeScale().fitContent();

    v2El.title.textContent =
      `${code} ${name} · ${v2State.rows.length} 交易日 · ${start} ~ ${end}`;
    v2SetStatus("");

    // 兜底强制重绘一次（图表布局完成后）
    v2ScheduleDrawNorm(300);
  } catch (e) {
    console.error(e);
    v2El.title.textContent = `${code} ${name} · 加载失败`;
    v2SetStatus("加载失败：" + e.message, true);
  }
}

// ─── 候选股列表 ───
function v2ApplyFilter(stocks) {
  const fK = document.getElementById("v2FilterKcb").checked;
  const fC = document.getElementById("v2FilterCyb").checked;
  const fB = document.getElementById("v2FilterBjs").checked;
  const fS = document.getElementById("v2FilterSt").checked;

  return stocks.filter(s => {
    const c  = String(s.code || "");
    const p  = c.slice(0, 2);
    const nm = String(s.name || "").toUpperCase();

    if (fK && p === "68")                 return false;  // 科创板
    if (fC && p === "30")                 return false;  // 创业板
    if (fB && (p === "8" || p === "4" || p === "92")) return false; // 北交所
    if (fS && (nm.includes("ST") || nm.includes("*"))) return false; // ST
    return true;
  });
}


// 过滤勾选 → 重渲染列表
["v2FilterKcb", "v2FilterCyb", "v2FilterBjs", "v2FilterSt"]
  .forEach(id => {
    document.getElementById(id).addEventListener("change", v2RenderFilteredList);
  });


function v2RenderStockList(stocks) {
  if (!stocks || !stocks.length) {
    v2El.stockList.innerHTML =
      `<div class="v2-stock-empty">无符合条件的股票</div>`;
    return;
  }
  const sorted = v2SortStocks(stocks);
  if (v2ListState.expanded) v2RenderExpanded(sorted);
  else v2RenderCollapsed(sorted);
}

function v2BuildHead(cols, tmpl) {
  const head = document.createElement("div");
  head.className = "v2-grid-head";
  head.style.gridTemplateColumns = tmpl;
  head.innerHTML = cols.map(c => {
    const sorted = c.key === v2ListState.sortKey;
    const cls = sorted
      ? (v2ListState.sortDir === "desc" ? "sorted-desc" : "sorted-asc")
      : "";
    return `<span data-key="${c.key}" class="${cls}"
                  style="text-align:${c.align}">${c.label}</span>`;
  }).join("");
  return head;
}

function v2RenderCollapsed(stocks) {
  const tmpl = "56px 76px 1fr";
  const cols = [
    { key: "code",       label: "代码", align: "left"  },
    { key: "name",       label: "名称", align: "left"  },
    { key: "pct_change", label: "涨幅", align: "right" },
  ];
  const head = v2BuildHead(cols, tmpl);

  const rowsHtml = stocks.map(s => `
    <div class="v2-stock-item" style="grid-template-columns:${tmpl}"
         data-code="${s.code}" data-name="${s.name}">
      <span class="v2-stock-code">${s.code}</span>
      <span class="v2-stock-name" title="${s.name}">${s.name}</span>
      <span class="v2-stock-pct ${v2Cls(s.pct_change)}">${v2FmtPct(s.pct_change)}</span>
    </div>`).join("");

  v2El.stockList.innerHTML = "";
  v2El.stockList.appendChild(head);
  v2El.stockList.insertAdjacentHTML("beforeend", rowsHtml);
}

function v2RenderExpanded(stocks) {
  const tmpl = v2GridTemplate();
  const head = v2BuildHead(V2_COLUMNS, tmpl, v2RenderFilteredList);

  const rowsHtml = stocks.map(s => `
    <div class="v2-stock-row" style="grid-template-columns:${tmpl}"
         data-code="${s.code}" data-name="${s.name}">
      <span class="c-code">${s.code}</span>
      <span class="c-name" title="${s.name}">${s.name}</span>
      <span class="c-num ${v2Cls(s.pct_change)}">${v2FmtPct(s.pct_change)}</span>
      <span class="c-num">${v2FmtNum(s.close)}</span>
      <span class="c-num">${v2FmtNum(s.turnover_rate)}</span>
      <span class="c-num">${s.hot_rank == null ? "-" : s.hot_rank.toFixed(0)}</span>
      <span class="c-num ${v2Cls(s.huge_order_net)}">${v2FmtBig(s.huge_order_net)}</span>
      <span class="c-num ${v2Cls(s.big_order_net)}">${v2FmtBig(s.big_order_net)}</span>
      <span class="c-num ${v2Cls(s.medium_order_net)}">${v2FmtBig(s.medium_order_net)}</span>
      <span class="c-num ${v2Cls(s.small_order_net)}">${v2FmtBig(s.small_order_net)}</span>
    </div>`).join("");

  v2El.stockList.innerHTML = "";
  v2El.stockList.appendChild(head);
  v2El.stockList.insertAdjacentHTML("beforeend", rowsHtml);
}

// ─── 过滤后的重渲染 ───
function v2RenderFilteredList() {
  const filtered = v2ApplyFilter(v2State.allStocks);

  // 更新 header
  const total = v2State.allStocks.length;
  const shown = filtered.length;
  const name  = v2State.conceptName || "";
  const headEl = document.getElementById("v2SidebarHeader");
  if (headEl) {
    headEl.textContent = shown === total
      ? `${name} · 涨停候选股 ${total} 只`
      : `${name} · 涨停候选股 ${shown}/${total} 只（已过滤）`;
  }

  v2RenderStockList(filtered);

  // 保留选中高亮
  if (v2State.code) {
    const hit = document.querySelector(
      `.v2-stock-item[data-code="${v2State.code}"],
       .v2-stock-row[data-code="${v2State.code}"]`);
    if (hit) hit.classList.add("active");
  }
}

v2El.stockList.addEventListener("click", (e) => {
  // 命中列头 → 已经通过 v2BuildHead 单独挂载，这里忽略
  if (e.target.closest(".v2-grid-head")) return;

  const row = e.target.closest(".v2-stock-item, .v2-stock-row");
  if (!row) return;
  document.querySelectorAll(".v2-stock-item, .v2-stock-row").forEach(x =>
    x.classList.toggle("active", x === row));
  v2LoadStock(row.dataset.code, row.dataset.name);
});

(function initV2ExpandBtn() {
  const btn = document.getElementById("v2ExpandBtn");
  const sidebar = document.getElementById("v2Sidebar");
  if (!btn || !sidebar) return;
  btn.addEventListener("click", () => {
    v2ListState.expanded = !v2ListState.expanded;
    if (v2ListState.expanded) {
      v2ListState.prevWidth = sidebar.getBoundingClientRect().width;
      if (v2ListState.prevWidth < 640) sidebar.style.width = "640px";
      btn.classList.add("expanded");
      btn.textContent = "«";
      btn.title = "收缩列";
    } else {
      if (v2ListState.prevWidth) {
        sidebar.style.width = Math.round(v2ListState.prevWidth) + "px";
      }
      btn.classList.remove("expanded");
      btn.textContent = "»";
      btn.title = "展开列";
    }
    v2RenderFilteredList();
  });
})();

// ─── 查询主流程 ───
async function v2UpdateRangeFromDate(tradeDate) {
  if (!tradeDate) return;
  try {
    const r = await fetch(
      `/api/trading_days_around?date=${tradeDate}&before=200&after=100`);
    if (r.ok) {
      const j = await r.json();
      v2El.startInput.value = j.start;
      v2El.endInput.value   = j.end;
      return;
    }
  } catch (e) {
    console.error("K 线范围计算失败", e);
  }
  // 兜底：自然日
  const d = new Date(tradeDate);
  const s = new Date(d); s.setDate(s.getDate() - 300);
  const e2 = new Date(d); e2.setDate(e2.getDate() + 150);
  const fmt = (x) => `${x.getFullYear()}-`
    + `${String(x.getMonth() + 1).padStart(2, "0")}-`
    + `${String(x.getDate()).padStart(2, "0")}`;
  v2El.startInput.value = fmt(s);
  v2El.endInput.value   = fmt(e2);
}
async function v2Query() {
  const conceptCode = v2El.codeInput.value.trim();
  const tradeDate   = v2El.dateInput.value;
  if (!conceptCode) { v2SetStatus("请输入概念代码", true); return; }
  if (!tradeDate)   { v2SetStatus("请选择查询日期", true); return; }

  v2El.queryBtn.disabled = true;
  v2SetStatus("加载中…");

  try {
    // 1. 默认 K 线范围（±120 交易日）
    // 1. 若 K 线起止为空，按查询日期 ±200/100 交易日计算
    if (!v2El.startInput.value || !v2El.endInput.value) {
      await v2UpdateRangeFromDate(tradeDate);
    }

    // 2. 候选股
    const res = await fetch(
      `/api/concept/limit_up_stocks?concept_code=${encodeURIComponent(conceptCode)}`
      + `&trade_date=${tradeDate}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();

    v2El.sidebarHead.textContent =
      `${data.concept_name} · 近10交易日涨停股 ${data.stocks.length} 只`;
    v2RenderStockList(data.stocks);

    v2State.allStocks  = data.stocks || [];
    v2State.conceptName = data.concept_name || "";
    // 切概念时先清空旧数据
    v2State.rows = [];
    v2DrawNormLabels();
    v2RenderFilteredList();

    // 自动选中第一只（过滤后）
    const first = v2El.stockList.querySelector(".v2-stock-item");
    if (first) {
      first.classList.add("active");
      await v2LoadStock(first.dataset.code, first.dataset.name);
    } else {
      v2DestroyChart();
      v2El.title.textContent = `${data.concept_name} · 无符合条件的股票`;
    }

    v2SetStatus(`${data.concept_name} · ${tradeDate} · ${data.stocks.length} 只`);
    if (typeof saveFormState === "function") saveFormState();
  } catch (e) {
    console.error(e);
    v2SetStatus("查询失败：" + e.message, true);
    v2El.stockList.innerHTML = `<div class="v2-stock-empty">查询失败</div>`;
  } finally {
    v2El.queryBtn.disabled = false;
  }
}

v2El.queryBtn.addEventListener("click", v2Query);
// 查询日期变化 → K 线范围跟着变
v2El.dateInput.addEventListener("change", async () => {
  const d = v2El.dateInput.value;
  if (!d) return;
  await v2UpdateRangeFromDate(d);
  if (typeof saveFormState === "function") saveFormState();
});

// 适屏
document.getElementById("v2Fit").addEventListener("click", () => {
  if (v2State.chart) {
    v2State.chart.timeScale().fitContent();
    v2ScheduleDrawNorm(150);
  }
});

// ─── 左右拖拽 ───
(function initV2Splitter() {
  const splitter = document.getElementById("v2Splitter");
  const sidebar  = document.getElementById("v2Sidebar");
  if (!splitter || !sidebar) return;

  let dragging = false, startX = 0, startW = 0;

  splitter.addEventListener("mousedown", (e) => {
    dragging = true;
    startX = e.clientX;
    startW = sidebar.getBoundingClientRect().width;
    splitter.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const w = Math.max(160, Math.min(640, startW + dx));
    sidebar.style.width = w + "px";
    // 宽度变化会触发 ResizeObserver，图表自动重绘，无需手动 applyOptions
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  });
})();

// ─── 从界面 1 跳转 ───

window.switchToView2 = function (conceptCode, conceptName, tradeDate) {
  document.querySelectorAll(".tab-btn").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === "view2"));
  document.querySelectorAll(".view-panel").forEach(p =>
    p.classList.remove("active"));
  document.getElementById("view2").classList.add("active");

  document.getElementById("view1DateGroup").style.display = "none";
  document.getElementById("view2FilterGroup").style.display = "";
  document.getElementById("view2IndicatorRow").style.display = "";

  v2El.codeInput.value = conceptCode || "";
  v2El.dateInput.value = tradeDate || document.getElementById("v1Date").value;

  v2El.startInput.value = "";
  v2El.endInput.value = "";

  // 保存状态（tab=view2 + 表单内容）
  if (typeof saveFormState === "function") saveFormState();

  v2Query();
};

// ─── 列头点击：捕获阶段最先触发，无视其它监听 ───
// ─── 列头点击：只重排行，不重建 head ───
// ─── 列头点击 → 直接重排现有 DOM 行 ───
document.addEventListener("click", (e) => {
  const head = e.target.closest(".v2-grid-head > span[data-key]");
  if (!head) return;
  e.stopPropagation();
  e.preventDefault();

  const key = head.dataset.key;
  if (v2ListState.sortKey === key) {
    v2ListState.sortDir = v2ListState.sortDir === "desc" ? "asc" : "desc";
  } else {
    v2ListState.sortKey = key;
    v2ListState.sortDir = "desc";
  }

  const list = document.getElementById("v2StockList");
  const rows = Array.from(
    list.querySelectorAll(".v2-stock-item, .v2-stock-row"));

  const byCode = {};
  for (const s of v2State.allStocks) byCode[String(s.code)] = s;

  rows.sort((a, b) => {
    const sa = byCode[a.dataset.code] || {};
    const sb = byCode[b.dataset.code] || {};
    const va = sa[key], vb = sb[key];
    const an = va == null || (typeof va === "number" && Number.isNaN(va));
    const bn = vb == null || (typeof vb === "number" && Number.isNaN(vb));
    if (an && bn) return String(a.dataset.code).localeCompare(String(b.dataset.code));
    if (an) return 1;
    if (bn) return -1;
    let cmp;
    if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
    else cmp = String(va).localeCompare(String(vb), "zh");
    return v2ListState.sortDir === "desc" ? -cmp : cmp;
  });

  // 直接按新顺序 append 回 list（head 是 list 的第一个子节点，会保留）
  for (const row of rows) list.appendChild(row);

  // 刷新列头箭头
  document.querySelectorAll(".v2-grid-head > span[data-key]").forEach(el => {
    el.classList.remove("sorted-asc", "sorted-desc");
    if (el.dataset.key === v2ListState.sortKey) {
      el.classList.add(
        v2ListState.sortDir === "desc" ? "sorted-desc" : "sorted-asc");
    }
  });
}, true);

function v2SortAndReorderRows() {
  const list = document.getElementById("v2StockList");
  const rows = Array.from(
    list.querySelectorAll(".v2-stock-item, .v2-stock-row"));
  if (!rows.length) return;

  const byCode = {};
  for (const s of v2State.allStocks) byCode[s.code] = s;

  const { key, dir } = v2ListState;
  const sorted = rows.slice().sort((a, b) => {
    const sa = byCode[a.dataset.code] || {};
    const sb = byCode[b.dataset.code] || {};
    const va = sa[key], vb = sb[key];
    const an = va == null || (typeof va === "number" && Number.isNaN(va));
    const bn = vb == null || (typeof vb === "number" && Number.isNaN(vb));
    if (an && bn) {
      return String(a.dataset.code).localeCompare(String(b.dataset.code));
    }
    if (an) return 1;
    if (bn) return -1;
    let cmp;
    if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
    else cmp = String(va).localeCompare(String(vb), "zh");
    return dir === "desc" ? -cmp : cmp;
  });

  // appendChild 对已有子节点是"移动"，会按新顺序重排
  for (const row of sorted) list.appendChild(row);
}

function v2RefreshHeadArrows() {
  document.querySelectorAll(".v2-grid-head > span[data-key]").forEach(el => {
    el.classList.remove("sorted-asc", "sorted-desc");
    if (el.dataset.key === v2ListState.sortKey) {
      el.classList.add(
        v2ListState.sortDir === "desc" ? "sorted-desc" : "sorted-asc");
    }
  });
}

// ─── 归一化值标注（画在 K 线覆盖层） ───

const V2_NORM_BASES = [
  "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
];

function v2FindNormBase(name) {
  if (!name) return null;
  for (const b of V2_NORM_BASES) {
    if (name === b || name.startsWith(b + "_")) return b;
  }
  return null;
}

function v2DrawNormLabels() {
  const canvas = document.getElementById("v2ChartOverlay");
  if (!canvas) return;

  const chart = v2State.chart;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;

  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr)
      || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!chart || !v2State.rows.length) return;

  const threshold = parseFloat(
    document.getElementById("v2NormThreshold")?.value) || 0;

  const ts = chart.timeScale();
  const paneNames = {
    ind1: v2IndCurrent.ind1,
    ind2: v2IndCurrent.ind2,
    ind3: v2IndCurrent.ind3,
  };

  ctx.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";

  for (const paneKey of ["ind1", "ind2", "ind3"]) {
    const ref = v2State.panes[paneKey];
    if (!ref || !ref.series) continue;

    const indName = paneNames[paneKey];
    const base = v2FindNormBase(indName);
    if (!base) continue;

    const series = ref.series;

    for (const r of v2State.rows) {
      // 用选中字段本身（累计 N 日时就是累计值）
      const net = r[indName];
      const amt = r.amount;
      const tr  = r.turnover_rate;
      if (net == null || amt == null || tr == null || amt === 0) continue;

      const norm = net * tr / amt;
      if (Math.abs(norm) < threshold) continue;

      const seriesVal = r[indName];
      if (seriesVal == null) continue;

      const x = ts.timeToCoordinate(r.trade_date);
      if (x == null || x < -8 || x > w + 8) continue;
      let y = series.priceToCoordinate(seriesVal);
      if (y == null) {
        // 兜底：用 0 或最近非空值反推
        y = series.priceToCoordinate(0) ?? null;
        if (y == null) continue;
      }

      const label = norm.toFixed(2);
      const above = seriesVal >= 0;
      const yText = above ? y - 2 : y + 9;
      ctx.fillStyle = above ? "#ef5350" : "#26a69a";
      ctx.fillText(label, x, yText);
    }
  }
}
let __normTimer = null;
function v2ScheduleDrawNorm(delay = 120) {
  clearTimeout(__normTimer);
  __normTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(v2DrawNormLabels);
    });
  }, delay);
}
// 阈值变化 → 只重绘覆盖层，不重新拉数据
document.getElementById("v2NormThreshold")?.addEventListener("input", () => {
  v2ScheduleDrawNorm(0);
});

// ─── 界面 2 交易日翻页 ───
async function v2ShiftDate(direction) {
  const d = document.getElementById("v2Date").value;
  if (!d) return;
  const btnP = document.getElementById("v2PrevDay");
  const btnN = document.getElementById("v2NextDay");
  btnP.disabled = btnN.disabled = true;
  try {
    const r = await fetch(
      `/api/trading_day_neighbor?date=${d}&direction=${direction}`);
    if (!r.ok) return;
    const j = await r.json();
    if (!j.date) return;
    document.getElementById("v2Date").value = j.date;
    // 复用已有的 K 线范围联动逻辑
    await v2UpdateRangeFromDate(j.date);
    if (typeof saveFormState === "function") saveFormState();
    v2Query();
  } finally {
    btnP.disabled = btnN.disabled = false;
  }
}
document.getElementById("v2PrevDay").addEventListener("click",
  () => v2ShiftDate("prev"));
document.getElementById("v2NextDay").addEventListener("click",
  () => v2ShiftDate("next"));