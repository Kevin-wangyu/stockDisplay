// ─────────────── 初始化 ───────────────

(async () => {
    await loadIndicatorOptions();

    const params = new URLSearchParams(location.search);
    const urlCode   = params.get("code");
    const urlCenter = params.get("center");
    const urlC7     = params.get("c7");       // 来自策略选股列表
    const urlStreak = parseInt(params.get("streak") || "1", 10);
    const urlPsrc   = params.get("psrc") || "C7";  // 来源：C7 严选 / C6 补充

    if (urlCode) {
        document.getElementById("code").value = urlCode;
    }
    // 规则5.1 + 来源：详情页标注 C7/C6 池、连续在池≥2日
    if (urlC7 === "1") {
        const badge = document.getElementById("c7DetailBadge");
        if (badge) {
            const srcTxt = urlPsrc === "C6" ? "C6补充池" : "C7严选池";
            if (urlStreak >= 2) {
                badge.textContent = `★ ${srcTxt} · 连续在池 ${urlStreak} 日 · 优先关注`;
                badge.title = "规则5.1：该股连续≥2日满足选股条件，优先关注";
            } else {
                badge.textContent = `${srcTxt} · 当日入选`;
                badge.title = `来源策略池：${srcTxt}`;
            }
            badge.style.display = "inline-block";
            badge.className = urlPsrc === "C6" ? "c7-detail-badge src-c6" : "c7-detail-badge src-c7";
        }
    }

    // center：URL 参数优先，否则今天
    const today = (() => {
        const d = new Date();
        return `${d.getFullYear()}-`
             + `${String(d.getMonth() + 1).padStart(2, "0")}-`
             + `${String(d.getDate()).padStart(2, "0")}`;
    })();
    const center = urlCenter || today;

    // 前 300 交易日 ~ center
    let ok = false;
    if (urlCenter) {
        try {
            const r = await fetch(
                `/api/trading_days_around?date=${urlCenter}&before=300&after=300`);
            if (r.ok) {
                const j = await r.json();
                document.getElementById("start").value = j.start;
                document.getElementById("end").value   = j.end;
                document.getElementById("tradeDate").value = urlCenter;
                ok = true;
            }
        } catch (e) {
            console.error("日期范围获取失败", e);
        }
    }
    if (!ok) setDefaultDateRange();
    try {
        const saved = localStorage.getItem("amo_side");
        if (saved && ["both", "buy", "sell"].includes(saved)) {
            setAmoSide(saved);
        }
    } catch (e) {}

    updateUIByDataSource();
    await load();
})();