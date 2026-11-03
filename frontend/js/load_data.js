// ─────────────── 数据加载（个股/行业） ───────────────

function setDefaultDateRange() {
const today = new Date();
const oneYearAgo = new Date(today);
oneYearAgo.setFullYear(today.getFullYear() - 1);

const fmt = (d) => {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
};

document.getElementById("start").value = fmt(oneYearAgo);
document.getElementById("end").value = fmt(today);
document.getElementById("tradeDate").value = fmt(today);
}

let rangeToken = 0;
async function loadDateRange() {
const myToken = ++rangeToken;
const code = document.getElementById("code").value.trim();
if (!code) return;
try {
    const res = await fetch(`/api/stock/${encodeURIComponent(code)}/date_range`);
    if (!res.ok) return;
    const data = await res.json();
    if (myToken !== rangeToken) return;
    if (data.min_date && data.max_date) {
    document.getElementById("start").value = data.min_date;
    document.getElementById("end").value = data.max_date;
    document.getElementById("tradeDate").value = data.max_date;
    }
} catch (e) { console.error("获取日期范围失败", e); }
}

let __loadInFlight = false;
async function load() {
  if (__loadInFlight) {
    console.warn("[load] 已有请求进行中，忽略重复调用");
    return;
  }
  __loadInFlight = true;
  try {
    const myToken = ++loadToken;
    const code = document.getElementById("code").value.trim();
    if (!code) { setStatus("请输入股票代码", true); return; }

    const start = document.getElementById("start").value;
    const end   = document.getElementById("end").value;

    if (!start || !end) { setStatus("请选择日期范围", true); return; }
    if (start > end)    { setStatus("开始日期不能晚于结束日期", true); return; }

    // ⭐ 概念模式：走新分支
    if (dataSource === "concept") {
        return loadConceptMode(code, start, end, myToken);
    }

    // 下面的 stock / industry 逻辑保持不变
    btn.disabled = true;
    setStatus("加载中…");

    try {
        const wanted = Array.from(new Set([
        ...OVERLAY_KEYS,
        ...BOLL_KEYS,
        ...state.indicators
            .filter(i => {
            const s = i.scope || "all";
            return s === "all" || s === dataSource;
            })
            .map(i => i.name),
        ]));

        let rows, industryName = "";
        let missingDates = [];
        let suspectedSuspension = [];

        if (dataSource === "stock") {
            const url = `/api/stock/${encodeURIComponent(code)}`
                        + `?start=${start}&end=${end}&indicators=${wanted.join(",")}`;
            const res = await fetch(url);
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const data = await res.json();
            if (myToken !== loadToken) return;
            rows = data.rows;
            missingDates = data.missing_dates || [];
            suspectedSuspension = data.suspected_suspension || [];
        } else {
            const url = `/api/stock/${encodeURIComponent(code)}/industry_chart`
                        + `?start=${start}&end=${end}&indicators=${wanted.join(",")}`;
            const res = await fetch(url);
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const data = await res.json();
            if (myToken !== loadToken) return;
            rows = data.rows;
            industryName = data.industry_name || "";
            // industry_chart 接口不返回 missing 信息
            missingDates = [];
            suspectedSuspension = [];
        }

        drawState.drawings = [];
        redrawDrawings();

        state.rows = rows;
        showMissingWarning(missingDates, suspectedSuspension);

        if (!rows.length) {
            candleSeries.setData([]);
            candleSeries.setMarkers([]);
            for (const k in overlaySeries) overlaySeries[k].setData([]);
            rebuildPane("ind1", currentInd1Value, []);
            rebuildPane("ind2", currentInd2Value, []);
            rebuildPane("ind3", currentInd3Value, []);
            chart.timeScale().fitContent();
            setStatus("该区间无数据");
            setTimeout(drawNormLabels, 60);
            return;
        }

        // ── 健壮的日期标准化（兼容 string / ISO / Date / timestamp） ──
        const _normDate = (v) => {
        if (v == null) return null;
        if (v instanceof Date) {
            if (isNaN(v.getTime())) return null;
            const y = v.getFullYear();
            const m = String(v.getMonth() + 1).padStart(2, "0");
            const d = String(v.getDate()).padStart(2, "0");
            return `${y}-${m}-${d}`;
        }
        if (typeof v === "number") {
            const ms = v < 1e10 ? v * 1000 : v;
            const d = new Date(ms);
            if (isNaN(d.getTime())) return null;
            return d.toISOString().slice(0, 10);
        }
        const s = String(v);
        const match = s.match(/^(\d{4}-\d{2}-\d{2})/);
        return match ? match[1] : null;
        };

        // ── 严格过滤：日期合法 + OHLC 均 > 0 ──
        // ── 深度标准化：兼容 null / 0 / 字符串日期 ──
        const _normCandle = (r) => {
        if (!r || typeof r !== "object") return null;
        const rawTime = r.trade_date;
        if (rawTime == null) return null;
        const time = String(rawTime).slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(time)) return null;

        const o = r.open, h = r.high, l = r.low, c = r.close;
        // 严格过滤：任何一个为 null / undefined / 空串 直接丢弃
        if (o == null || h == null || l == null || c == null) return null;

        const on = Number(o), hn = Number(h), ln = Number(l), cn = Number(c);
        if (!Number.isFinite(on) || !Number.isFinite(hn)
            || !Number.isFinite(ln) || !Number.isFinite(cn)) return null;
        // 过滤 0 值和负数
        if (on <= 0 || hn <= 0 || ln <= 0 || cn <= 0) return null;

        return { time, open: on, high: hn, low: ln, close: cn };
        };

        const _map = new Map();
        const _dropped = [];
        for (const r of rows) {
        const item = _normCandle(r);
        if (item) _map.set(item.time, item);
        else _dropped.push(r);
        }
        const candleData = Array.from(_map.values())
        .sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);

        if (_dropped.length) {
        console.warn(`[load] 过滤 ${_dropped.length} 行异常 K 线，样例:`, _dropped[0]);
        }

        if (!candleData.length) {
        candleSeries.setData([]);
        candleSeries.setMarkers([]);
        for (const k in overlaySeries) overlaySeries[k].setData([]);
        rebuildPane("ind1", currentInd1Value, []);
        rebuildPane("ind2", currentInd2Value, []);
        rebuildPane("ind3", currentInd3Value, []);
        chart.timeScale().fitContent();
        setStatus("该区间数据无效（OHLC 异常）");
        return;
        }

        try {
        candleSeries.setData(candleData);
        } catch (e) {
        console.error("[load] candleSeries.setData 失败", e);
        console.error("[load] 最后 5 行:", candleData.slice(-5));
        console.error("[load] 首行:", candleData[0]);
        throw e;
        }

        // ── URL center 参数 → 在 K 线上标记跳转日 ──
        try {
        const params = new URLSearchParams(location.search);
        const centerParam = params.get("center");
        const candleDates = new Set(candleData.map(d => d.time));
        let markDate = null;

        if (centerParam) {
            for (let i = candleData.length - 1; i >= 0; i--) {
            if (candleData[i].time <= centerParam) {
                markDate = candleData[i].time;
                break;
            }
            }
        }

        if (markDate && candleDates.has(markDate)) {
            candleSeries.setMarkers([{
            time: markDate,
            position: "aboveBar",
            color: "#ffb454",
            shape: "arrowDown",
            text: "跳转日",
            }]);
        } else {
            candleSeries.setMarkers([]);
        }
        } catch (e) {
        console.warn("[setMarkers] 失败", e);
        try { candleSeries.setMarkers([]); } catch (_) {}
        }
        

        applyOverlays();
        applyPane("ind1", currentInd1Value);
        applyPane("ind2", currentInd2Value);
        applyPane("ind3", currentInd3Value);

        chart.timeScale().fitContent();
        setTimeout(drawNormLabels, 200);

        // AMO 模式开着：加载完 K 线后重拉 amo + 重新应用隐藏
        if (typeof amoState !== "undefined" && amoState.visible) {
          setTimeout(() => {
            setAmoVisible(true);   // 重设价格轴 + 隐藏 3 pane
            loadAmoData();
          }, 300);
        }

        if (amoState.visible) {
            setTimeout(loadAmoData, 300);
        }

        const tag = dataSource === "stock" ? `${code}` : `行业:${industryName}`;
        setStatus(`${tag} · ${rows.length} 个交易日 · ${rows[0].trade_date} ~ ${rows[rows.length - 1].trade_date}`);
    } catch (e) {
        console.error(e);
        setStatus("错误：" + e.message, true);
    } finally {
        btn.disabled = false;
    }
  } finally {
    __loadInFlight = false;
  }
}

