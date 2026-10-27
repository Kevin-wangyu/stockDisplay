// ─────────── 入口 ───────────

const statusEl = document.getElementById("status");
const setStatus = (m, e = false) => {
  statusEl.textContent = m;
  statusEl.classList.toggle("error", e);
};

const getToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
};

// tab 切换
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach(b =>
      b.classList.toggle("active", b === btn));
    document.querySelectorAll(".view-panel").forEach(p =>
      p.classList.remove("active"));
    document.getElementById(tab).classList.add("active");

    document.getElementById("view1DateGroup").style.display =
      tab === "view1" ? "" : "none";
    document.getElementById("view2FilterGroup").style.display =
      tab === "view2" ? "" : "none";
    document.getElementById("view2IndicatorRow").style.display =
      tab === "view2" ? "" : "none";

    // 从界面 2 切回界面 1 → 界面 1 的日期跟随界面 2
    if (tab === "view1") {
      const v2Date = document.getElementById("v2Date").value;
      const v1Date = document.getElementById("v1Date").value;
      if (v2Date && v2Date !== v1Date) {
        document.getElementById("v1Date").value = v2Date;
        loadView1();
      }
    }

    setTimeout(saveFormState, 0);
  });
});

// 日期 change
document.getElementById("v1Date").addEventListener("change", loadView1);

// 指标下拉填充（阶段 4 会用到）
async function fillIndicatorDropdowns() {
  try {
    const [r1, r2] = await Promise.all([
      fetch("/api/indicators"),
      fetch("/api/indicator_groups"),
    ]);
    const all  = await r1.json();
    const grps = await r2.json();

    v2IndState.indicators       = all.filter(x => x.type === "sql");
    v2IndState.cumulativeGroups = grps.cumulative_groups || [];

    initV2IndicatorDropdowns();
  } catch (e) {
    console.error("填充指标下拉失败", e);
  }
}

// 回车触发
document.getElementById("v1Date").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadView1();
});
document.getElementById("v2Code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") v2Query();
});
document.getElementById("v2Date").addEventListener("keydown", (e) => {
  if (e.key === "Enter") v2Query();
});
document.getElementById("v2Start").addEventListener("keydown", (e) => {
  if (e.key === "Enter") v2Query();
});
document.getElementById("v2End").addEventListener("keydown", (e) => {
  if (e.key === "Enter") v2Query();
});


// ─── localStorage 记忆 ───
const LS_KEY = "concept_page_state_v1";

function saveFormState() {
  const state = {
    v1Date:  document.getElementById("v1Date").value,
    v2Code:  document.getElementById("v2Code").value,
    v2Date:  document.getElementById("v2Date").value,
    v2Start: document.getElementById("v2Start").value,
    v2End:   document.getElementById("v2End").value,
    v2Ind1:  v2IndCurrent.ind1,
    v2Ind2:  v2IndCurrent.ind2,
    v2Ind3:  v2IndCurrent.ind3,
    tab:     document.querySelector(".tab-btn.active")?.dataset.tab || "view1",
  };
  try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch (e) {}
}

function loadFormState() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch (e) { return {}; }
}

// 各输入框变化时保存
["v1Date", "v2Code", "v2Date", "v2Start", "v2End"].forEach(id => {
  document.getElementById(id).addEventListener("change", saveFormState);
});

// tab 切换时保存
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => setTimeout(saveFormState, 0));
});

// 指标变化时保存（在 initV2IndicatorDropdowns 完成后再挂）
setTimeout(() => {
  for (const key of ["ind1", "ind2", "ind3"]) {
    const n = key.slice(-1);
    const root = document.getElementById(`v2IndDd${n}`);
    if (root) root.addEventListener("click", () => setTimeout(saveFormState, 0));
  }
}, 0);

// 覆盖 bootstrap：初始化时读 localStorage
(async () => {
  await fillIndicatorDropdowns();

  const saved = loadFormState();
  const today = getToday();

  document.getElementById("v1Date").value = saved.v1Date || today;
  document.getElementById("v2Date").value = saved.v2Date || today;
  document.getElementById("v2Code").value = saved.v2Code || "";
  document.getElementById("v2Start").value = saved.v2Start || "";
  document.getElementById("v2End").value   = saved.v2End   || "";

  // 恢复指标
  if (saved.v2Ind1) {
    v2IndCurrent.ind1 = saved.v2Ind1;
    v2IndDds.ind1.setValue(saved.v2Ind1, v2FindLabel(saved.v2Ind1));
  }
  if (saved.v2Ind2) {
    v2IndCurrent.ind2 = saved.v2Ind2;
    v2IndDds.ind2.setValue(saved.v2Ind2, v2FindLabel(saved.v2Ind2));
  }
  if (saved.v2Ind3) {
    v2IndCurrent.ind3 = saved.v2Ind3;
    v2IndDds.ind3.setValue(saved.v2Ind3, v2FindLabel(saved.v2Ind3));
  }

  // 恢复 tab 显示
  if (saved.tab === "view2") {
    document.querySelectorAll(".tab-btn").forEach(t =>
      t.classList.toggle("active", t.dataset.tab === "view2"));
    document.querySelectorAll(".view-panel").forEach(p =>
      p.classList.remove("active"));
    document.getElementById("view2").classList.add("active");
    document.getElementById("view1DateGroup").style.display = "none";
    document.getElementById("view2FilterGroup").style.display = "";
    document.getElementById("view2IndicatorRow").style.display = "";
  }

  // 界面 1 的数据无论当前在哪个 tab 都预加载一次
  await loadView1();

  // 若记忆的是界面 2，再补一次查询
  if (saved.tab === "view2" && saved.v2Code && saved.v2Date) {
    v2Query();
  }
})();