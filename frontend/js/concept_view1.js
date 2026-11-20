// ─────────── 界面 1：三榜 + C7 策略选股 ───────────
// 默认按规则1.1 综合分降序（C7 口径）；点击表头可切换其它列
const v1SortState = { key: "c7_score", dir: "desc" };
let v1LastPicks = [];
let v1C7Meta = null;   // C7 选股元信息（阈值/口径/缓存状态）
// ⭐ 天梯榜独立排序状态（与交集榜互不影响）
const v1LadderSortState = { key: null, dir: null };   // null = 默认后端排序
let v1LadderLastStocks = [];
// ─── 颜色状态（每榜独立 + 全局共有）───
const V1_COLOR_ORDER = { red: 0, yellow: 1, blue: 2, white: 3 };

// 全局颜色状态（每次 loadView1 重新计算）
let v1Common2 = new Set();    // 任意两榜共有
let v1A5Strong = new Set();   // 归成比榜前5（只含 is_strong）
let v1P5 = new Set();         // 涨跌幅榜前5
let v1L5 = new Set();         // 涨停数榜前5
let v1Top5Union = new Set();  // 三榜前5并集（蓝色判断）

/** 从三榜数据算出 common3 + top5Union。 */
function v1ComputeGlobalColors(topAmount, topPct, topLimitUp) {
  // ── 归成比榜：只保留 is_strong（A>10% 且归成比>1.1）──
  const strongAmount = (topAmount || []).filter(it => it.is_strong !== false);
  const S = new Set(strongAmount.map(it => it.name));

  // ── 涨跌幅榜 / 涨停数榜全部概念 ──
  const P = new Set((topPct || []).map(it => it.name));
  const L = new Set((topLimitUp || []).map(it => it.name));

  // ── 归成比前5（strong）──
  v1A5Strong = new Set(strongAmount.slice(0, 5).map(it => it.name));

  // ── 涨跌幅前5 / 涨停数前5 ──
  v1P5 = new Set((topPct || []).slice(0, 5).map(it => it.name));
  v1L5 = new Set((topLimitUp || []).slice(0, 5).map(it => it.name));

  // ── 任意两榜共有 ──
  v1Common2 = new Set();
  for (const n of S) if (P.has(n) || L.has(n)) v1Common2.add(n);
  for (const n of P) if (L.has(n)) v1Common2.add(n);

  // ── 三榜前5并集（用于蓝色判断）──
  v1Top5Union = new Set([...v1A5Strong, ...v1P5, ...v1L5]);
}

/** 榜单行颜色：按【本榜前5】+【三榜共有】判定。 */
function v1GetColorByName(name) {
  // 红：归成比前5（strong）+ (涨跌幅前5 或 涨停数前5)
  if (v1A5Strong.has(name) && (v1P5.has(name) || v1L5.has(name))) {
    return "red";
  }
  // 黄：任意两榜共有
  if (v1Common2.has(name)) {
    return "yellow";
  }
  // 蓝：某榜前5
  if (v1Top5Union.has(name)) {
    return "blue";
  }
  return "white";
}

function v1ColorForRow(name, rank) {
  return v1GetColorByName(name);
}

function v1ColorForTag(name) {
  return v1GetColorByName(name);
}

function v1SortConcepts(cfs) {
  return (cfs || []).slice().sort((a, b) => {
    const ca = V1_COLOR_ORDER[v1ColorForTag(a)] ?? 3;
    const cb = V1_COLOR_ORDER[v1ColorForTag(b)] ?? 3;
    return ca - cb;
  });
}
const v1El = {
  marketTotal:  document.getElementById("v1MarketTotal"),
  amountList:   document.getElementById("v1AmountList"),
  pctList:      document.getElementById("v1PctList"),
  limitUpList:  document.getElementById("v1LimitUpList"),
  ladderList:   document.getElementById("v1LadderList"),
  picksList:    document.getElementById("v1PicksList"),
  leaderPop:    document.getElementById("leaderPop"),
  leaderCount:  document.getElementById("leaderCount"),
};

