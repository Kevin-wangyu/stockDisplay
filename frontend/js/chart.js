// ─────────────── 图表（个股/行业） ───────────────

const chartEl  = document.getElementById("chart");
const legendEl = document.getElementById("legend");
const statusEl = document.getElementById("status");
const missingWarnEl = document.getElementById("missingWarning");
const btn = document.getElementById("load");

const chart = LightweightCharts.createChart(chartEl, {
layout: {
    background: { color: COLORS.bg },
    textColor: COLORS.text,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
    fontSize: 12,
},
grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: { color: COLORS.crosshair, width: 1, style: 3, labelBackgroundColor: "#2962ff" },
    horzLine: { color: COLORS.crosshair, width: 1, style: 3, labelBackgroundColor: "#2962ff" },
},
rightPriceScale: {
    borderColor: COLORS.border,
    scaleMargins: PANES.price,
    minimumWidth: 70,
},
timeScale: { borderColor: COLORS.border, timeVisible: false, secondsVisible: false },
localization: { locale: "zh-CN" },
});

const candleSeries = chart.addCandlestickSeries({
upColor: COLORS.up, downColor: COLORS.down,
borderUpColor: COLORS.up, borderDownColor: COLORS.down,
wickUpColor: COLORS.up, wickDownColor: COLORS.down,
priceLineVisible: false, lastValueVisible: true,
});

const overlaySeries = {};
for (const k of OVERLAY_KEYS) {
overlaySeries[k] = chart.addLineSeries({
    priceScaleId: "right", color: OVERLAY_COLORS[k], lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
});
}
overlaySeries.boll_mid = chart.addLineSeries({
priceScaleId: "right", color: OVERLAY_COLORS.boll_mid, lineWidth: 1,
priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
});
overlaySeries.boll_up = chart.addLineSeries({
priceScaleId: "right", color: OVERLAY_COLORS.boll_up, lineWidth: 1, lineStyle: 2,
priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
});
overlaySeries.boll_low = chart.addLineSeries({
priceScaleId: "right", color: OVERLAY_COLORS.boll_low, lineWidth: 1, lineStyle: 2,
priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
});

const paneRefs = {
  ind1: { series: null, name: null },
  ind2: { series: null, name: null },
  ind3: { series: null, name: null },
};

function rebuildPane(paneKey, name, data) {
    const ref = paneRefs[paneKey];
    if (ref.series) { chart.removeSeries(ref.series); ref.series = null; }
    if (!data || !data.length) { ref.name = name; return; }

    if (isHistogramRedGreen(name)) {
        ref.series = chart.addHistogramSeries({
        priceScaleId: paneKey, priceLineVisible: false, lastValueVisible: true,
        });
    } else {
        ref.series = chart.addLineSeries({
        priceScaleId: paneKey, color: COLORS.line, lineWidth: 1.5,
        priceLineVisible: false, lastValueVisible: true,
        });
    }
    chart.priceScale(paneKey).applyOptions({
        scaleMargins: PANES[paneKey],
        visible: true,
        borderColor: COLORS.border,
        minimumWidth: 70,
    });
    ref.series.setData(data);
    ref.name = name;
}
// ─── 归一化标注 ───

const NORM_BASES = [
  "huge_order_net", "big_order_net", "medium_order_net", "small_order_net",
];

function findNormBase(name) {
  if (!name) return null;
  for (const b of NORM_BASES) {
    if (name === b || name.startsWith(b + "_")) return b;
  }
  return null;
}