function applyPane(paneKey, name) {
    if (!name) return;
    if (!state.rows.length) { rebuildPane(paneKey, name, []); return; }

    const isHist = isHistogramRedGreen(name);
    const data = state.rows.map(r => {
        const v = r[name];
        if (isHist) {
        let color;
        if (name === "volume") {
            color = r.close >= r.open ? COLORS.upFill : COLORS.downFill;
        } else {
            color = v >= 0 ? COLORS.upFill : COLORS.downFill;
        }
        return { time: r.trade_date, value: v, color };
        }
        return { time: r.trade_date, value: v };
    }).filter(d => d.value != null);

    rebuildPane(paneKey, name, data);
    setTimeout(drawNormLabels, 60);
}

// ─────────────── 数据加载（概念） ───────────────


// ─────────────── 事件绑定 ───────────────

btn.addEventListener("click", load);

document.getElementById("fit").addEventListener("click", () => {
chart.timeScale().fitContent();
});

const codeInput = document.getElementById("code");
codeInput.addEventListener("blur", loadDateRange);
codeInput.addEventListener("keydown", async (e) => {
if (e.key === "Enter") {
    await loadDateRange();
    await load();
}
});

document.querySelectorAll('input[name="datasource"]').forEach(el => {
el.addEventListener("change", async () => {
    dataSource = el.value;
    document.querySelectorAll(".radio-toggle").forEach(x => {
    x.classList.toggle("active", x.querySelector("input").checked);
    });
    updateUIByDataSource();

    if (dataSource === "concept") {
    await loadDateRange();     // ← 先同步日期
    } else {
    updateIndicatorOptions();
    }
    load();
    });
});

