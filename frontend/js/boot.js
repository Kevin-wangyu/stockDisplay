// ─────────────── 初始化 ───────────────

(async () => {
    await loadIndicatorOptions();

    const params = new URLSearchParams(location.search);
    const urlCode   = params.get("code");
    const urlCenter = params.get("center");

    if (urlCode) {
        document.getElementById("code").value = urlCode;
    }

    // center：URL 参数优先，否则今天
    const today = (() => {
        const d = new Date();
        return `${d.getFullYear()}-`
             + `${String(d.getMonth() + 1).padStart(2, "0")}-`
             + `${String(d.getDate()).padStart(2, "0")}`;
    })();
    const center = urlCenter || today;

    // 前 400 交易日 ~ center
    let ok = false;
    if (urlCenter) {
        try {
            const r = await fetch(
                `/api/trading_days_around?date=${urlCenter}&before=200&after=100`);
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

    updateUIByDataSource();
    await load();
})();