// ─────────────── 画线模块 ───────────────

const drawCanvas = document.getElementById("drawCanvas");
const drawCtx = drawCanvas.getContext("2d");
const chartWrapEl = document.getElementById("chartWrap");

const drawState = {
tool: "none",
pending: null,           // 正在绘制的第一个点
drawings: [],            // [{type, points:[{time,price}...], color}]
hoverIndex: -1,          // erase 模式下鼠标悬停到哪条线
};

const DRAW_COLORS = {
hline: "#f0b90b",
vline: "#a855f7",
trend: "#2962ff",
ray:   "#26a69a",
};

// 调整 canvas 尺寸（含 DPR）
function resizeDrawCanvas() {
const dpr = window.devicePixelRatio || 1;
const w = chartWrapEl.clientWidth;
const h = chartWrapEl.clientHeight;
drawCanvas.width = w * dpr;
drawCanvas.height = h * dpr;
drawCanvas.style.width = w + "px";
drawCanvas.style.height = h + "px";
drawCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
redrawDrawings();
}

// 主重绘函数
function redrawDrawings() {
const w = drawCanvas.width / (window.devicePixelRatio || 1);
const h = drawCanvas.height / (window.devicePixelRatio || 1);
drawCtx.clearRect(0, 0, w, h);

// 已完成的线
drawState.drawings.forEach((d, idx) => {
    drawOne(d, idx === drawState.hoverIndex);
});

// 正在绘制的第一个点（趋势线/射线等待第二个点）
if (drawState.pending) {
    const { x, y } = drawState.pending.pixel;
    drawCtx.beginPath();
    drawCtx.arc(x, y, 4, 0, Math.PI * 2);
    drawCtx.fillStyle = "#2962ff";
    drawCtx.fill();
}
}

function drawOne(d, highlight) {
const pts = d.points;
if (!pts || !pts.length) return;

const toPixel = (pt) => {
    const x = chart.timeScale().timeToCoordinate(pt.time);
    const y = candleSeries.priceToCoordinate(pt.price);
    return { x, y };
};

const lineWidth = highlight ? 3 : 1.5;
const color = highlight ? "#ef5350" : d.color;

drawCtx.strokeStyle = color;
drawCtx.lineWidth = lineWidth;
drawCtx.setLineDash([]);

const W = drawCanvas.width / (window.devicePixelRatio || 1);
const H = drawCanvas.height / (window.devicePixelRatio || 1);

if (d.type === "hline") {
    const { y } = toPixel(pts[0]);
    if (y == null) return;
    drawCtx.beginPath();
    drawCtx.moveTo(0, y);
    drawCtx.lineTo(W, y);
    drawCtx.stroke();
} else if (d.type === "vline") {
    const { x } = toPixel(pts[0]);
    if (x == null) return;
    drawCtx.beginPath();
    drawCtx.moveTo(x, 0);
    drawCtx.lineTo(x, H);
    drawCtx.stroke();
} else if (d.type === "trend" || d.type === "ray") {
    const p1 = toPixel(pts[0]);
    const p2 = toPixel(pts[1]);
    if (p1.x == null || p2.x == null || p1.y == null || p2.y == null) return;

    if (d.type === "trend") {
    drawCtx.beginPath();
    drawCtx.moveTo(p1.x, p1.y);
    drawCtx.lineTo(p2.x, p2.y);
    drawCtx.stroke();
    } else {
    // 射线：从 p1 出发经过 p2 无限延长
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return;
    // 参数 t 取足够大，覆盖画布
    const tMax = 10000;
    drawCtx.beginPath();
    drawCtx.moveTo(p1.x, p1.y);
    drawCtx.lineTo(p1.x + dx * tMax, p1.y + dy * tMax);
    drawCtx.stroke();
    }
    // 两端小圆点
    drawCtx.fillStyle = color;
    [p1, p2].forEach(p => {
    drawCtx.beginPath();
    drawCtx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    drawCtx.fill();
    });
}
}

// 像素 → 数据点
function pixelToData(x, y) {
const time = chart.timeScale().coordinateToTime(x);
const price = candleSeries.coordinateToPrice(y);
return { time, price };
}