// ─── 交易日翻页 ───
async function shiftTradingRange(direction) {
  const end = document.getElementById("end").value;
  if (!end) return;
  const btnP = document.getElementById("prevDay");
  const btnN = document.getElementById("nextDay");
  btnP.disabled = btnN.disabled = true;
  try {
    const r = await fetch(
      `/api/trading_day_neighbor?date=${end}&direction=${direction}`);
    if (!r.ok) return;
    const j = await r.json();
    if (!j.date) return;

    const newEnd = j.date;
    // 用新 end 重算 start（保持前 400 交易日跨度）
    const r2 = await fetch(
      `/api/trading_days_around?date=${newEnd}&before=400&after=0`);
    if (r2.ok) {
      const j2 = await r2.json();
      document.getElementById("start").value = j2.start;
      document.getElementById("end").value   = j2.end;
    } else {
      document.getElementById("end").value = newEnd;
    }
    await load();
  } catch (e) {
    console.error("翻页失败", e);
  } finally {
    btnP.disabled = btnN.disabled = false;
  }
}
document.getElementById("prevDay").addEventListener("click",
  () => shiftTradingRange("prev"));
document.getElementById("nextDay").addEventListener("click",
  () => shiftTradingRange("next"));

  // ═══════════════════════════════════════════════════════════
// AMO 买卖结构
// ═══════════════════════════════════════════════════════════

async function loadAmoData() {
  console.log("[AMO] loadAmoData called, visible:", amoState.visible);   // ⭐
  if (!amoState.visible) return;
  const code = document.getElementById("code").value.trim();
  const start = document.getElementById("start").value;
  const end = document.getElementById("end").value;
  if (!code || !start || !end) return;

  try {
    const url = `/api/amo/chart?code=${encodeURIComponent(code)}`
              + `&start=${start}&end=${end}`;
    const res = await fetch(url);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    amoState.rows = data.rows || [];

    // 更新 helper 数据的 time 轴（用于坐标系定标）
    if (amoState.rows.length) {
      amoState.helper.setData(
        amoState.rows.map(r => ({ time: r.trade_date, value: 0 }))
      );
      amoUpdateScale();
    } else {
      amoState.helper.setData([]);
    }

    setTimeout(() => {
      renderAmoCanvas();
    }, 100);
  } catch (e) {
    console.error("[amo] 加载失败", e);
  }
}