/** 用 DOM 安全渲染龙头浮层：每项「概念名 + score」，按 score 降序。 */
function v1RenderLeaderPop(leaders) {
  const pop = v1El.leaderPop;
  const cnt = v1El.leaderCount;
  if (!pop || !cnt) return;
  pop.replaceChildren();
  if (!Array.isArray(leaders) || leaders.length === 0) {
    cnt.textContent = "";
    const empty = document.createElement("div");
    empty.className = "leader-pop-empty";
    empty.textContent = "当日无龙头概念";
    pop.appendChild(empty);
    return;
  }
  cnt.textContent = leaders.length;
  // 后端已按 score 降序，这里再排一次确保口径稳妥（不改后端）
  const sorted = leaders.slice().sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0));
  const title = document.createElement("div");
  title.className = "leader-pop-title";
  title.textContent = `当日龙头概念（score ≥ 0.8）· 共 ${sorted.length} 个`;
  pop.appendChild(title);
  const list = document.createElement("div");
  list.className = "leader-pop-list";
  sorted.forEach((l, i) => {
    const row = document.createElement("div");
    row.className = "leader-pop-item";
    const rank = document.createElement("span");
    rank.className = "leader-pop-rank";
    rank.textContent = String(i + 1);
    const name = document.createElement("span");
    name.className = "leader-pop-name";
    name.textContent = l.name;
    const score = document.createElement("span");
    score.className = "leader-pop-score";
    score.textContent = Number(l.score).toFixed(4);
    row.appendChild(rank);
    row.appendChild(name);
    row.appendChild(score);
    list.appendChild(row);
  });
  pop.appendChild(list);
}
const v1FmtYi  = (v) => (v / 1e8).toFixed(2) + " 亿";
const v1FmtZY  = (v) => (v / 1e12).toFixed(2) + " 兆";
const v1FmtPct = (v) => {
  if (v == null || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
};
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

  el.innerHTML = items.map((it, idx) => {
    const w = Math.abs(opt.weight(it)) / maxAbs * 100;
    const inside = opt.inside(it) || "";
    const right  = opt.right(it)  || "";
    const fillCls = opt.fillClass(it) || "";
    const widthStyle = opt.centerAxis
      ? `width:${(w / 2).toFixed(2)}%`
      : `width:${w.toFixed(2)}%`;

    return `
      <div class="v1-row">
        <div class="v1-name v1-cname-${v1ColorForRow(it.name, idx)}"
             data-code="${v1Escape(it.code)}"
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
  if (!items || !items.length) {
    v1El.amountList.innerHTML = `<div class="v1-empty">无数据</div>`;
    return;
  }

  const maxRatio = Math.max(
    ...items.map(it => it.norm_amount_ratio || 0), 1e-9);

  v1El.amountList.innerHTML = items.map((it, idx) => {
    const ratio = it.norm_amount_ratio || 0;
    const w = (ratio / maxRatio * 100).toFixed(2);
    const amountPct = it.amount_pct != null
      ? it.amount_pct.toFixed(2) + "%" : "-";
    const amountStr = v1FmtYi(it.amount);

    // ⭐ 强弱判定（A>10% AND 归成比>1.1）
    const isStrong = it.is_strong !== false;
    const cnameCls = isStrong ? v1ColorForRow(it.name, idx) : "white";
    const rowCls = isStrong ? "" : "v1-row-weak";
    const weakTag = isStrong ? "" : `<span class="v1-weak-tag">补</span>`;

    const lu = it.limit_up_count || 0;
    const top = it.top_stock;
    let leader = `<span class="v1-meta-none">—</span>`;
    if (top) {
      const pct = (top.pct >= 0 ? "+" : "") + top.pct.toFixed(2) + "%";
      const cls = top.pct >= 0 ? "up" : "down";
      leader = `<span class="n" title="${v1Escape(top.name)}">${v1Escape(top.name)}</span>
                <span class="p ${cls}">${pct}</span>`;
    }

    return `
      <div class="v1-row v1-row-ratio ${rowCls}">
        <div class="v1-ratio" title="归成比 = 成交额占比 / 市值占比">
          <div class="v1-ratio-track">
            <div class="v1-ratio-fill" style="width:${w}%"></div>
          </div>
          <div class="v1-ratio-val">${ratio.toFixed(2)}</div>
        </div>
        <div class="v1-name v1-cname-${cnameCls}" data-code="${v1Escape(it.code)}"
             data-name="${v1Escape(it.name)}"
             title="${v1Escape(it.name)}">${v1Escape(it.name)}${weakTag}</div>
        <div class="v1-amount-pct" title="成交额占市场总成交额">${amountPct}</div>
        <div class="v1-right">${amountStr}</div>
        <div class="v1-meta">
          <div class="v1-meta-lu">涨停 ${lu} 只</div>
          <div class="v1-meta-top">领涨 ${leader}</div>
        </div>
      </div>`;
  }).join("");
}

function v1RenderPct(items) {
  // 过滤 null（虽然后端已经过滤过，双保险）
  const safe = (items || []).filter(it => it.pct_change != null);

  v1RenderList(v1El.pctList, safe, {
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

  v1El.limitUpList.innerHTML = items.map((it, idx) => {
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

    // ⭐ 按本榜排名 + 三榜共有 判定颜色
    const cnameCls = v1ColorForRow(it.name, idx);

    return `
      <div class="v1-row">
        <div class="v1-name v1-cname-${cnameCls}" data-code="${v1Escape(it.code)}"
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
function v1ImpClass(v, pct) {
  if (pct != null) {
    if (pct >= 0.85) return "high";
    if (pct >= 0.60) return "mid";
    return "low";
  }
  // 兼容旧数据
  if (v == null) return "low";
  if (v >= 1.0) return "high";
  if (v >= 0.6) return "mid";
  return "low";
}
function v1LadderSortedStocks(stocks) {
  if (!stocks || !stocks.length) return [];
  const { key, dir } = v1LadderSortState;
  if (!key || !dir) return stocks.slice();   // 无排序 → 保持后端默认顺序

  const arr = stocks.slice();
  arr.sort((a, b) => {
    const va = a[key], vb = b[key];
    const aNull = va == null || (typeof va === "number" && Number.isNaN(va));
    const bNull = vb == null || (typeof vb === "number" && Number.isNaN(vb));
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;

    let cmp;
    if (typeof va === "number" && typeof vb === "number") {
      cmp = va - vb;
    } else if (Array.isArray(va) && Array.isArray(vb)) {
      // concepts / matched_cfs_detail 等数组：按元素个数排
      cmp = va.length - vb.length;
    } else {
      cmp = String(va).localeCompare(String(vb), "zh");
    }
    return dir === "desc" ? -cmp : cmp;
  });
  return arr;
}

function v1UpdateLadderSortIndicator() {
  document.querySelectorAll(".v1-ladder-head > span[data-key]").forEach(el => {
    el.classList.remove("sorted-asc", "sorted-desc");
    if (el.dataset.key === v1LadderSortState.key && v1LadderSortState.dir) {
      el.classList.add(
        v1LadderSortState.dir === "desc" ? "sorted-desc" : "sorted-asc");
    }
  });
}
function v1RenderLadder(data) {
  // ⭐ 缓存原始数据（供排序点击重新渲染）
  v1LadderLastStocks = (data?.stocks) || [];

  // ⭐ 应用排序
  const stocks = v1LadderSortedStocks(v1LadderLastStocks);

  const headHtml = `
    <div class="v1-ladder-head">
      <span data-key="streak_days" style="text-align:center">连板</span>
      <span data-key="code">代码</span>
      <span data-key="name">名称</span>
      <span data-key="concepts">所属概念<span class="cfs-resize-handle" data-var="--ladder-cfs-w" title="拖动调整宽度">⋮</span></span>
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

  // ⭐ 后续渲染逻辑完全不变（保留原有 MAX_SHOW / tag / 列值）
  const MAX_SHOW = 5;
  const rowsHtml = stocks.map(s => {
    // ⭐ 构建 gld 映射（命中三榜的概念才有）
    const detailMap = {};
    const detailArr = Array.isArray(s.matched_cfs_detail)
      ? s.matched_cfs_detail
      : [];
    detailArr.forEach(d => {
      if (d && d.name) detailMap[d.name] = d.gld;
    });

    const sortedCfs = v1SortConcepts(s.concepts || []);
    const cfs = sortedCfs.slice(0, MAX_SHOW);
    const more = sortedCfs.length - cfs.length;
    const tags =
      cfs.map(c => {
        const color = v1ColorForTag(c);
        const gld = detailMap[c];
        const gldStr = gld != null ? `gld ${gld}` : "非三榜命中";
        return `<span class="v1-ladder-cf cf-${color}" title="${v1Escape(c)} · ${gldStr}">${v1Escape(c)}</span>`;
      }).join("")
      + (more > 0 ? `<span class="v1-ladder-cf more">+${more}</span>` : "");
    const titleAll = (s.concepts || []).join("、");

    const cls = (v) => v == null ? "" : (v >= 0 ? "up" : "down");
    const num = (v, d = 2) => v == null ? "-" : v.toFixed(d);

    // ⭐ 重要性渲染（含 pct 分档 + title）
    const impText = s.importance_index != null
      ? s.importance_index.toFixed(2) : "-";
    const impCls = v1ImpClass(s.importance_index, s.importance_pct);
    const impTitle = s.importance_pct != null
      ? `当日相对强度 ${(s.importance_pct * 100).toFixed(0)}%` +
        (s.rank_in_picks ? `，第 ${s.rank_in_picks}/${s.total_in_picks} 名` : "")
      : "";

    const streakCls = s.streak_days >= 5 ? "lv-high"
                     : s.streak_days === 4 ? "lv-4"
                     : s.streak_days === 3 ? "lv-3" : "lv-2";

    return `
      <div class="v1-ladder-row" data-code="${v1Escape(s.code)}" data-name="${v1Escape(s.name)}">
        <span class="v1-ladder-streak ${streakCls}">${s.streak_label || "-"}</span>
        <span class="v1-ladder-code" data-code="${v1Escape(s.code)}"
              data-name="${v1Escape(s.name)}">${v1Escape(s.code)}</span>
        <span class="v1-ladder-name" data-code="${v1Escape(s.code)}"
              data-name="${v1Escape(s.name)}"
              title="${v1Escape(s.name)}">${v1Escape(s.name)}</span>
        <span class="v1-ladder-cfs" title="${v1Escape(titleAll)}">${tags}</span>
        <span class="v1-ladder-imp ${impCls}" title="${impTitle}">${impText}</span>
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
  v1UpdateLadderSortIndicator();  
}
function v1RenderPicks(picks) {
  const sorted = (picks && picks.length) ? v1SortedPicks(picks) : [];

  // ── 表头：C7 口径列（排序指标用 ▼ 标记，默认综合分降序）──
  const headHtml = `
    <div class="v1-picks-head v1-c7-head">
      <span>#</span>
      <span data-key="code">代码</span>
      <span data-key="name">名称</span>
      <span data-key="source" title="C7=龙头严选池；C6=补充池（无龙头概念，覆盖更广）">来源</span>
      <span data-key="n_concepts">概念<span class="cfs-resize-handle" data-var="--picks-cfs-w" title="拖动调整宽度">⋮</span></span>
      <span data-key="c7_score" class="c7-sort-key" title="规则1.1 综合分=z(vol20)+z(ret_5d)+z(turnover_rate)">综合分</span>
      <span data-key="streak_days" class="c7-sort-key" title="规则5.1 连续在池天数，≥2日特殊标注">连池</span>
      <span data-key="close" style="text-align:right">收盘</span>
      <span data-key="pct_change" style="text-align:right">涨跌%</span>
      <span data-key="turnover_rate" class="c7-sort-key" style="text-align:right">换手%</span>
      <span data-key="vol20" class="c7-sort-key" style="text-align:right">波动20</span>
      <span data-key="ret_5d" class="c7-sort-key" style="text-align:right">涨5日%</span>
      <span data-key="lu_cnt_20d" style="text-align:right">涨停20</span>
      <span data-key="main_net_norm" style="text-align:right">资金强度</span>
      <span data-key="n_leader_concepts" style="text-align:right">龙头概念</span>
      <span data-key="fwd_max_5d" class="c7-fwd-col" style="text-align:right" title="复盘列：未来5日内最高价相对当日收盘的涨幅%；不足5交易日为-">+5日高%</span>
      <span data-key="fwd_max_10d" class="c7-fwd-col" style="text-align:right" title="复盘列：未来10日内最高价相对当日收盘的涨幅%；不足10交易日为-">+10日高%</span>
    </div>`;

  if (!sorted.length) {
    v1El.picksList.innerHTML =
      headHtml + `<div class="v1-picks-empty">该日无 C6/C7 策略选中标的（条件未满足）</div>`;
    return;
  }

  const MAX_SHOW = 8;
  const rowsHtml = sorted.map((p, i) => {
    const cfs = v1SortConcepts(p.matched_concepts || []);
    const shown = cfs.slice(0, MAX_SHOW);
    const more = cfs.length - shown.length;
    const tags =
      shown.map(c => {
        const color = v1ColorForTag(c);
        return `<span class="v1-pick-cf cf-${color}" title="${v1Escape(c)}">${v1Escape(c)}</span>`;
      }).join("")
      + (more > 0 ? `<span class="v1-pick-cf more">+${more}</span>` : "");
    const titleAll = cfs.join("、");

    const cls = (v) => v == null ? "" : (v >= 0 ? "up" : "down");
    const num = (v, d = 2) => v == null ? "-" : v.toFixed(d);
    // 规则5.1：连续在池≥2日 → 整行高亮 + 连池列徽标
    const streakCls = p.is_streak2 ? " c7-streak-row" : "";
    const streakBadge = p.is_streak2
      ? `<span class="c7-streak-badge" title="规则5.1：连续在池≥2日，优先关注">连${p.streak_days}日</span>`
      : `<span class="c7-streak-dim">${p.streak_days || 1}</span>`;
    const scoreCls = (i < Math.max(1, Math.ceil(sorted.length * 0.5))) ? "c7-score-top" : "";

    return `
      <div class="v1-pick-row${streakCls}" data-code="${v1Escape(p.code)}">
        <span class="col-idx">${i + 1}</span>
        <span class="col-code" title="打开个股详情 · ${v1Escape(p.code)}">${v1Escape(p.code)}</span>
        <span class="col-name" title="${v1Escape(p.name)}${p.is_streak2 ? ' · 连续在池≥2日' : ''}">${v1Escape(p.name)}${p.is_streak2 ? '<span class="c7-name-star">★</span>' : ''}</span>
        <span class="col-num"><span class="c7-src-badge ${p.source === 'C7' ? 'src-c7' : 'src-c6'}" title="${p.source === 'C7' ? 'C7 龙头严选池（命中龙头概念，命中率略高）' : 'C6 补充池（无龙头概念，覆盖约3倍）'}">${p.source === 'C7' ? 'C7' : 'C6'}</span></span>
        <span class="col-cfs" title="${v1Escape(titleAll)}">${tags}</span>
        <span class="col-num c7-score ${scoreCls}" title="综合分=z(波动)+z(5日涨)+z(换手)，越高越优先">${num(p.c7_score, 2)}</span>
        <span class="col-num c7-streak" title="连续满足C7的交易日数">${streakBadge}</span>
        <span class="col-num">${num(p.close)}</span>
        <span class="col-num ${cls(p.pct_change)}">${v1FmtPctShort(p.pct_change)}</span>
        <span class="col-num">${num(p.turnover_rate)}</span>
        <span class="col-num">${num(p.vol20)}</span>
        <span class="col-num ${cls(p.ret_5d)}">${v1FmtPctShort(p.ret_5d)}</span>
        <span class="col-num">${p.lu_cnt_20d}</span>
        <span class="col-num ${p.main_net_norm > 0 ? 'up' : ''}">${num(p.main_net_norm, 3)}</span>
        <span class="col-num">${p.n_leader_concepts}</span>
        <span class="col-num c7-fwd-col ${cls(p.fwd_max_5d)}" title="复盘列，非选股条件">${p.fwd_max_5d == null ? '-' : '+' + num(p.fwd_max_5d) + '%'}</span>
        <span class="col-num c7-fwd-col ${cls(p.fwd_max_10d)}" title="复盘列，非选股条件">${p.fwd_max_10d == null ? '-' : '+' + num(p.fwd_max_10d) + '%'}</span>
      </div>`;
  }).join("");

  v1El.picksList.innerHTML = headHtml + rowsHtml;
  v1UpdateSortIndicator();
}

let __loadView1Last = { date: null, ts: 0 };
async function loadView1() {
  const date = document.getElementById("v1Date").value;
  if (!date) return;
  // 同一日期 300ms 内不重复执行
  const now = Date.now();
  if (date === __loadView1Last.date && now - __loadView1Last.ts < 300) {
    return;
  }
  __loadView1Last = { date, ts: now };

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

    // ⭐ 前端自算（common3 + top5Union）
    v1ComputeGlobalColors(data.top_amount, data.top_pct, data.top_limit_up);
    v1RenderAmount(data.top_amount || [], data.market_total_amount || 0);
    v1RenderPct(data.top_pct || []);
    v1RenderLimitUp(data.top_limit_up || []);

    // 天梯榜
    v1RenderLadder(ladder);

    // ── C7 策略选股：独立请求，失败不影响三榜/天梯 ──
    v1LastPicks = [];
    v1C7Meta = null;
    try {
      const r3 = await fetch(`/api/concept/c7_picks?trade_date=${date}`);
      if (r3.ok) {
        const c7 = await r3.json();
        if (c7.ok && c7.data) {
          v1LastPicks = c7.data.picks || [];
          v1C7Meta = c7.meta || null;
        }
      }
    } catch (e3) {
      console.warn("[C7选股] 请求失败，列表留空", e3);
    }
    v1RenderPicks(v1LastPicks);

    // 天梯榜未缓存提示
    if (ladder.cached === false) {
      v1El.ladderList.innerHTML = `
        <div class="v1-ladder-empty">
          该日期未预计算天梯榜，请运行 <code>scripts\\recompute_picks.py</code>
        </div>`;
    }

    // 状态栏（不用 reduce）
    // ⭐ 第1项改进：右上角原「日期」替换为「当日龙头概念」（score>=0.8，降序）
    const ladderCount = Array.isArray(ladder.stocks)
      ? ladder.stocks.length : 0;
    const picksCount = v1LastPicks.length;
    const nC7 = v1LastPicks.filter(p => p.source === 'C7').length;
    const nC6 = v1LastPicks.filter(p => p.source === 'C6').length;
    const streakCount = v1LastPicks.filter(p => p.is_streak2).length;
    const leaders = Array.isArray(data.leader_concepts) ? data.leader_concepts : [];
    // ⭐ 龙头概念改为独立「龙头」按钮框 + hover 浮层，不再塞进 #status
    v1RenderLeaderPop(leaders);
    setStatus(`天梯 ${ladderCount} 只 · 策略池 ${picksCount} 只`
      + ` (C7：${nC7} / C6：${nC6})`);
  } catch (e) {
    console.error(e);
    setStatus("错误：" + e.message, true);
    v1RenderLeaderPop([]);
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

// ═══════════════════════════════════════════════════════════
// 安全绑定：所有事件都用 document 委托 + 可选链，不再依赖具体元素存在
// ═══════════════════════════════════════════════════════════

// ─── 交易日翻页 ───
let __shiftInFlight = false;
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
// ─── 天梯榜列头点击排序（document 委托，重建后生效）───
document.addEventListener("click", (e) => {
  const head = e.target.closest(".v1-ladder-head > span[data-key]");
  if (!head) return;

  e.stopPropagation();
  e.preventDefault();

  const key = head.dataset.key;
  if (v1LadderSortState.key === key && v1LadderSortState.dir) {
    // 同列：切换方向
    v1LadderSortState.dir = v1LadderSortState.dir === "desc" ? "asc" : "desc";
  } else {
    // 新列：默认降序
    v1LadderSortState.key = key;
    v1LadderSortState.dir = "desc";
  }

  if (v1LadderLastStocks.length) {
    v1RenderLadder({ stocks: v1LadderLastStocks });
  }
}, true);
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
// ─── C7选股榜点代码/名称 → 新窗口 index.html（携带规则5.1连池标注）───
document.addEventListener("click", (e) => {
  const cell = e.target.closest(".v1-pick-row .col-code, .v1-pick-row .col-name");
  if (!cell) return;
  const row = cell.closest(".v1-pick-row");
  const code = row?.dataset.code || row?.querySelector(".col-code")?.textContent.trim();
  if (!code) return;
  const center = document.getElementById("v1Date")?.value;
  // 从当前 C7 结果里找该股连池信息，带入详情页
  const pk = (v1LastPicks || []).find(p => p.code === code);
  const streak = pk ? (pk.streak_days || 1) : 1;
  const src = pk ? (pk.source || 'C7') : 'C7';
  const url = `index.html?code=${encodeURIComponent(code)}`
            + (center ? `&center=${encodeURIComponent(center)}` : "")
            + `&c7=1&streak=${streak}&psrc=${encodeURIComponent(src)}`;
  window.open(url, "_blank");
});
// ─── 天梯榜点代码/名称 → 新窗口 index.html（用 data-code 而不是文本） ───
document.addEventListener("click", (e) => {
  if (e.target.closest(".cfs-resize-handle")) return;
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
  if (e.target.closest(".cfs-resize-handle")) return;
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

// ─── 概念列拖拽手柄（document 委托，重建后也生效）───
document.addEventListener("mousedown", (e) => {
  const handle = e.target.closest(".cfs-resize-handle");
  if (!handle) return;

  e.preventDefault();
  e.stopPropagation();

  const varName = handle.dataset.var;
  if (!varName) return;

  const curW = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue(varName)
  ) || parseInt(getComputedStyle(handle.closest(".v1-picks-head, .v1-ladder-head"))
                  ?.gridTemplateColumns?.split(" ")[3]) || 170;

  const startX = e.clientX;
  let moved = false;

  document.body.style.userSelect = "none";
  document.body.style.cursor = "col-resize";

  const onMove = (ev) => {
    const dx = ev.clientX - startX;
    if (Math.abs(dx) > 2) moved = true;
    const w = Math.max(80, Math.min(600, curW + dx));
    document.documentElement.style.setProperty(varName, w + "px");
  };

  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    document.body.style.userSelect = "";
    document.body.style.cursor = "";

    // ⭐ 拖拽过就抑制后续 click（防止触发排序）
    if (moved) {
      const stopClick = (ce) => {
        ce.stopPropagation();
        ce.preventDefault();
      };
      document.addEventListener("click", stopClick, true);
      setTimeout(() => {
        document.removeEventListener("click", stopClick, true);
      }, 100);
    }
  };

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
});