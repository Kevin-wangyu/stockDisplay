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
    if (myToken !== loadToken) return; 
    if (data.min_date && data.max_date) {
    document.getElementById("start").value = data.min_date;
    document.getElementById("end").value = data.max_date;
    document.getElementById("tradeDate").value = data.max_date;
    }
} catch (e) { console.error("获取日期范围失败", e); }
}

async function load() {
const myToken = ++loadToken;   
const code = document.getElementById("code").value.trim();
if (!code) { setStatus("请输入股票代码", true); return; }

if (dataSource === "concept") {
    let trade_date = document.getElementById("tradeDate").value;
    if (!trade_date) {
    // 没有就现场拉一次 date_range，用 max_date
    await loadDateRange();
    trade_date = document.getElementById("tradeDate").value;
    if (!trade_date) {
        setStatus("请先选择股票", true);
        return;
    }
    }
    return loadConcept(code, trade_date);
}

const start = document.getElementById("start").value;
const end   = document.getElementById("end").value;

if (!start || !end) { setStatus("请选择日期范围", true); return; }
if (start > end)    { setStatus("开始日期不能晚于结束日期", true); return; }

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

    let rows, industryName = "", missing = [];

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
    missing = data.missing_dates || [];
    drawState.drawings = [];
    redrawDrawings();
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
    }

    state.rows = rows;
    showMissingWarning(missing);

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

    candleSeries.setData(rows.map(r => ({
        time: r.trade_date, open: r.open, high: r.high, low: r.low, close: r.close,
    })));

    // ── URL center 参数 → 在 K 线上标记跳转日 ──
    {
      const params = new URLSearchParams(location.search);
      const centerParam = params.get("center");
      let markDate = null;
      if (centerParam && rows.length) {
        for (let i = rows.length - 1; i >= 0; i--) {
          if (rows[i].trade_date <= centerParam) {
            markDate = rows[i].trade_date;
            break;
          }
        }
      }
      candleSeries.setMarkers(markDate ? [{
        time: markDate,
        position: "aboveBar",
        color: "#ffb454",
        shape: "arrowDown",
        text: "跳转日",
        size: 1.5,
      }] : []);
    }

    applyOverlays();
    applyPane("ind1", currentInd1Value);
    applyPane("ind2", currentInd2Value);
    applyPane("ind3", currentInd3Value);

    chart.timeScale().fitContent();
    setTimeout(drawNormLabels, 200);

    const tag = dataSource === "stock" ? `${code}` : `行业:${industryName}`;
    setStatus(`${tag} · ${rows.length} 个交易日 · ${rows[0].trade_date} ~ ${rows[rows.length - 1].trade_date}`);
} catch (e) {
    console.error(e);
    setStatus("错误：" + e.message, true);
} finally {
    btn.disabled = false;
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

async function loadConcept(code, trade_date) {
const myToken = ++loadToken;
btn.disabled = true;
setStatus("加载中…");

try {
    const url = `/api/stock/${encodeURIComponent(code)}/concept_panel`
            + `?trade_date=${trade_date}&days=120`;
    const res = await fetch(url);
    if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
    }
    if (myToken !== loadToken) return;
    const data = await res.json();
    if (myToken !== loadToken) return; 
    state.conceptData = data;

    renderBarColumn("marketBarCol", "全市场概念成交额 TOP15", data.market_top);
    renderBarColumn("stockBarCol",  "该股票所属概念（按成交额降序）", data.bars);
    renderStockPicks(data.stock_picks);

    setStatus(`${code} · ${trade_date} · ${data.bars.length} 个概念`);
} catch (e) {
    console.error(e);
    setStatus("错误：" + e.message, true);
    document.getElementById("marketBarCol").innerHTML = "";
    document.getElementById("stockBarCol").innerHTML = "";
    document.getElementById("picksTop5Col").innerHTML = "";
    document.getElementById("picksTop10Col").innerHTML = "";
} finally {
    if (myToken === loadToken) btn.disabled = false;
}
}

function renderBarColumn(containerId, title, bars) {
const el = document.getElementById(containerId);
if (!bars || !bars.length) {
    el.innerHTML = `<div class="cb-header">${title} · 无数据</div>`;
    return;
}
const maxAmount = Math.max(...bars.map(b => Math.abs(b.amount || 0)), 1);
el.innerHTML =
    `<div class="cb-header">${title}</div>` +
    bars.map(b => {
    const pct = Math.min(100, (Math.abs(b.amount || 0) / maxAmount) * 100);
    return `<div class="concept-bar-row">
        <div class="concept-bar-name" title="${b.name}">${b.name}</div>
        <div class="concept-bar-track">
        <div class="concept-bar-fill" style="width:${pct}%"></div>
        </div>
        <div class="concept-bar-value">${fmtBig(b.amount)}</div>
    </div>`;
    }).join("");
}

function renderStockPicks(picks) {
const renderList = (elId, title, list) => {
    const el = document.getElementById(elId);
    if (!list || !list.length) {
    el.innerHTML = `<div class="picks-header">${title}</div>
        <div style="color:var(--text-dim);font-size:12px">无匹配</div>`;
    return;
    }
    el.innerHTML =
    `<div class="picks-header">${title}</div>` +
    list.map(p => {
        const csText = (p.matched_concepts || []).join("、");
        return `<div class="pick-row">
        <span class="pick-code">${p.code}</span>
        <span class="pick-name" title="${p.name}">${p.name}</span>
        <span class="pick-concepts" title="${csText}">${csText}</span>
        <span class="pick-count">${p.match_count}</span>
        </div>`;
    }).join("");
};

renderList("picksTop5Col",  "TOP5 概念交集选股",  picks ? picks.top5  : []);
renderList("picksTop10Col", "TOP10 概念交集选股", picks ? picks.top10 : []);
}


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