function drawNormLabels() {
  const canvas = document.getElementById("chartOverlay");
  if (!canvas) return;

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

  if (!state.rows.length) return;

  const th = parseFloat(
    document.getElementById("normThreshold")?.value) || 0;

  const paneNames = {
    ind1: currentInd1Value,
    ind2: currentInd2Value,
    ind3: currentInd3Value,
  };

  ctx.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";

  for (const paneKey of ["ind1", "ind2", "ind3"]) {
    const ref = paneRefs[paneKey];
    if (!ref || !ref.series) continue;

    const indName = paneNames[paneKey];
    const base = findNormBase(indName);
    if (!base) continue;

    const series = ref.series;

    for (const r of state.rows) {
      const net = r[indName];
      const amt = r.amount;
      const tr  = r.turnover_rate;
      if (net == null || amt == null || tr == null || amt === 0) continue;

      const norm = net * tr / amt;
      if (Math.abs(norm) < th) continue;

      const seriesVal = r[indName];
      if (seriesVal == null) continue;

      const x = chart.timeScale().timeToCoordinate(r.trade_date);
      if (x == null || x < -8 || x > w + 8) continue;
      let y = series.priceToCoordinate(seriesVal);
      if (y == null) {
        y = series.priceToCoordinate(0);
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

// 缩放 / 滚动 → 重绘
chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
  requestAnimationFrame(drawNormLabels);
});

// 阈值变化 → 重绘
document.getElementById("normThreshold")?.addEventListener("input", () => {
  requestAnimationFrame(drawNormLabels);
});

new ResizeObserver(() => {
  chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
  requestAnimationFrame(drawNormLabels);
}).observe(chartEl);

chart.subscribeCrosshairMove((param) => {
    if (!param.time) { legendEl.innerHTML = ""; return; }
    const candle = param.seriesData.get(candleSeries);
    if (!candle) { legendEl.innerHTML = ""; return; }

    const cls = candle.close >= candle.open ? "up" : "down";
    const paneBits = [];
    for (const key of ["ind1", "ind2", "ind3"]) {
        const ref = paneRefs[key];
        if (!ref.series || !ref.name) continue;
        const v = param.seriesData.get(ref.series);
        if (v && v.value != null) {
        paneBits.push(`<span class="title">${ref.name}</span><span class="val">${fmtBig(v.value)}</span>`);
        }
    }

    const maBits = [];
    for (const k of [...OVERLAY_KEYS, ...BOLL_KEYS]) {
        const s = overlaySeries[k];
        if (!s) continue;
        const v = param.seriesData.get(s);
        if (v && v.value != null) {
        const label = k === "boll_mid" ? "MID"
                    : k === "boll_up"  ? "UP"
                    : k === "boll_low" ? "DN"
                    : k.toUpperCase();
        maBits.push(`<span style="color:${OVERLAY_COLORS[k]}">${label} ${fmtNum(v.value)}</span>`);
        }
    }

    const codeVal = document.getElementById("code").value;
    const nameVal = state.rows.find(r => r.code === codeVal)?.name
                || (state.rows[0] && state.rows[0].name)
                || "";
    legendEl.innerHTML =
        `<span class="row"><span class="val">${param.time}</span>` +
        `<span style="color:var(--text-dim)">${codeVal}${nameVal ? " " + nameVal : ""}</span></span>` +
        `<span class="row ${cls}">` +
        `开 <span class="val">${fmtNum(candle.open)}</span>` +
        `高 <span class="val">${fmtNum(candle.high)}</span>` +
        `低 <span class="val">${fmtNum(candle.low)}</span>` +
        `收 <span class="val">${fmtNum(candle.close)}</span>` +
        (() => {
        // 归一化 param.time（字符串 / 时间戳 / BusinessDay 对象）
        const key = (() => {
            const t = param.time;
            if (typeof t === "string") return t;
            if (typeof t === "number") {
            const d = new Date(t * 1000);
            const y = d.getUTCFullYear();
            const m = String(d.getUTCMonth() + 1).padStart(2, "0");
            const dd = String(d.getUTCDate()).padStart(2, "0");
            return `${y}-${m}-${dd}`;
            }
            if (t && typeof t === "object" && "year" in t) {
            const m = String(t.month).padStart(2, "0");
            const dd = String(t.day).padStart(2, "0");
            return `${t.year}-${m}-${dd}`;
            }
            return null;
        })();

        if (!key) return "";
        const idx = state.rows.findIndex(r => r.trade_date === key);
        if (idx <= 0) return "";

        const prev = state.rows[idx - 1];
        if (prev.close == null || prev.close === 0) return "";

        // 用前复权 close 现算（与 K 线口径完全一致）
        const pct = (candle.close - prev.close) / prev.close * 100;
        const cls = pct >= 0 ? "up" : "down";
        const sign = pct >= 0 ? "+" : "";
        return `涨 <span class="val ${cls}">${sign}${pct.toFixed(2)}%</span>`;
        })() +
        `</span>` +
        (paneBits.length ? `<span class="row">${paneBits.join(" ")}</span>` : "") +
        (maBits.length ? `<span class="row" style="font-size:11px">${maBits.join(" ")}</span>` : "");
});

// ─────────────── 状态 ───────────────

const state = {
rows: [],
indicators: [],
cumulativeGroups: [],
conceptData: null,
};
let loadToken = 0;
let dataSource = "stock";

const setStatus = (m, e = false) => {
statusEl.textContent = m;
statusEl.classList.toggle("error", e);
};

function showMissingWarning(missingDates) {
if (!missingDates || !missingDates.length) {
    missingWarnEl.classList.remove("show");
    missingWarnEl.innerHTML = "";
    return;
}
const MAX_SHOW = 8;
const shown = missingDates.slice(0, MAX_SHOW).join("、");
const more = missingDates.length > MAX_SHOW ? ` 等 ${missingDates.length} 天` : "";

missingWarnEl.innerHTML =
    `<span class="close-btn" title="关闭">×</span>` +
    `<span class="title">⚠ 缺失交易日 ${missingDates.length} 天</span>` +
    `<span class="dates">${shown}${more}</span>`;
missingWarnEl.title = missingDates.join("\n");
missingWarnEl.classList.add("show");

const closeBtn = missingWarnEl.querySelector(".close-btn");
if (closeBtn) {
    closeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    missingWarnEl.classList.remove("show");
    });
}
}