function setAmoVisible(visible) {
  amoState.visible = visible;
  const wrap = document.getElementById("chartWrap");
  const sideCtrl = document.getElementById("amoSideCtrl");

  if (visible) {
    _initAmoHelper(); 
    wrap.classList.add("amo-visible");
    if (sideCtrl) sideCtrl.style.display = "inline-flex";

    // 隐藏 3 个指标
    for (const k of ["ind1", "ind2", "ind3"]) {
      if (paneRefs[k].series) {
        paneRefs[k].series.applyOptions({ visible: false });
      }
      chart.priceScale(k).applyOptions({
        visible: false,
        scaleMargins: { top: 0.99, bottom: 0.0 },
      });
    }

    // K 线缩到上半部分（2% ~ 40%）
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.02, bottom: 0.60 },
    });

    // amo helper 完全隐藏（只用来定 x 轴，y 轴不用它了）
    chart.priceScale("amo").applyOptions({ visible: false });
        // 立即清空归一化标注 canvas
    requestAnimationFrame(drawNormLabels);

    loadAmoData();
  } else {
    wrap.classList.remove("amo-visible");
    if (sideCtrl) sideCtrl.style.display = "none";
    amoClearCanvas();
    setTimeout(drawNormLabels, 100);

    for (const k of ["ind1", "ind2", "ind3"]) {
      if (paneRefs[k].series) {
        paneRefs[k].series.applyOptions({ visible: true });
      }
      chart.priceScale(k).applyOptions({
        visible: true,
        scaleMargins: PANES[k],
      });
    }
    chart.priceScale("right").applyOptions({ scaleMargins: PANES.price });
    chart.priceScale("amo").applyOptions({ visible: false });
  }

  requestAnimationFrame(() => {
    chart.applyOptions({
      width:  chartEl.clientWidth,
      height: chartEl.clientHeight,
    });
    requestAnimationFrame(() => {
      if (visible) renderAmoCanvas();
    });
  });
}

function setAmoMode(mode) {
  amoState.mode = mode;
  document.querySelectorAll("#amoModeGroup .ma-toggle").forEach(el => {
    el.classList.toggle("active", el.dataset.mode === mode);
  });
  amoUpdateScale();
  setTimeout(renderAmoCanvas, 60);
}
function setAmoSide(side) {
  amoState.side = side;
  document.querySelectorAll("#amoSideCtrl .seg-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.side === side));
  setTimeout(renderAmoCanvas, 30);
  try {
    localStorage.setItem("amo_side", side);
  } catch (e) {}
}

document.querySelectorAll("#amoSideCtrl .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => setAmoSide(btn.dataset.side));
});
// 绑定事件
// AMO 三段式开关
document.querySelectorAll("#amoSegCtrl .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.amo;
    document.querySelectorAll("#amoSegCtrl .seg-btn").forEach(b =>
      b.classList.toggle("active", b === btn));

    if (mode === "off") {
      setAmoVisible(false);
    } else {
      setAmoVisible(true);
      setAmoMode(mode);
    }
  });
});

document.querySelectorAll('input[name="amomode"]').forEach(el => {
  el.addEventListener("change", () => setAmoMode(el.value));
});

// ═══════════════════════════════════════════════════════════
// 概念模式：左侧候选栏 + 右侧 K 线
// ═══════════════════════════════════════════════════════════

const conceptState = {
    concepts: [],
    active: null,
};

