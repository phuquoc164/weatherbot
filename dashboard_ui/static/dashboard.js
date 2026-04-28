/* WeatherBet Operations Center — Client-side logic */

(function () {
    "use strict";

    const DATA = window.__DASHBOARD_DATA__;
    let ws = null;
    let reconnectDelay = 1000;

    // Track current data source at module level.
    // Never query #source-select on each poll tick — that races with user input.
    let currentSource = "main";

    // =========================================================================
    // City Position Table
    // =========================================================================
    function updateCityTable(data) {
        const forecasts   = data.forecasts        || [];
        const openPos     = data.open_positions   || [];
        const closedPos   = data.closed_positions || [];
        const locations   = data.locations        || {};
        const calibration = data.calibration      || {};

        // closed_positions arrive sorted descending by closed_at — first hit per city is newest
        const lastClosed = {};
        for (const p of closedPos) {
            if (!lastClosed[p.city]) lastClosed[p.city] = p;
        }

        // minimum sigma across any source with n≥10 for a given city
        function citySigma(cityKey) {
            const prefix = cityKey + "_";
            let min = null;
            for (const [k, v] of Object.entries(calibration)) {
                if (k.startsWith(prefix) && v.n >= 10) {
                    if (min === null || v.sigma < min) min = v.sigma;
                }
            }
            return min;
        }

        let html = "";
        for (const [key, loc] of Object.entries(locations)) {
            const forecast = forecasts.find(f => f.city === key);
            const open     = openPos.find(p => p.city === key);
            const closed   = lastClosed[key];
            const sigma    = citySigma(key);

            const temp     = (forecast && forecast.best != null) ? `${forecast.best}°${forecast.unit}` : "—";
            const sigmaStr = sigma !== null ? sigma.toFixed(1) : "—";

            let statusHtml, bucketHtml, pnlHtml;
            if (open) {
                const pnlCls = (open.pnl ?? 0) >= 0 ? "text-green" : "text-red";
                const sign   = (open.pnl ?? 0) >= 0 ? "+" : "";
                statusHtml = `<span class="city-status-open">OPEN</span>`;
                bucketHtml = `${open.bucket_low}–${open.bucket_high}°${open.unit || ""}`;
                pnlHtml    = `<span class="${pnlCls}">${sign}$${(open.pnl ?? 0).toFixed(2)}</span>`;
            } else if (closed) {
                const pnlCls = (closed.pnl ?? 0) >= 0 ? "text-green" : "text-red";
                const sign   = (closed.pnl ?? 0) >= 0 ? "+" : "";
                statusHtml = `<span class="city-status-closed">CLOSED</span>`;
                bucketHtml = closed.bucket_low != null
                    ? `${closed.bucket_low}–${closed.bucket_high}°${closed.unit || ""}`
                    : "—";
                pnlHtml    = `<span class="${pnlCls}">${sign}$${(closed.pnl ?? 0).toFixed(2)}</span>`;
            } else {
                statusHtml = `<span class="text-muted">—</span>`;
                bucketHtml = "—";
                pnlHtml    = "—";
            }

            html += `<div class="city-table-row">` +
                `<span class="city-table-code">${key.toUpperCase().slice(0, 3)}</span>` +
                `<span class="city-table-name">${loc.name}</span>` +
                `<span class="city-table-temp">${temp}</span>` +
                `<span>${statusHtml}</span>` +
                `<span class="city-table-bucket">${bucketHtml}</span>` +
                `<span class="city-table-pnl">${pnlHtml}</span>` +
                `<span class="city-table-sigma text-muted">${sigmaStr}</span>` +
                `</div>`;
        }

        const body = document.getElementById("city-table-body");
        body.innerHTML = html || `<div class="empty-state">No city data</div>`;
    }

    // =========================================================================
    // Chart.js — Balance History
    // =========================================================================
    const ctx = document.getElementById("balance-chart").getContext("2d");
    const balanceChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [{
                data: [],
                borderColor: "#58a6ff",
                backgroundColor: "rgba(88,166,255,0.1)",
                fill: true,
                tension: 0.3,
                pointRadius: 2,
                pointHoverRadius: 4,
                borderWidth: 1.5,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#21262d",
                    titleColor: "#e1e4e8",
                    bodyColor: "#c9d1d9",
                    borderColor: "#30363d",
                    borderWidth: 1,
                    callbacks: {
                        label: function (ctx) {
                            return "$" + ctx.parsed.y.toFixed(2);
                        },
                    },
                },
            },
            scales: {
                x: {
                    display: true,
                    ticks: { color: "#8b949e", font: { size: 9 }, maxTicksLimit: 6 },
                    grid: { color: "rgba(48,54,61,0.5)", drawBorder: false },
                },
                y: {
                    display: true,
                    ticks: {
                        color: "#8b949e",
                        font: { size: 9 },
                        callback: v => "$" + v,
                    },
                    grid: { color: "rgba(48,54,61,0.5)", drawBorder: false },
                },
            },
        },
    });

    function updateChart(history) {
        if (!history || history.length === 0) {
            balanceChart.data.labels = [];
            balanceChart.data.datasets[0].data = [];
            balanceChart.update("none");
            return;
        }
        balanceChart.data.labels = history.map(h => {
            const d = new Date(h.ts);
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        });
        balanceChart.data.datasets[0].data = history.map(h => h.balance);
        balanceChart.update("none");
    }

    // =========================================================================
    // Update KPIs
    // =========================================================================
    function updateKPIs(kpi) {
        document.getElementById("kpi-starting").textContent = "$" + kpi.starting_balance.toFixed(2);
        document.getElementById("kpi-open-cost").textContent = "$" + kpi.open_cost.toFixed(2);
        document.getElementById("kpi-cash").textContent = "$" + kpi.cash.toFixed(2);

        function setPnl(id, value) {
            const el = document.getElementById(id);
            el.textContent = (value >= 0 ? "+" : "") + "$" + value.toFixed(2);
            el.className = "kpi-value " + (value >= 0 ? "text-green" : "text-red");
        }
        setPnl("kpi-realized", kpi.realized_pnl);
        setPnl("kpi-unrealized", kpi.unrealized_pnl);

        document.getElementById("kpi-open").textContent = kpi.open_count;
        document.getElementById("kpi-winrate").textContent = kpi.win_rate !== null ? kpi.win_rate.toFixed(1) + "%" : "—";

        const ddEl = document.getElementById("kpi-drawdown");
        ddEl.textContent = kpi.drawdown.toFixed(1) + "%";
        ddEl.className = "kpi-value " + (kpi.drawdown < 0 ? "text-red" : "text-muted");
    }

    // =========================================================================
    // Update Positions Table
    // =========================================================================
    function updatePositions(positions) {
        const body = document.getElementById("positions-body");
        const count = document.getElementById("positions-count");
        count.textContent = positions.length + " active";

        if (positions.length === 0) {
            body.innerHTML = '<div class="empty-state">No open positions</div>';
            return;
        }

        let html = "";
        for (const p of positions) {
            const pnl = p.pnl ?? 0;
            const pnlClass = pnl >= 0 ? "text-green" : "text-red";
            const pnlSign = pnl >= 0 ? "+" : "";
            const pnlText = pnlSign + "$" + pnl.toFixed(2);
            const curPrice = p.current_price ? "$" + p.current_price.toFixed(3) : "—";

            const evText   = p.ev   != null ? "+" + p.ev.toFixed(2)   : "—";
            const kellyText = p.kelly != null ? p.kelly.toFixed(2) : "—";
            const entryText = p.entry_price != null ? "$" + p.entry_price.toFixed(3) : "—";
            html += `<div class="table-row">` +
                `<span>${p.city.toUpperCase().slice(0, 3)}</span>` +
                `<span style="font-family:var(--font-mono);font-size:9px;color:var(--text-secondary)">${p.date || "—"}</span>` +
                `<span>${p.bucket_low}-${p.bucket_high}°${p.unit}</span>` +
                `<span>${entryText} → ${curPrice}</span>` +
                `<span class="text-green">${evText}</span>` +
                `<span>${kellyText}</span>` +
                `<span class="${pnlClass}">${pnlText}</span>` +
                `</div>`;
        }
        body.innerHTML = html;
    }

    // =========================================================================
    // Update Forecasts Table
    // =========================================================================
    function updateForecasts(forecasts) {
        const body = document.getElementById("forecast-body");

        if (!forecasts || forecasts.length === 0) {
            body.innerHTML = '<div class="empty-state">Waiting for first scan...</div>';
            return;
        }

        let html = "";
        for (const f of forecasts) {
            html += `<div class="forecast-row">` +
                `<span style="font-weight:600;">${f.city.toUpperCase().slice(0, 3)}</span>` +
                `<span>${f.ecmwf !== null && f.ecmwf !== undefined ? f.ecmwf + "°" : "—"}</span>` +
                `<span>${f.hrrr !== null && f.hrrr !== undefined ? f.hrrr + "°" : "—"}</span>` +
                `<span>${f.metar !== null && f.metar !== undefined ? f.metar + "°" : "—"}</span>` +
                `<span class="best">${f.best}°${f.unit}</span>` +
                `</div>`;
        }
        body.innerHTML = html;
    }

    // =========================================================================
    // Update Calibration
    // =========================================================================
    function updateCalibration(calibration) {
        const container = document.getElementById("calibration-bars");

        if (!calibration) {
            container.innerHTML = '<div class="empty-state" style="height:auto;padding:12px;">Calibration data not yet available — requires resolved markets</div>';
            return;
        }

        let html = "";
        for (const [key, val] of Object.entries(calibration)) {
            if (val.n < 10) continue;
            const pct = Math.min(val.sigma / 4 * 100, 100);
            const color = val.sigma < 1.5 ? "var(--accent-green)" : val.sigma < 2.5 ? "var(--accent-yellow)" : "var(--accent-red)";
            const textClass = val.sigma < 1.5 ? "text-green" : val.sigma < 2.5 ? "text-yellow" : "text-red";

            html += `<div class="calibration-entry">` +
                `<span class="label">${key}</span>` +
                `<div class="calibration-bar-track"><div class="calibration-bar-fill" style="width:${pct}%;background:${color};"></div></div>` +
                `<span class="calibration-value ${textClass}">σ=${val.sigma.toFixed(1)}</span>` +
                `</div>`;
        }
        container.innerHTML = html || '<div class="empty-state" style="height:auto;padding:12px;">No calibration entries with n≥10</div>';
    }

    // =========================================================================
    // Update Trade History
    // =========================================================================
    function updateHistory(trades) {
        const body = document.getElementById("history-body");
        const count = document.getElementById("history-count");
        count.textContent = trades.length + " closed";

        if (trades.length === 0) {
            body.innerHTML = '<div class="empty-state">No closed trades yet</div>';
            return;
        }

        let html = "";
        for (const t of trades) {
            const pnl = t.pnl ?? 0;
            const pnlClass = pnl >= 0 ? "text-green" : "text-red";
            const pnlSign = pnl >= 0 ? "+" : "";
            const reason = t.close_reason || "unknown";

            const entryP = t.entry_price != null ? "$" + t.entry_price.toFixed(3) : "—";
            const exitP  = t.exit_price  != null ? "$" + t.exit_price.toFixed(3)  : "—";
            html += `<div class="history-row">` +
                `<span>${t.city_name}</span>` +
                `<span>${t.date}</span>` +
                `<span>${entryP} → ${exitP}</span>` +
                `<span class="reason-badge reason-${reason}">${reason}</span>` +
                `<span class="${pnlClass}">${pnlSign}$${pnl.toFixed(2)}</span>` +
                `</div>`;
        }
        body.innerHTML = html;
    }

    // =========================================================================
    // Update Activity Feed
    // =========================================================================
    function updateActivity(events) {
        const feed = document.getElementById("activity-feed");
        if (!events || events.length === 0) return;

        let html = "";
        for (const ev of events) {
            const ts = ev.ts ? ev.ts.slice(11, 19) : "";
            html += `<div class="activity-entry ${ev.type}">${ts} ${ev.msg}</div>`;
        }
        feed.innerHTML = html;
        feed.scrollTop = feed.scrollHeight;
    }

    // =========================================================================
    // Update Bot Status
    // =========================================================================
    function updateBotStatus(status) {
        const el = document.getElementById("bot-status");
        const prefix = currentSource === "main" ? "Bot" : currentSource;
        if (status.running) {
            const detail = (currentSource === "main" && status.pid)
                ? ": PID " + status.pid
                : ": running";
            el.textContent = prefix + detail;
        } else {
            el.innerHTML = prefix + ': <span class="badge badge-stopped">STOPPED</span>';
        }
    }

    // =========================================================================
    // Full dashboard update
    // =========================================================================
    function updateDashboard(data) {
        try {
            updateKPIs(data.kpi);
            updateCityTable(data);
            updateChart(data.balance_history);
            updatePositions(data.open_positions || []);
            updateHistory(data.closed_positions || []);
            updateForecasts(data.forecasts || []);
            updateCalibration(data.calibration);
            updateActivity(data.activity || []);
        } catch (e) {
            console.error("Dashboard update error:", e);
        }
        updateBotStatus(data.bot_status || {});
    }

    // =========================================================================
    // Connection status UI
    // =========================================================================
    function setConnectionStatus(status) {
        const dot = document.getElementById("connection-dot");
        const badge = document.getElementById("connection-badge");

        dot.className = "live-dot " + status;
        badge.className = "badge badge-" + status;
        badge.textContent = status.toUpperCase();
    }

    // =========================================================================
    // Source selector
    // =========================================================================
    async function initSourceSelector() {
        let resp;
        try {
            resp = await fetch("/api/variants");
            if (!resp.ok) return;
        } catch {
            return;
        }
        const { main_running, variants } = await resp.json();
        if (variants.length === 0) return; // no variants — normal single-source dashboard

        const sel = document.getElementById("source-select");

        if (main_running) {
            sel.appendChild(new Option("Main thread", "main"));
        }
        variants.forEach(v => {
            const label = v.running ? v.label : `${v.label} (stopped)`;
            sel.appendChild(new Option(label, v.name));
        });
        sel.appendChild(new Option("— Comparison —", "comparison"));

        // Default: main if running, otherwise first running variant
        const firstRunning = main_running
            ? "main"
            : (variants.find(v => v.running)?.name ?? variants[0].name);
        sel.value = firstRunning;
        currentSource = firstRunning;

        sel.style.display = "inline-block";
        sel.addEventListener("change", onSourceChange);

        // If default is not main, switch to it now to load that source's data
        if (firstRunning !== "main") {
            await onSourceChange({ target: sel });
        }
    }

    async function onSourceChange(e) {
        currentSource = e.target.value;
        const bloomberg = document.getElementById("bloomberg-panels");
        const comparison = document.getElementById("comparison-panel");

        if (currentSource === "comparison") {
            bloomberg.style.display = "none";
            comparison.style.display = "block";
            await refreshComparison();
        } else {
            bloomberg.style.display = "";
            comparison.style.display = "none";
            await refreshDashboard();
        }
    }

    async function refreshDashboard() {
        const url = currentSource === "main"
            ? "/api/dashboard"
            : `/api/source/${currentSource}/dashboard`;
        try {
            const resp = await fetch(url);
            if (resp.ok) {
                updateDashboard(await resp.json());
            }
        } catch { /* keep stale data on network error */ }
    }

    async function refreshComparison() {
        try {
            const resp = await fetch("/api/comparison");
            if (resp.ok) {
                renderComparison(await resp.json());
            }
        } catch { /* keep stale */ }
    }

    // =========================================================================
    // Sparkline — inline SVG, per-row normalized Y axis
    // =========================================================================
    function buildSparkline(series, color) {
        if (!series || series.length < 2) return "";
        const min = Math.min(...series);
        const max = Math.max(...series);
        const range = max - min || 1;
        const W = 100, H = 24;
        const step = W / (series.length - 1);
        const pts = series.map((v, i) => {
            const x = (i * step).toFixed(1);
            const y = (H - ((v - min) / range) * (H - 2) - 1).toFixed(1);
            return `${x},${y}`;
        }).join(" ");
        return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">` +
            `<polyline stroke="${color}" stroke-width="1.5" fill="none" ` +
            `vector-effect="non-scaling-stroke" points="${pts}"/></svg>`;
    }

    // =========================================================================
    // Comparison view renderer
    // =========================================================================
    function renderComparison(data) {
        const sources = data.sources || [];
        const panel = document.getElementById("comparison-panel");

        if (sources.length === 0) {
            panel.innerHTML = '<div class="empty-state" style="padding:32px 0;">No variant data yet — run setup and start variants first.</div>';
            return;
        }

        const maxPnl = Math.max(...sources.map(s => s.pnl));

        let html = `<div class="comp-table">`;

        // Header
        html += `<div class="comp-header">` +
            `<span>SOURCE</span>` +
            `<span>FLAGS</span>` +
            `<span class="comp-num">BALANCE</span>` +
            `<span class="comp-num">P&amp;L</span>` +
            `<span class="comp-num">ROI%</span>` +
            `<span class="comp-num">TRADES</span>` +
            `<span class="comp-num">WIN%</span>` +
            `<span class="comp-num">AVG EV</span>` +
            `<span>EQUITY</span>` +
            `</div>`;

        for (const s of sources) {
            const isBest  = s.pnl === maxPnl && maxPnl > 0;
            const noData  = s.trades === 0 && !s.running;
            const pnlCls  = s.pnl > 0 ? "text-green" : s.pnl < 0 ? "text-red" : "text-muted";
            const spark   = buildSparkline(s.series, s.pnl >= 0 ? "#3fb950" : "#f85149");

            const pnlStr  = (s.pnl >= 0 ? "+" : "") + s.pnl.toFixed(2);
            const roiStr  = (s.roi >= 0 ? "+" : "") + s.roi.toFixed(1) + "%";
            const winStr  = s.win_rate !== null ? s.win_rate.toFixed(1) + "%" : "—";
            const evStr   = s.avg_ev  !== null ? s.avg_ev.toFixed(4)   : "—";
            const flagStr = s.flags && s.flags.length
                ? s.flags.map(f => f.replace(/_/g, " ")).join(", ")
                : "—";

            let rowCls = "comp-row";
            if (isBest)  rowCls += " comp-row-best";
            if (noData)  rowCls += " comp-row-dim";
            if (!s.running && !noData) rowCls += " comp-row-stopped";

            html += `<div class="${rowCls}">` +
                `<span class="comp-name">${s.label}` +
                (isBest ? ` <span class="comp-best-marker">←</span>` : "") +
                `</span>` +
                `<span class="comp-flags">${flagStr}</span>` +
                `<span class="comp-num">${noData ? "—" : "$" + s.balance.toFixed(2)}</span>` +
                `<span class="comp-num ${pnlCls}">${noData ? "—" : pnlStr}</span>` +
                `<span class="comp-num ${pnlCls}">${noData ? "—" : roiStr}</span>` +
                `<span class="comp-num">${noData ? "—" : s.trades}</span>` +
                `<span class="comp-num">${noData ? "—" : winStr}</span>` +
                `<span class="comp-num">${noData ? "—" : evStr}</span>` +
                `<span class="comp-spark">${spark || "—"}</span>` +
                `</div>`;
        }

        html += `</div>`;

        const ts = data.generated_at
            ? new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : "";
        html += `<div class="comp-footer">Updated ${ts} · auto-refreshes every 30s</div>`;

        panel.innerHTML = html;
    }

    // =========================================================================
    // WebSocket connection (main thread only)
    // =========================================================================
    let pollInterval = null;

    function connectWebSocket() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(`${protocol}//${location.host}/ws`);

        ws.onopen = function () {
            reconnectDelay = 1000;
            setConnectionStatus("live");
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
        };

        ws.onmessage = function (event) {
            const msg = JSON.parse(event.data);
            // WebSocket only carries main-thread data — skip if viewing a variant
            if (msg.data && currentSource === "main") {
                updateDashboard(msg.data);
            }
        };

        ws.onclose = function () {
            setConnectionStatus("polling");
            startPolling();
            setTimeout(connectWebSocket, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        };

        ws.onerror = function () {
            ws.close();
        };
    }

    // =========================================================================
    // Polling fallback (source-aware)
    // =========================================================================
    function startPolling() {
        if (pollInterval) return;
        pollInterval = setInterval(async function () {
            try {
                if (currentSource === "comparison") {
                    await refreshComparison();
                    return;
                }
                const url = currentSource === "main"
                    ? "/api/dashboard"
                    : `/api/source/${currentSource}/dashboard`;
                const resp = await fetch(url);
                if (resp.ok) {
                    updateDashboard(await resp.json());
                    setConnectionStatus("polling");
                } else {
                    setConnectionStatus("offline");
                }
            } catch {
                setConnectionStatus("offline");
            }
        }, 30000);
    }

    // =========================================================================
    // Init
    // =========================================================================
    updateDashboard(DATA);
    connectWebSocket();
    initSourceSelector();

    setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
        }
    }, 30000);

})();