// ─────────────── 级联下拉 ───────────────

function initCascadeDropdown(root, getGroups, onSelect) {
const trigger = root.querySelector(".dd-trigger");
const labelEl = root.querySelector(".dd-label");
const panel   = root.querySelector(".dd-panel");

function close() { root.classList.remove("open"); }

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
}

function select(value, label) {
    labelEl.textContent = label;
    onSelect(value, label);
    close();
}

trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!root.classList.contains("open")) {
    const rect = trigger.getBoundingClientRect();
    root.classList.toggle("flip-sub", rect.left + rect.width + 220 > window.innerWidth);
    render();
    }
    root.classList.toggle("open");
});

document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) close();
});

return {
    setValue(value, label) { labelEl.textContent = label; },
    refresh() { if (root.classList.contains("open")) render(); },
};
}

function buildGroupsForDropdown() {
const groups = [];

for (const g of state.cumulativeGroups) {
    const items = [];
    for (const d of g.days) {
    const value = `${g.key}_${d}d`;
    const meta = state.indicators.find(i => i.name === value);
    items.push({
        value,
        label: `${d}日`,
        fullLabel: meta ? meta.description : `${d}日`,
    });
    }
    groups.push({ type: "cascade", label: g.label, items });
}

const cascadeValues = new Set();
for (const g of groups) {
    if (g.type === "cascade") for (const it of g.items) cascadeValues.add(it.value);
}

for (const it of state.indicators) {
    if (OVERLAY_ONLY.has(it.name)) continue;
    if (cascadeValues.has(it.name)) continue;
    const s = it.scope || "all";
    if (s !== "all" && s !== dataSource) continue;
    groups.push({ type: "flat", value: it.name, label: it.description });
}
return groups;
}