// 计算点到线段的距离
function distToSegment(px, py, x1, y1, x2, y2) {
const dx = x2 - x1;
const dy = y2 - y1;
const len2 = dx * dx + dy * dy;
if (len2 === 0) return Math.hypot(px - x1, py - y1);
let t = ((px - x1) * dx + (py - y1) * dy) / len2;
t = Math.max(0, Math.min(1, t));
const cx = x1 + t * dx;
const cy = y1 + t * dy;
return Math.hypot(px - cx, py - cy);
}

// erase 模式：判断鼠标悬停在哪条线上
function hitTest(x, y) {
const W = drawCanvas.width / (window.devicePixelRatio || 1);
const H = drawCanvas.height / (window.devicePixelRatio || 1);

for (let i = drawState.drawings.length - 1; i >= 0; i--) {
    const d = drawState.drawings[i];
    const pts = d.points;
    const p1 = pts[0];
    const x1 = chart.timeScale().timeToCoordinate(p1.time);
    const y1 = candleSeries.priceToCoordinate(p1.price);
    if (x1 == null || y1 == null) continue;

    if (d.type === "hline") {
    if (Math.abs(y - y1) < 6) return i;
    } else if (d.type === "vline") {
    if (Math.abs(x - x1) < 6) return i;
    } else {
    const p2 = pts[1];
    const x2 = chart.timeScale().timeToCoordinate(p2.time);
    const y2 = candleSeries.priceToCoordinate(p2.price);
    if (x2 == null || y2 == null) continue;
    if (distToSegment(x, y, x1, y1, x2, y2) < 6) return i;
    }
}
return -1;
}

// 鼠标点击 canvas
drawCanvas.addEventListener("click", (e) => {
const rect = drawCanvas.getBoundingClientRect();
const x = e.clientX - rect.left;
const y = e.clientY - rect.top;

if (drawState.tool === "none") return;

if (drawState.tool === "erase") {
    const idx = hitTest(x, y);
    if (idx >= 0) {
    drawState.drawings.splice(idx, 1);
    drawState.hoverIndex = -1;
    redrawDrawings();
    }
    return;
}

const pt = pixelToData(x, y);
if (pt.time == null || pt.price == null) return;

if (drawState.tool === "hline" || drawState.tool === "vline") {
    drawState.drawings.push({
    type: drawState.tool,
    points: [pt],
    color: DRAW_COLORS[drawState.tool],
    });
    redrawDrawings();
    return;
}

if (drawState.tool === "trend" || drawState.tool === "ray") {
    if (!drawState.pending) {
    drawState.pending = { data: pt, pixel: { x, y } };
    redrawDrawings();
    } else {
    drawState.drawings.push({
        type: drawState.tool,
        points: [drawState.pending.data, pt],
        color: DRAW_COLORS[drawState.tool],
    });
    drawState.pending = null;
    redrawDrawings();
    }
}
});

// 鼠标移动：erase 模式高亮
drawCanvas.addEventListener("mousemove", (e) => {
if (drawState.tool !== "erase") return;
const rect = drawCanvas.getBoundingClientRect();
const x = e.clientX - rect.left;
const y = e.clientY - rect.top;
const idx = hitTest(x, y);
if (idx !== drawState.hoverIndex) {
    drawState.hoverIndex = idx;
    redrawDrawings();
}
});

// Esc 取消正在绘制
document.addEventListener("keydown", (e) => {
if (e.key === "Escape" && drawState.pending) {
    drawState.pending = null;
    redrawDrawings();
}
});

// 工具按钮
document.querySelectorAll(".draw-btn[data-tool]").forEach(btn => {
btn.addEventListener("click", () => {
    const tool = btn.dataset.tool;
    drawState.tool = tool;
    drawState.pending = null;
    drawState.hoverIndex = -1;

    document.querySelectorAll(".draw-btn[data-tool]").forEach(b => {
    b.classList.toggle("active", b === btn);
    });

    chartWrapEl.classList.toggle("draw-mode", tool !== "none" && tool !== "erase");
    chartWrapEl.classList.toggle("erase-mode", tool === "erase");
    redrawDrawings();
});
});

document.getElementById("clearDrawings").addEventListener("click", () => {
drawState.drawings = [];
drawState.pending = null;
drawState.hoverIndex = -1;
redrawDrawings();
});

// 图表变化时重绘
chart.timeScale().subscribeVisibleLogicalRangeChange(() => redrawDrawings());
new ResizeObserver(() => resizeDrawCanvas()).observe(chartWrapEl);

// 初始化
resizeDrawCanvas();