function _csEscape(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderConceptSidebar(concepts) {
    const listEl = document.getElementById("conceptList");
    const countEl = document.getElementById("csCount");
    countEl.textContent = `(${concepts.length})`;

    if (!concepts.length) {
        listEl.innerHTML = `<div class="cs-item" style="cursor:default;color:#4b515c">该股无概念数据</div>`;
        return;
    }

    listEl.innerHTML = concepts.map(c => `
        <div class="cs-item" data-name="${_csEscape(c)}" title="${_csEscape(c)}">${_csEscape(c)}</div>
    `).join("");
}

function highlightConcept(name) {
    document.querySelectorAll("#conceptList .cs-item").forEach(el => {
        el.classList.toggle("active", el.dataset.name === name);
    });
}

async function loadConceptMode(code, start, end, myToken) {
    btn.disabled = true;
    setStatus("加载中…");

    try {
        // 1. 拿该股所属概念列表
        const res = await fetch(`/api/stock/${encodeURIComponent(code)}/sectors`);
        if (!res.ok) {
            throw new Error(`获取概念列表失败 HTTP ${res.status}`);
        }
        const data = await res.json();
        if (myToken && myToken !== loadToken) return;

        const concepts = (data.concepts || []).map(c => c.name).filter(Boolean);
        conceptState.concepts = concepts;
        renderConceptSidebar(concepts);

        if (!concepts.length) {
            setStatus(`${code} 无概念数据`, true);
            clearConceptChart();
            return;
        }

        // 2. 保持之前选中的概念（如果还在列表里），否则选第一个
        let target = conceptState.active;
        if (!concepts.includes(target)) target = concepts[0];
        conceptState.active = target;
        highlightConcept(target);

        // 3. 加载该概念的 K 线
        await loadConceptChart(target, start, end, myToken);

    } catch (e) {
        console.error(e);
        setStatus("错误：" + e.message, true);
    } finally {
        btn.disabled = false;
    }
}

function clearConceptChart() {
    candleSeries.setData([]);
    candleSeries.setMarkers([]);
    for (const k in overlaySeries) overlaySeries[k].setData([]);
    rebuildPane("ind1", currentInd1Value, []);
    rebuildPane("ind2", currentInd2Value, []);
    rebuildPane("ind3", currentInd3Value, []);
    chart.timeScale().fitContent();
}

async function loadConceptChart(conceptName, start, end, myToken) {
    setStatus(`加载概念「${conceptName}」…`);

    try {
        // 指标集：overlay + BOLL + 所有 scope 允许的指标
        const wanted = Array.from(new Set([
            ...OVERLAY_KEYS,
            ...BOLL_KEYS,
            ...state.indicators
                .filter(i => {
                    const s = i.scope || "all";
                    return s === "all" || s === "industry" || s === "concept";
                })
                .map(i => i.name),
        ]));

        const url = `/api/sector/concept/${encodeURIComponent(conceptName)}`
                  + `?start=${start}&end=${end}&indicators=${wanted.join(",")}`;
        const res = await fetch(url);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const rows = await res.json();
        if (myToken && myToken !== loadToken) return;

        // 过滤 OHLC 异常行
        state.rows = (rows || []).filter(r => {
            const t = String(r.trade_date ?? "").slice(0, 10);
            if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return false;
            return [r.open, r.high, r.low, r.close].every(v =>
                v != null && Number.isFinite(Number(v)));
        });

        if (!state.rows.length) {
            clearConceptChart();
            setStatus(`概念「${conceptName}」该区间无数据`);
            return;
        }

        candleSeries.setData(state.rows.map(r => ({
            time: r.trade_date,
            open: Number(r.open),
            high: Number(r.high),
            low:  Number(r.low),
            close:Number(r.close),
        })));
        candleSeries.setMarkers([]);

        applyOverlays();
        applyPane("ind1", currentInd1Value);
        applyPane("ind2", currentInd2Value);
        applyPane("ind3", currentInd3Value);

        chart.timeScale().fitContent();

        const first = state.rows[0].trade_date;
        const last  = state.rows[state.rows.length - 1].trade_date;
        setStatus(`概念: ${conceptName} · ${state.rows.length} 个交易日 · ${first} ~ ${last}`);
    } catch (e) {
        console.error(e);
        setStatus("错误：" + e.message, true);
    }
}

// 侧栏点击切换
document.getElementById("conceptList").addEventListener("click", async (e) => {
    const item = e.target.closest(".cs-item");
    if (!item || !item.dataset.name) return;
    const name = item.dataset.name;
    if (name === conceptState.active) return;

    conceptState.active = name;
    highlightConcept(name);

    const start = document.getElementById("start").value;
    const end = document.getElementById("end").value;
    if (!start || !end) return;

    await loadConceptChart(name, start, end, null);
});