function findLabel(value) {
for (const g of buildGroupsForDropdown()) {
    if (g.type === "flat" && g.value === value) return g.label;
    if (g.type === "cascade") {
    for (const it of g.items) if (it.value === value) return it.fullLabel;
    }
}
return value;
}

let currentInd1Value = "huge_order_net_1d";
let currentInd2Value = "medium_order_net_1d";
let currentInd3Value = "small_order_net_1d";

const ddInd1 = initCascadeDropdown(
document.getElementById("dd-ind1"),
buildGroupsForDropdown,
(value) => { currentInd1Value = value; applyPane("ind1", value); },
);
const ddInd2 = initCascadeDropdown(
document.getElementById("dd-ind2"),
buildGroupsForDropdown,
(value) => { currentInd2Value = value; applyPane("ind2", value); },
);
const ddInd3 = initCascadeDropdown(
document.getElementById("dd-ind3"),
buildGroupsForDropdown,
(value) => { currentInd3Value = value; applyPane("ind3", value); },
);

// ─────────────── 均线胶囊 ───────────────

const overlayVisible = {};
document.querySelectorAll(".ma-toggle input").forEach(el => {
const k = el.dataset.line;
overlayVisible[k] = el.checked;
el.parentElement.classList.toggle("active", el.checked);
el.addEventListener("change", () => {
    overlayVisible[k] = el.checked;
    el.parentElement.classList.toggle("active", el.checked);
    applyOverlays();
});
});

function applyOverlays() {
if (!state.rows.length) {
    for (const k in overlaySeries) overlaySeries[k].setData([]);
    return;
}
for (const k of OVERLAY_KEYS) {
    if (!overlayVisible[k]) { overlaySeries[k].setData([]); continue; }
    overlaySeries[k].setData(state.rows.map(r => ({
    time: r.trade_date, value: r[k],
    })).filter(d => d.value != null));
}
const bollOn = overlayVisible.boll;
for (const k of BOLL_KEYS) {
    if (!bollOn) { overlaySeries[k].setData([]); continue; }
    overlaySeries[k].setData(state.rows.map(r => ({
    time: r.trade_date, value: r[k],
    })).filter(d => d.value != null));
}
}

// ─────────────── 加载指标列表 ───────────────

async function loadIndicatorOptions() {
try {
    const [r1, r2] = await Promise.all([
    fetch("/api/indicators"),
    fetch("/api/indicator_groups"),
    ]);
    const all = await r1.json();
    const grps = await r2.json();

    state.indicators = all.filter(i => i.type === "sql");
    state.cumulativeGroups = grps.cumulative_groups || [];

    if (!state.indicators.some(i => i.name === "volume")) {
    state.indicators.unshift({
        name: "volume", description: "成交量（原始）",
        type: "sql", scope: "all",
    });
    }
    updateIndicatorOptions();
} catch (e) {
    console.error("加载指标列表失败", e);
    setStatus("加载指标列表失败", true);
}
}

function updateIndicatorOptions() {
currentInd1Value = "huge_order_net_1d";
currentInd2Value = "medium_order_net_1d";
currentInd3Value = "small_order_net_1d";

ddInd1.setValue(currentInd1Value, findLabel(currentInd1Value));
ddInd2.setValue(currentInd2Value, findLabel(currentInd2Value));
ddInd3.setValue(currentInd3Value, findLabel(currentInd3Value));
}

// ─────────────── UI 模式切换 ───────────────

function updateUIByDataSource() {
const isConcept = dataSource === "concept";

document.getElementById("dateRangeGroup").style.display = isConcept ? "none" : "";
document.getElementById("singleDateGroup").style.display = isConcept ? "" : "none";
document.getElementById("overlayRow").style.display = isConcept ? "none" : "";

if (isConcept) {
    document.getElementById("chart").style.display = "none";
    document.getElementById("conceptView").classList.add("show");
    legendEl.style.display = "none";
} else {
    document.getElementById("chart").style.display = "";
    document.getElementById("conceptView").classList.remove("show");
    legendEl.style.display = "";
}
}