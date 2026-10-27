"use strict";

// ─────────────── 常量 ───────────────

const COLORS = {
bg:       "#131722",
text:     "#d1d4dc",
grid:     "#1c202b",
border:   "#2a2e39",
crosshair:"#758696",
up:       "#ef5350",
down:     "#26a69a",
upFill:   "rgba(239,83,80,0.85)",
downFill: "rgba(38,166,154,0.85)",
line:     "#5b8def",
accent:   "#2962ff",
};

const PANES = {
  price: { top: 0.00, bottom: 0.55 },
  ind1:  { top: 0.47, bottom: 0.37 },
  ind2:  { top: 0.65, bottom: 0.19 },
  ind3:  { top: 0.83, bottom: 0.01 },
};

const OVERLAY_COLORS = {
ma5: "#f5f5f5", ma10: "#f0b90b", ma20: "#a855f7", ma30: "#06b6d4",
ma60: "#f97316", ma120: "#e91e63", ma250: "#9ca3af",
boll_mid: "#f0b90b", boll_up: "#6c5ce7", boll_low: "#6c5ce7",
};
const OVERLAY_KEYS = ["ma5","ma10","ma20","ma30","ma60","ma120","ma250"];
const BOLL_KEYS    = ["boll_mid","boll_up","boll_low"];
const OVERLAY_ONLY = new Set([...OVERLAY_KEYS, ...BOLL_KEYS]);

const HISTOGRAM_REDGREEN = new Set([
"volume",
"main_net", "main_net_3d", "main_net_5d", "main_net_10d", "main_net_20d",
"close_change", "pct",
]);
const CUM_NET_RE = /^(main_net|huge_order_net|big_order_net|medium_order_net|small_order_net)_\d+d$/;
const isHistogramRedGreen = (name) =>
HISTOGRAM_REDGREEN.has(name) || CUM_NET_RE.test(name);

// ─────────────── 工具函数 ───────────────

const fmtNum = (v, d = 2) =>
(v == null || Number.isNaN(v)) ? "-" : Number(v).toFixed(d);

const fmtBig = (v) => {
if (v == null || Number.isNaN(v)) return "-";
const n = Number(v), abs = Math.abs(n);
if (abs >= 1e8) return (n / 1e8).toFixed(2) + "亿";
if (abs >= 1e4) return (n / 1e4).toFixed(2) + "万";
return n.toFixed(0);
};

function cumulative(values, n) {
const out = new Array(values.length);
let sum = 0;
for (let i = 0; i < values.length; i++) {
    sum += (values[i] || 0);
    if (i >= n) sum -= (values[i - n] || 0);
    out[i] = sum;
}
return out;
}