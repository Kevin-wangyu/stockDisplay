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

// ─── AMO 买卖结构 ───
const AMO_COLORS = {
  // 主买：暖色系（红 → 橙 → 金 → 紫）
  huge:   { buy: "rgba(239, 83, 80, 0.92)",   sell: "rgba(67, 56, 202, 0.92)"   },  // 红  / 深蓝紫
  big:    { buy: "rgba(255, 152, 0, 0.92)",   sell: "rgba(2, 132, 199, 0.92)"   },  // 橙  / 蓝
  medium: { buy: "rgba(255, 193, 7, 0.92)",   sell: "rgba(6, 182, 212, 0.92)"   },  // 金  / 青
  small:  { buy: "rgba(168, 85, 247, 0.92)",  sell: "rgba(103, 232, 249, 0.92)" },  // 紫  / 浅青
};
const AMO_PANES = {
  price: { top: 0.00, bottom: 0.42 },   // K 线缩到 42%~58% 处
  amo:   { top: 0.45, bottom: 0.02 },   // amo 区从 45% 到 98%
};

const amoState = {
  visible: false,
  mode: "ratio",       // "ratio" | "amount"
  rows: [],            // [{trade_date, huge_buy, ...}]
  helper: null,        // 透明 series，用于坐标转换
};

// helper series：绑定 amo pane 的坐标系
amoState.helper = chart.addLineSeries({
  priceScaleId: "amo",
  color: "rgba(0,0,0,0)",
  lineWidth: 1,
  crosshairMarkerVisible: false,
  priceLineVisible: false,
  lastValueVisible: false,
});
chart.priceScale("amo").applyOptions({
  visible: false,
  scaleMargins: AMO_PANES.amo,
  borderColor: COLORS.border,
});

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
      // 若 AMO 模式开着，新 series 也隐藏（否则重建后可见）
    if (typeof amoState !== "undefined" && amoState.visible) {
      ref.series.applyOptions({ visible: false });
      chart.priceScale(paneKey).applyOptions({
        visible: false,
        scaleMargins: { top: 0.99, bottom: 0.0 },
      });
    }
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

  // ⭐ AMO 模式下不画归一化标注
  if (typeof amoState !== "undefined" && amoState.visible) {
    return;
  }

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

// ─── AMO canvas 渲染 ───
function amoComputeLayout(mode) {
  if (mode === "ratio") {
    return { minV: -50, maxV: 50 };   // ← 从 ±110 改成 ±100
  }
  let maxAbs = 0;
  for (const r of amoState.rows) {
    maxAbs = Math.max(maxAbs, r.total_buy / 1e8, r.total_sell / 1e8);
  }
  const pad = maxAbs * 1.1 || 1;
  return { minV: -pad, maxV: pad };
}

function amoUpdateScale() {
  const { minV, maxV } = amoComputeLayout(amoState.mode);
  amoState.helper.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: minV, maxValue: maxV },
    }),
  });
}

function renderAmoCanvas() {
  console.log("[AMO] renderAmoCanvas called, rows:", amoState.rows.length);   // ⭐
  const canvas = document.getElementById("amoOverlay");
  if (!canvas) return;
  if (!amoState.visible) return;

  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr);
  if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!amoState.rows.length) return;

  const ts = chart.timeScale();
  const mode = amoState.mode;
  const YI = 1e8;

  // ── Y 轴映射：canvas 内部 y 直接从 minV/maxV 线性映射到 h ~ 0 ──
  const { minV, maxV } = amoComputeLayout(mode);
  const yOf = (v) => h - ((v - minV) / (maxV - minV)) * h;

  const keys = ["huge", "big", "medium", "small"];

  // 单日柱宽：取可视区域内单日宽度的 70%，限制 2~18px
  const visibleCount = Math.max(1, amoState.rows.length);
  const barWidth = Math.max(2, Math.min(18, (w / visibleCount) * 0.7));
  const halfW = Math.max(1, (barWidth - 1) / 2);

  // ── 逐日堆叠柱（买左卖右，从 0 向上） ──
  for (const r of amoState.rows) {
    const x = ts.timeToCoordinate(r.trade_date);
    if (x == null || x < -20 || x > w + 20) continue;

    const total = r.total_buy + r.total_sell;
    if (total <= 0) continue;

    let buyVals, sellVals;
    if (mode === "ratio") {
      buyVals  = [r.huge_buy, r.big_buy, r.medium_buy, r.small_buy]
                  .map(v => v / total * 100);
      sellVals = [r.huge_sell, r.big_sell, r.medium_sell, r.small_sell]
                  .map(v => v / total * 100);
    } else {
      buyVals  = [r.huge_buy, r.big_buy, r.medium_buy, r.small_buy]
                  .map(v => v / YI);
      sellVals = [r.huge_sell, r.big_sell, r.medium_sell, r.small_sell]
                  .map(v => v / YI);
    }

    const xBuy  = x - halfW - 0.5;
    const xSell = x + 0.5;

    // 主买（左半边，从 0 向上堆叠）
    let cum = 0;
    for (let i = 0; i < 4; i++) {
      const y1 = yOf(cum);
      cum += buyVals[i];
      const y2 = yOf(cum);
      ctx.fillStyle = AMO_COLORS[keys[i]].buy;
      ctx.fillRect(xBuy, Math.min(y1, y2), halfW,
                   Math.max(1, Math.abs(y2 - y1)));
    }

    // 主卖（右半边，从 0 向上堆叠）
    cum = 0;
    for (let i = 0; i < 4; i++) {
      const y1 = yOf(cum);
      cum += sellVals[i];
      const y2 = yOf(cum);
      ctx.fillStyle = AMO_COLORS[keys[i]].sell;
      ctx.fillRect(xSell, Math.min(y1, y2), halfW,
                   Math.max(1, Math.abs(y2 - y1)));
    }
  }

  // ── 净主买折线 ──
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  for (const r of amoState.rows) {
    const x = ts.timeToCoordinate(r.trade_date);
    if (x == null) continue;

    let netVal;
    if (mode === "ratio") {
      const total = r.total_buy + r.total_sell;
      if (total <= 0) continue;
      netVal = (r.total_buy - r.total_sell) / total * 100;
    } else {
      netVal = (r.total_buy - r.total_sell) / YI;
    }
    const y = yOf(netVal);
    if (!started) { ctx.moveTo(x, y); started = true; }
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // ── 零基线 ──
  const y0 = yOf(0);
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, y0);
  ctx.lineTo(w, y0);
  ctx.stroke();
}
function amoClearCanvas() {
  const canvas = document.getElementById("amoOverlay");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// 缩放 / 滚动 → 重绘
chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
  requestAnimationFrame(drawNormLabels);
  requestAnimationFrame(renderAmoCanvas);
});

