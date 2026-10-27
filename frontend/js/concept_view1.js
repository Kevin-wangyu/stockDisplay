// ─────────── 界面 1：三榜 + 交集选股 ───────────
const v1SortState = { key: "importance_index", dir: "desc" };
let v1LastPicks = [];
const v1El = {
  marketTotal:  document.getElementById("v1MarketTotal"),
  amountList:   document.getElementById("v1AmountList"),
  pctList:      document.getElementById("v1PctList"),
  limitUpList:  document.getElementById("v1LimitUpList"),
  ladderList:   document.getElementById("v1LadderList"),
  picksList:    document.getElementById("v1PicksList"),
};
const v1FmtYi  = (v) => (v / 1e8).toFixed(2) + " 亿";
const v1FmtZY  = (v) => (v / 1e12).toFixed(2) + " 兆";
const v1FmtPct = (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
function v1FmtBigShort(v) {
  if (v == null) return "-";
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(0);
}
function v1FmtPctShort(v) {
  if (v == null) return "-";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}
function v1NetCell(net, norm) {
  const cls = net == null ? "" : (net >= 0 ? "up" : "down");
  const v1 = v1FmtBigShort(net);
  const v2 = norm == null ? "" : `(${norm.toFixed(2)}%)`;
  return `<span class="v1-net-val ${cls}">${v1}</span>
          <span class="v1-net-norm">${v2}</span>`;
}
function v1Escape(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function v1RenderMeta(it) {
  const lu = it.limit_up_count || 0;
  const top = it.top_stock;

  const luHtml = lu > 0
    ? `<div class="v1-meta-lu">涨停 ${lu} 只</div>`
    : `<div class="v1-meta-lu v1-meta-none">无涨停</div>`;

  let topHtml = "";
  if (top) {
    const cls = top.pct >= 0 ? "up" : "down";
    const pct = (top.pct >= 0 ? "+" : "") + top.pct.toFixed(2) + "%";
    topHtml = `<div class="v1-meta-top" title="${v1Escape(top.code)} ${v1Escape(top.name)} ${pct}">
      领涨 <span class="n">${v1Escape(top.name)}</span>
      <span class="p ${cls}">${pct}</span>
    </div>`;
  } else {
    topHtml = `<div class="v1-meta-top v1-meta-none">—</div>`;
  }
  return `<div class="v1-meta">${luHtml}${topHtml}</div>`;
}
function v1RenderList(el, items, opt) {
  if (!items || !items.length) {
    el.innerHTML = `<div class="v1-empty">无数据</div>`;
    return;
  }
  const maxAbs = Math.max(...items.map(it => Math.abs(opt.weight(it))), 1e-9);

  el.innerHTML = items.map(it => {
    const w = Math.abs(opt.weight(it)) / maxAbs * 100;
    const inside = opt.inside(it) || "";
    const right  = opt.right(it)  || "";
    const fillCls = opt.fillClass(it) || "";
    const widthStyle = opt.centerAxis
      ? `width:${(w / 2).toFixed(2)}%`
      : `width:${w.toFixed(2)}%`;

    return `
      <div class="v1-row">
        <div class="v1-name" data-code="${v1Escape(it.code)}"
             data-name="${v1Escape(it.name)}"
             title="${v1Escape(it.name)}">${v1Escape(it.name)}</div>
        <div class="v1-track${opt.centerAxis ? " center-axis" : ""}">
          <div class="v1-fill ${fillCls}" style="${widthStyle}">
            ${inside ? `<span class="v1-inside">${inside}</span>` : ""}
          </div>
        </div>
        <div class="v1-right">${right}</div>
        ${v1RenderMeta(it)}
      </div>`;
  }).join("");
}
function v1RenderAmount(items, marketTotal) {
  v1RenderList(v1El.amountList, items, {
    weight:    (it) => it.amount,
    fillClass: ()   => "fill-amount",
    inside:    (it) => marketTotal > 0
      ? (it.amount / marketTotal * 100).toFixed(2) + "%" : "",
    right:     (it) => v1FmtYi(it.amount),
  });
}
function v1RenderPct(items) {
  v1RenderList(v1El.pctList, items, {
    weight:     (it) => it.pct_change,
    fillClass:  (it) => it.pct_change >= 0 ? "pos" : "neg",
    inside:     ()   => "",
    right:      (it) => v1FmtPct(it.pct_change),
    centerAxis: true,
  });
}
function v1RenderLimitUp(items) {
  if (!items || !items.length) {
    v1El.limitUpList.innerHTML = `<div class="v1-empty">无数据</div>`;
    return;
  }
  const maxLu = Math.max(...items.map(it => it.limit_up_count || 0), 1);

  v1El.limitUpList.innerHTML = items.map(it => {
    const lu = it.limit_up_count || 0;
    const w = (lu / maxLu * 100).toFixed(2);
    const top = it.top_stock;
    let leader = `<span class="v1-meta-none">—</span>`;
    if (top) {
      const pct = (top.pct >= 0 ? "+" : "") + top.pct.toFixed(2) + "%";
      const cls = top.pct >= 0 ? "up" : "down";
      leader = `<span class="n" title="${v1Escape(top.name)}">${v1Escape(top.name)}</span>
                <span class="p ${cls}">${pct}</span>`;
    }
    return `
      <div class="v1-row">
        <div class="v1-name" data-code="${v1Escape(it.code)}"
             data-name="${v1Escape(it.name)}"
             title="${v1Escape(it.name)}">${v1Escape(it.name)}</div>
        <div class="v1-track">
          <div class="v1-fill fill-limitup" style="width:${w}%"></div>
        </div>
        <div class="v1-right">${lu} 只</div>
        <div class="v1-meta">
          <div class="v1-meta-lu">涨停 ${lu} 只</div>
          <div class="v1-meta-top">领涨 ${leader}</div>
        </div>
      </div>`;
  }).join("");
}
function v1SortedPicks(picks) {
  const { key, dir } = v1SortState;
  const arr = picks.slice();
  arr.sort((a, b) => {
    const va = a[key], vb = b[key];
    const aNull = va == null || (typeof va === "number" && Number.isNaN(va));
    const bNull = vb == null || (typeof vb === "number" && Number.isNaN(vb));
    // null 永远排最后，不受升降序影响
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;

    let cmp;
    if (typeof va === "number" && typeof vb === "number") {
      cmp = va - vb;
    } else {
      cmp = String(va).localeCompare(String(vb), "zh");
    }
    return dir === "desc" ? -cmp : cmp;
  });
  return arr;
}
function v1UpdateSortIndicator() {
  document.querySelectorAll(".v1-picks-head [data-key]").forEach(el => {
    el.classList.remove("sorted-asc", "sorted-desc");
    if (el.dataset.key === v1SortState.key) {
      el.classList.add(v1SortState.dir === "desc" ? "sorted-desc" : "sorted-asc");
    }
  });
}
function v1ImpClass(v) {
  if (v == null) return "low";
  if (v >= 1.3) return "high";   // 顶部一批
  if (v >= 0.9) return "mid";
  return "low";
}
function v1RenderLadder(data) {
  const stocks = data?.stocks || [];

  const headHtml = `
    <div class="v1-ladder-head">
      <span data-key="streak_days" style="text-align:center">连板</span>
      <span data-key="code">代码</span>
      <span data-key="name">名称</span>
      <span data-key="concepts">所属概念</span>
      <span data-key="importance_index" style="text-align:right">重要性</span>
      <span data-key="close" style="text-align:right">收盘</span>
      <span data-key="pct_change" style="text-align:right">涨跌%</span>
      <span data-key="turnover_rate" style="text-align:right">换手%</span>
      <span data-key="hot_rank" style="text-align:right">热度</span>
      <span data-key="huge_order_net" style="text-align:right">超大单</span>
      <span data-key="big_order_net" style="text-align:right">大单</span>
      <span data-key="medium_order_net" style="text-align:right">中单</span>
      <span data-key="small_order_net" style="text-align:right">小单</span>
      <span data-key="fwd_5d" style="text-align:right">+5日</span>
      <span data-key="fwd_10d" style="text-align:right">+10日</span>
      <span data-key="fwd_20d" style="text-align:right">+20日</span>
      <span data-key="fwd_30d" style="text-align:right">+30日</span>
    </div>`;

  if (!stocks.length) {
    v1El.ladderList.innerHTML =
      headHtml + `<div class="v1-ladder-empty">无二板及以上的股票</div>`;
    return;
  }

  const MAX_SHOW = 5;
  const rowsHtml = stocks.map(s => {
    const cfs = (s.concepts || []).slice(0, MAX_SHOW);
    const more = (s.concepts || []).length - cfs.length;
    const tags =
      cfs.map(c => `<span class="v1-pick-cf">${v1Escape(c)}</span>`).join("")
      + (more > 0 ? `<span class="v1-pick-cf more">+${more}</span>` : "");
    const titleAll = (s.concepts || []).join("、");

    const cls = (v) => v == null ? "" : (v >= 0 ? "up" : "down");
    const num = (v, d = 2) => v == null ? "-" : v.toFixed(d);
    const impText = s.importance_index != null
      ? s.importance_index.toFixed(2) : "-";
    const impCls = v1ImpClass(s.importance_index);
    const streakCls = s.streak_days >= 5 ? "lv-high"
                     : s.streak_days === 4 ? "lv-4"
                     : s.streak_days === 3 ? "lv-3" : "lv-2";

    return `
      <div class="v1-ladder-row" data-code="${v1Escape(s.code)}" data-name="${v1Escape(s.name)}">
        <span class="v1-ladder-streak ${streakCls}">${s.streak_label}</span>
        <span class="v1-ladder-code" data-code="${v1Escape(s.code)}"
              data-name="${v1Escape(s.name)}">${v1Escape(s.code)}</span>
        <span class="v1-ladder-name" data-code="${v1Escape(s.code)}"
              data-name="${v1Escape(s.name)}"
              title="${v1Escape(s.name)}">${v1Escape(s.name)}</span>
        <span class="v1-ladder-cfs" title="${v1Escape(titleAll)}">${tags}</span>
        <span class="v1-ladder-imp ${impCls}">${impText}</span>
        <span class="v1-ladder-num">${num(s.close)}</span>
        <span class="v1-ladder-num ${cls(s.pct_change)}">${v1FmtPctShort(s.pct_change)}</span>
        <span class="v1-ladder-num">${num(s.turnover_rate)}</span>
        <span class="v1-ladder-num">${s.hot_rank == null ? "-" : s.hot_rank.toFixed(0)}</span>
        <span class="v1-ladder-net">${v1NetCell(s.huge_order_net,   s.huge_order_net_norm)}</span>
        <span class="v1-ladder-net">${v1NetCell(s.big_order_net,    s.big_order_net_norm)}</span>
        <span class="v1-ladder-net">${v1NetCell(s.medium_order_net, s.medium_order_net_norm)}</span>
        <span class="v1-ladder-net">${v1NetCell(s.small_order_net,  s.small_order_net_norm)}</span>
        <span class="v1-ladder-num ${cls(s.fwd_5d)}">${v1FmtPctShort(s.fwd_5d)}</span>
        <span class="v1-ladder-num ${cls(s.fwd_10d)}">${v1FmtPctShort(s.fwd_10d)}</span>
        <span class="v1-ladder-num ${cls(s.fwd_20d)}">${v1FmtPctShort(s.fwd_20d)}</span>
        <span class="v1-ladder-num ${cls(s.fwd_30d)}">${v1FmtPctShort(s.fwd_30d)}</span>
      </div>`;
  }).join("");

  v1El.ladderList.innerHTML = headHtml + rowsHtml;
}
function v1RenderPicks(picks) {
  const sorted = (picks && picks.length) ? v1SortedPicks(picks) : [];

  // ── 表头（动态生成，和行一起进 #v1PicksList） ──
  const headHtml = `
    <div class="v1-picks-head">
      <span>#</span>
      <span data-key="code">代码</span>
      <span data-key="name">名称</span>
      <span data-key="match_count">命中概念</span>
      <span data-key="match_count" style="text-align:center">×N</span>
      <span data-key="importance_index" style="text-align:right">重要性</span>
      <span data-key="close" style="text-align:right">收盘</span>
      <span data-key="pct_change" style="text-align:right">涨跌%</span>
      <span data-key="turnover_rate" style="text-align:right">换手%</span>
      <span data-key="hot_rank" style="text-align:right">热度</span>
      <span data-key="huge_order_net" style="text-align:right">超大单</span>
      <span data-key="big_order_net" style="text-align:right">大单</span>
      <span data-key="medium_order_net" style="text-align:right">中单</span>
      <span data-key="small_order_net" style="text-align:right">小单</span>
      <span data-key="fwd_5d" style="text-align:right">+5日</span>
      <span data-key="fwd_10d" style="text-align:right">+10日</span>
      <span data-key="fwd_20d" style="text-align:right">+20日</span>
      <span data-key="fwd_30d" style="text-align:right">+30日</span>
    </div>`;

  if (!sorted.length) {
    v1El.picksList.innerHTML =
      headHtml + `<div class="v1-picks-empty">无命中个股</div>`;
    return;
  }

  const MAX_SHOW = 8;
  const rowsHtml = sorted.map((p, i) => {
    const cfs = p.matched_concepts || [];
    const shown = cfs.slice(0, MAX_SHOW);
    const more = cfs.length - shown.length;
    const tags =
      shown.map(c => `<span class="v1-pick-cf">${v1Escape(c)}</span>`).join("")
      + (more > 0 ? `<span class="v1-pick-cf more">+${more}</span>` : "");
    const titleAll = cfs.join("、");

    const cls = (v) => v == null ? "" : (v >= 0 ? "up" : "down");
    const num = (v, d = 2) => v == null ? "-" : v.toFixed(d);
    const impText = p.importance_index != null
      ? p.importance_index.toFixed(2) : "-";
    const impCls = p.importance_index != null
      ? (p.importance_index >= 1.3 ? "imp-high"
       : p.importance_index >= 0.9 ? "imp-mid" : "imp-low")
      : "imp-low";

    return `
      <div class="v1-pick-row">
        <span class="col-idx">${i + 1}</span>
        <span class="col-code">${v1Escape(p.code)}</span>
        <span class="col-name" title="${v1Escape(p.name)}">${v1Escape(p.name)}</span>
        <span class="col-cfs" title="${v1Escape(titleAll)}">${tags}</span>
        <span class="col-cnt">×${p.match_count}</span>
        <span class="col-num col-importance ${impCls}">${impText}</span>
        <span class="col-num">${num(p.close)}</span>
        <span class="col-num ${cls(p.pct_change)}">${v1FmtPctShort(p.pct_change)}</span>
        <span class="col-num">${num(p.turnover_rate)}</span>
        <span class="col-num">${p.hot_rank == null ? "-" : p.hot_rank.toFixed(0)}</span>
        <span class="col-net">${v1NetCell(p.huge_order_net,   p.huge_order_net_norm)}</span>
        <span class="col-net">${v1NetCell(p.big_order_net,    p.big_order_net_norm)}</span>
        <span class="col-net">${v1NetCell(p.medium_order_net, p.medium_order_net_norm)}</span>
        <span class="col-net">${v1NetCell(p.small_order_net,  p.small_order_net_norm)}</span>
        <span class="col-num ${cls(p.fwd_5d)}">${v1FmtPctShort(p.fwd_5d)}</span>
        <span class="col-num ${cls(p.fwd_10d)}">${v1FmtPctShort(p.fwd_10d)}</span>
        <span class="col-num ${cls(p.fwd_20d)}">${v1FmtPctShort(p.fwd_20d)}</span>
        <span class="col-num ${cls(p.fwd_30d)}">${v1FmtPctShort(p.fwd_30d)}</span>
      </div>`;
  }).join("");

  v1El.picksList.innerHTML = headHtml + rowsHtml;
  v1UpdateSortIndicator();
}
async function loadView1() {
  const date = document.getElementById("v1Date").value;
  if (!date) return;
  setStatus("加载中…");
  try {
    // 串行：第一个请求把所有缓存预热，第二个请求全部命中
    const r1 = await fetch(`/api/concept/date_top10?trade_date=${date}`);
    if (!r1.ok) {
      const err = await r1.json().catch(() => ({ detail: r1.statusText }));
      throw new Error(err.detail || `HTTP ${r1.status}`);
    }
    const data = await r1.json();

    const r2 = await fetch(
      `/api/concept/limit_up_ladder?trade_date=${date}&min_days=2`);
    const ladder = r2.ok ? await r2.json() : { stocks: [] };

    // 市场总成交额 + 5/20 日均
    v1El.marketTotal.textContent =
      (data.market_total_amount / 1e8).toFixed(2) + " 亿元";
    const avg5El  = document.getElementById("v1Avg5d");
    const avg20El = document.getElementById("v1Avg20d");
    if (avg5El)  avg5El.textContent  =
      ((data.market_avg_5d  || 0) / 1e8).toFixed(2) + " 亿";
    if (avg20El) avg20El.textContent =
      ((data.market_avg_20d || 0) / 1e8).toFixed(2) + " 亿";

    // 三榜
    v1RenderAmount(data.top_amount || [], data.market_total_amount || 0);
    v1RenderPct(data.top_pct || []);
    v1RenderLimitUp(data.top_limit_up || []);

    // 天梯榜
    v1RenderLadder(ladder);

    // 交集榜
    v1LastPicks = data.intersection_picks || [];
    v1RenderPicks(v1LastPicks);

    // 状态栏（不用 reduce）
    const ladderCount = Array.isArray(ladder.stocks)
      ? ladder.stocks.length : 0;
    const picksCount = Array.isArray(data.intersection_picks)
      ? data.intersection_picks.length : 0;
    setStatus(`${date} · 天梯 ${ladderCount} 只 · 交集股 ${picksCount} 只`);
  } catch (e) {
    console.error(e);
    setStatus("错误：" + e.message, true);
    v1El.marketTotal.textContent = "-";
    if (v1El.amountList)  v1El.amountList.innerHTML = "";
    if (v1El.pctList)     v1El.pctList.innerHTML = "";
    if (v1El.limitUpList) v1El.limitUpList.innerHTML = "";
    if (v1El.ladderList)  v1El.ladderList.innerHTML = "";
    if (v1El.picksList)   v1El.picksList.innerHTML = "";
  }
}

// 鼠标样式
document.querySelectorAll(".v1-pick-row .col-code").forEach(el => {
  el.style.cursor = "pointer";
  el.style.textDecoration = "underline";
});
// 绑定（先解绑再绑，防止重复）
(function bindDayNav() {
  const p = document.getElementById("v1PrevDay");
  const n = document.getElementById("v1NextDay");
  if (p) {
    const fresh = p.cloneNode(true);
    p.parentNode.replaceChild(fresh, p);
    fresh.addEventListener("click", () => v1ShiftDate("prev"));
  } else {
    console.warn("[bindDayNav] #v1PrevDay 不存在");
  }
  if (n) {
    const fresh = n.cloneNode(true);
    n.parentNode.replaceChild(fresh, n);
    fresh.addEventListener("click", () => v1ShiftDate("next"));
  } else {
    console.warn("[bindDayNav] #v1NextDay 不存在");
  }
})();

// ═══════════════════════════════════════════════════════════
// 安全绑定：所有事件都用 document 委托 + 可选链，不再依赖具体元素存在
// ═══════════════════════════════════════════════════════════

// ─── 交易日翻页 ───
async function v1ShiftDate(direction) {
  const d = document.getElementById("v1Date")?.value;
  if (!d) return;
  const btnP = document.getElementById("v1PrevDay");
  const btnN = document.getElementById("v1NextDay");
  if (btnP) btnP.disabled = true;
  if (btnN) btnN.disabled = true;
  try {
    const r = await fetch(
      `/api/trading_day_neighbor?date=${d}&direction=${direction}`);
    if (!r.ok) return;
    const j = await r.json();
    if (!j.date) return;
    document.getElementById("v1Date").value = j.date;
    if (typeof saveFormState === "function") saveFormState();
    await loadView1();
  } catch (e) {
    console.error("[v1ShiftDate] 失败", e);
  } finally {
    if (btnP) btnP.disabled = false;
    if (btnN) btnN.disabled = false;
  }
}
document.addEventListener("click", (e) => {
  const btn = e.target.closest("#v1PrevDay, #v1NextDay");
  if (!btn) return;
  v1ShiftDate(btn.id === "v1PrevDay" ? "prev" : "next");
});
// ─── 概念榜（成交额/涨跌幅/涨停数）点名称 → 跳界面 2 ───
document.addEventListener("click", (e) => {
  const nameEl = e.target.closest("#view1 .v1-name");
  if (!nameEl) return;
  const code = nameEl.dataset.code;
  const name = nameEl.dataset.name;
  const date = document.getElementById("v1Date")?.value;
  if (typeof window.switchToView2 === "function") {
    window.switchToView2(code, name, date);
  }
});
// ─── 交集榜点代码/名称 → 新窗口 index.html ───
document.addEventListener("click", (e) => {
  const cell = e.target.closest(".v1-pick-row .col-code, .v1-pick-row .col-name");
  if (!cell) return;
  const row = cell.closest(".v1-pick-row");
  const codeCell = row?.querySelector(".col-code");
  const code = codeCell?.textContent.trim();
  if (!code) return;
  const center = document.getElementById("v1Date")?.value;
  const url = `index.html?code=${encodeURIComponent(code)}`
            + (center ? `&center=${encodeURIComponent(center)}` : "");
  window.open(url, "_blank");
});
// ─── 天梯榜点代码/名称 → 新窗口 index.html（用 data-code 而不是文本） ───
document.addEventListener("click", (e) => {
  const cell = e.target.closest(".v1-ladder-code, .v1-ladder-name");
  if (!cell) return;
  const code = cell.dataset.code;
  if (!code) return;
  const center = document.getElementById("v1Date")?.value;
  const url = `index.html?code=${encodeURIComponent(code)}`
            + (center ? `&center=${encodeURIComponent(center)}` : "");
  window.open(url, "_blank");
});
// ─── 交集榜表头点击排序（capture 阶段） ───
document.addEventListener("click", (e) => {
  const head = e.target.closest(".v1-picks-head > span[data-key]");
  if (!head) return;
  e.stopPropagation();
  e.preventDefault();
  const key = head.dataset.key;
  if (v1SortState.key === key) {
    v1SortState.dir = v1SortState.dir === "desc" ? "asc" : "desc";
  } else {
    v1SortState.key = key;
    v1SortState.dir = "desc";
  }
  if (v1LastPicks.length) v1RenderPicks(v1LastPicks);
}, true);
// ─── 左右分割条拖拽（可选链 + 兜底） ───
(function initV1Splitter() {
  const splitter = document.getElementById("v1Splitter");
  const panel = document.querySelector(".v1-split-panel");
  const leftCol = document.querySelector(".v1-ladder-col");
  if (!splitter || !panel || !leftCol) {
    console.warn("[initV1Splitter] 元素缺失，跳过分隔条绑定",
      { splitter: !!splitter, panel: !!panel, leftCol: !!leftCol });
    return;
  }

  let dragging = false, startX = 0, startLeftW = 0;

  splitter.addEventListener("mousedown", (e) => {
    dragging = true;
    startX = e.clientX;
    startLeftW = leftCol.getBoundingClientRect().width;
    splitter.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const totalW = panel.getBoundingClientRect().width;
    const dx = e.clientX - startX;
    let newLeft = startLeftW + dx;
    newLeft = Math.max(220, Math.min(totalW - 380, newLeft));
    leftCol.style.flex = `0 0 ${newLeft}px`;
    leftCol.style.minWidth = "220px";
    leftCol.style.maxWidth = "none";
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  });
})();