// 阈值变化 → 重绘
document.getElementById("normThreshold")?.addEventListener("input", () => {
  requestAnimationFrame(drawNormLabels);
});

new ResizeObserver(() => {
  chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
  requestAnimationFrame(drawNormLabels);
  requestAnimationFrame(renderAmoCanvas);
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

    // ⭐ 概念模式显示概念名，其它模式显示股票代码
    const displayCode = (typeof dataSource !== "undefined" && dataSource === "concept"
                        && typeof conceptState !== "undefined" && conceptState.active)
        ? conceptState.active
        : document.getElementById("code").value;

    const codeVal = document.getElementById("code").value;
    const nameVal = (typeof dataSource === "undefined" || dataSource !== "concept")
        ? (state.rows.find(r => r.code === codeVal)?.name
          || (state.rows[0] && state.rows[0].name) || "")
        : "";

    legendEl.innerHTML =
        `<span class="row"><span class="val">${param.time}</span>` +
        `<span style="color:var(--text-dim)">${displayCode}${nameVal ? " " + nameVal : ""}</span></span>` +
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
  const _safeData = (r, name) => {
    const time = String(r.trade_date ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(time)) return null;
    const v = r[name];
    if (v == null || !Number.isFinite(Number(v))) return null;
    return { time, value: Number(v) };
  };

  for (const k of OVERLAY_KEYS) {
    if (!overlayVisible[k]) { overlaySeries[k].setData([]); continue; }
    const map = new Map();
    state.rows.forEach(r => {
      const item = _safeData(r, k);
      if (item) map.set(item.time, item);
    });
    overlaySeries[k].setData(
      Array.from(map.values()).sort((a, b) =>
        a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  }

  const bollOn = overlayVisible.boll;
  for (const k of BOLL_KEYS) {
    if (!bollOn) { overlaySeries[k].setData([]); continue; }
    const map = new Map();
    state.rows.forEach(r => {
      const item = _safeData(r, k);
      if (item) map.set(item.time, item);
    });
    overlaySeries[k].setData(
      Array.from(map.values()).sort((a, b) =>
        a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
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
    const wrap = document.getElementById("chartWrap");
    const sidebar = document.getElementById("conceptSidebar");

    // 三个模式共用日期范围控件；单日期组永久隐藏
    document.getElementById("dateRangeGroup").style.display = "";
    document.getElementById("singleDateGroup").style.display = "none";
    document.getElementById("overlayRow").style.display = "";

    if (isConcept) {
        wrap.classList.add("concept-mode");
        sidebar.style.display = "flex";
        legendEl.style.display = "";
    } else {
        wrap.classList.remove("concept-mode");
        sidebar.style.display = "none";
        legendEl.style.display = "";
    }

    // 触发 resize（布局切换后图表要重新计算宽度）
    setTimeout(() => {
        chart.applyOptions({
            width:  chartEl.clientWidth,
            height: chartEl.clientHeight,
        });
    }, 50);
}