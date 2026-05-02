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
    // City Stats Table
    // =========================================================================
    let citySort    = { col: "pnl", dir: "desc" };
    let lastCityData = null;

    const SORT_LABELS = { trades: "Trades", winpct: "Win%", pnl: "P&L" };

    function renderCityTable() {
        if (!lastCityData) return;
        const { closedPos, locations } = lastCityData;

        // Aggregate per city — wins = any trade with P&L > 0
        const stats = {};
        for (const p of closedPos) {
            if (!stats[p.city]) stats[p.city] = { trades: 0, sl: 0, tp: 0, ts: 0, fc: 0, wins: 0, pnl: 0 };
            const s = stats[p.city];
            s.trades++;
            s.pnl += p.pnl ?? 0;
            if (p.close_reason === "stop_loss")                                    s.sl++;
            else if (p.close_reason === "take_profit" || p.close_reason === "resolved") s.tp++;
            else if (p.close_reason === "trailing_stop")                           s.ts++;
            else if (p.close_reason === "forecast_changed")                        s.fc++;
            if ((p.pnl ?? 0) > 0) s.wins++;
        }

        // Sort entries — cities with no trades always last
        const entries = Object.entries(locations).map(([key, loc]) => {
            const s      = stats[key] || { trades: 0, sl: 0, tp: 0, wins: 0, pnl: 0 };
            const winPct = s.trades > 0 ? (s.wins / s.trades) * 100 : null;
            return { key, loc, s, winPct };
        });
        entries.sort((a, b) => {
            if (a.s.trades === 0 && b.s.trades === 0) return 0;
            if (a.s.trades === 0) return 1;
            if (b.s.trades === 0) return -1;
            let cmp = 0;
            if (citySort.col === "trades") cmp = a.s.trades - b.s.trades;
            else if (citySort.col === "winpct") cmp = (a.winPct ?? 0) - (b.winPct ?? 0);
            else cmp = a.s.pnl - b.s.pnl;
            return citySort.dir === "desc" ? -cmp : cmp;
        });

        // Update sort indicators in header
        document.querySelectorAll(".city-sort-col").forEach(th => {
            const col   = th.dataset.sort;
            const arrow = citySort.col === col ? (citySort.dir === "desc" ? " ▼" : " ▲") : "";
            th.textContent = SORT_LABELS[col] + arrow;
        });

        let html = "";
        for (const { key, loc, s, winPct } of entries) {
            const pnlCls  = s.pnl >= 0 ? "text-green" : "text-red";
            const pnlSign = s.pnl >= 0 ? "+" : "";
            const winStr  = winPct !== null ? winPct.toFixed(0) + "%" : "—";
            const pnlStr  = s.trades > 0
                ? `<span class="${pnlCls}">${pnlSign}$${s.pnl.toFixed(2)}</span>`
                : `<span class="text-muted">—</span>`;

            html += `<tr>` +
                `<td class="col-code">${key.toUpperCase().slice(0, 3)}</td>` +
                `<td><div class="city-name">${loc.name}</div></td>` +
                `<td class="col-num">${s.trades || "—"}</td>` +
                `<td class="col-num text-red">${s.sl || "—"}</td>` +
                `<td class="col-num text-green">${s.tp || "—"}</td>` +
                `<td class="col-num text-yellow">${s.ts || "—"}</td>` +
                `<td class="col-num text-blue">${s.fc || "—"}</td>` +
                `<td class="col-num">${winStr}</td>` +
                `<td class="col-num">${pnlStr}</td>` +
                `</tr>`;
        }

        document.getElementById("city-table-body").innerHTML =
            html || `<tr><td colspan="9" class="empty-cell">No city data</td></tr>`;
    }

    function updateCityTable(data) {
        lastCityData = {
            closedPos: data.closed_positions || [],
            locations: data.locations        || {},
        };
        renderCityTable();
    }

    document.querySelectorAll(".city-sort-col").forEach(th => {
        th.addEventListener("click", () => {
            const col = th.dataset.sort;
            if (citySort.col === col) {
                citySort.dir = citySort.dir === "desc" ? "asc" : "desc";
            } else {
                citySort.col = col;
                citySort.dir = "desc";
            }
            renderCityTable();
        });
    });

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

    let allBalanceHistory = [];
    let activePeriod = "1W";

    const PERIOD_MS = { "1D": 86400000, "1W": 604800000, "1M": 2592000000 };

    function filterHistory(history, period) {
        if (period === "ALL" || !PERIOD_MS[period]) return history;
        const cutoff = Date.now() - PERIOD_MS[period];
        return history.filter(h => new Date(h.ts).getTime() >= cutoff);
    }

    function labelForPeriod(ts, period) {
        const d = new Date(ts);
        if (period === "1D") {
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        if (period === "1W") {
            return d.toLocaleDateString([], { month: "numeric", day: "numeric" })
                + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return d.toLocaleDateString([], { month: "numeric", day: "numeric" });
    }

    function renderChart(period) {
        const slice = filterHistory(allBalanceHistory, period);
        if (!slice || slice.length === 0) {
            balanceChart.data.labels = [];
            balanceChart.data.datasets[0].data = [];
            balanceChart.update("none");
            return;
        }
        balanceChart.data.labels = slice.map(h => labelForPeriod(h.ts, period));
        balanceChart.data.datasets[0].data = slice.map(h => h.balance);
        balanceChart.update("none");
    }

    function updateChart(history) {
        allBalanceHistory = history || [];
        renderChart(activePeriod);
    }

    document.querySelectorAll(".period-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            activePeriod = btn.dataset.period;
            renderChart(activePeriod);
        });
    });

    // =========================================================================
    // Update KPIs
    // =========================================================================
    function updateKPIs(kpi) {
        document.getElementById("kpi-equity").textContent = "$" + kpi.equity.toFixed(2);

        function setPnl(id, value) {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = (value >= 0 ? "+" : "") + "$" + value.toFixed(2);
            el.className = "kpi-value " + (value >= 0 ? "text-green" : "text-red");
        }

        const totalEl = document.getElementById("kpi-total-pnl");
        if (totalEl) {
            const sign = kpi.total_pnl >= 0 ? "+" : "";
            totalEl.className = "kpi-value " + (kpi.total_pnl >= 0 ? "text-green" : "text-red");
            totalEl.childNodes[0].textContent = sign + "$" + kpi.total_pnl.toFixed(2) + " ";
        }
        const pctEl = document.getElementById("kpi-total-pnl-pct");
        if (pctEl) {
            const sign = kpi.total_pnl_pct >= 0 ? "+" : "";
            pctEl.textContent = "(" + sign + kpi.total_pnl_pct.toFixed(1) + "%)";
        }

        setPnl("kpi-realized", kpi.realized_pnl);
        setPnl("kpi-unrealized", kpi.unrealized_pnl);

        document.getElementById("kpi-winrate").textContent = kpi.win_rate !== null ? kpi.win_rate.toFixed(1) + "%" : "—";

        const ddEl = document.getElementById("kpi-drawdown");
        ddEl.textContent = kpi.max_drawdown.toFixed(1) + "%";
        ddEl.className = "kpi-value " + (kpi.max_drawdown > 0 ? "text-red" : "text-muted");
    }

    // =========================================================================
    // Update Positions Table
    // =========================================================================
    function updatePositions(positions) {
        const body = document.getElementById("positions-body");
        const count = document.getElementById("positions-count");
        count.textContent = positions.length + " active";

        if (positions.length === 0) {
            body.innerHTML = '<tr><td colspan="7" class="empty-cell">No open positions</td></tr>';
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
            html += `<tr>` +
                `<td class="col-code">${p.city.toUpperCase().slice(0, 3)}</td>` +
                `<td class="col-mono">${p.date || "—"}</td>` +
                `<td>${p.bucket_low}-${p.bucket_high}°${p.unit}</td>` +
                `<td class="col-mono">${entryText} → ${curPrice}</td>` +
                `<td class="text-green">${evText}</td>` +
                `<td class="col-mono text-muted">${kellyText}</td>` +
                `<td class="${pnlClass}">${pnlText}</td>` +
                `</tr>`;
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
    // Trade History — filters
    // =========================================================================
    let allHistory = [];
    let historySortDir    = null; // null = closed_at desc, "desc" = P&L high→low, "asc" = P&L low→high
    let historyRoiSortDir = null; // null = no sort, "desc" = ROI high→low, "asc" = ROI low→high

    function fmtTs(ts) {
        if (!ts) return "—";
        return ts.slice(0, 16).replace("T", " ");
    }

    function fmtDuration(openedAt, closedAt) {
        if (!openedAt || !closedAt) return "—";
        const ms = new Date(closedAt) - new Date(openedAt);
        if (ms <= 0) return "—";
        const totalMins = Math.floor(ms / 60000);
        const days  = Math.floor(totalMins / 1440);
        const hours = Math.floor((totalMins % 1440) / 60);
        const mins  = totalMins % 60;
        if (days > 0)  return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    }

    function renderHistory() {
        const cityVal   = document.getElementById("history-filter-city").value;
        const reasonVal = document.getElementById("history-filter-reason").value;

        let trades = allHistory.slice();
        if (cityVal)   trades = trades.filter(t => t.city_name    === cityVal);
        if (reasonVal) trades = trades.filter(t => t.close_reason === reasonVal);

        if (historyRoiSortDir !== null) {
            trades.sort((a, b) => {
                const roiA = a.cost > 0 ? (a.pnl ?? 0) / a.cost : 0;
                const roiB = b.cost > 0 ? (b.pnl ?? 0) / b.cost : 0;
                const cmp = roiA - roiB;
                return historyRoiSortDir === "desc" ? -cmp : cmp;
            });
        } else if (historySortDir !== null) {
            trades.sort((a, b) => {
                const cmp = (a.pnl ?? 0) - (b.pnl ?? 0);
                return historySortDir === "desc" ? -cmp : cmp;
            });
        }

        const histSortTh = document.querySelector(".history-sort-col");
        if (histSortTh) {
            histSortTh.textContent = historySortDir === "desc" ? "P&L ▼"
                                   : historySortDir === "asc"  ? "P&L ▲"
                                   : "P&L";
        }
        const histRoiSortTh = document.querySelector(".history-roi-sort-col");
        if (histRoiSortTh) {
            histRoiSortTh.textContent = historyRoiSortDir === "desc" ? "ROI ▼"
                                      : historyRoiSortDir === "asc"  ? "ROI ▲"
                                      : "ROI";
        }

        const body      = document.getElementById("history-body");
        const count     = document.getElementById("history-count");
        const total     = allHistory.length;
        const filtered  = trades.length;
        const pnl       = trades.reduce((s, t) => s + (t.pnl ?? 0), 0);
        const pnlStr    = `<span class="${pnl >= 0 ? "text-green" : "text-red"}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</span>`;
        if ((cityVal || reasonVal) && filtered < total) {
            const pct = total > 0 ? (filtered / total * 100).toFixed(1) : "0.0";
            count.innerHTML = `${filtered}/${total} closed (${pct}%) · ${pnlStr}`;
        } else {
            count.innerHTML = `${total} closed · ${pnlStr}`;
        }

        if (trades.length === 0) {
            body.innerHTML = '<tr><td colspan="9" class="empty-cell">No closed trades yet</td></tr>';
            return;
        }

        let html = "";
        for (const t of trades) {
            const pnl      = t.pnl ?? 0;
            const pnlClass = pnl >= 0 ? "text-green" : "text-red";
            const pnlSign  = pnl >= 0 ? "+" : "";
            const roi      = t.cost > 0 ? (pnl / t.cost * 100) : 0;
            const roiSign  = roi >= 0 ? "+" : "";
            const reason    = t.close_reason || "unknown";
            const bucket    = t.bucket_low != null ? `${t.bucket_low}-${t.bucket_high}°${t.unit || "F"}` : "—";
            const entryText = t.entry_price != null ? "$" + t.entry_price.toFixed(3) : "—";
            const exitText  = t.exit_price  != null ? "$" + t.exit_price.toFixed(3)  : "—";
            html += `<tr>` +
                `<td>${t.city_name}</td>` +
                `<td class="col-mono">${t.date || "—"}</td>` +
                `<td class="col-mono">${bucket}</td>` +
                `<td class="col-mono">${fmtTs(t.opened_at)}</td>` +
                `<td class="col-mono">${fmtDuration(t.opened_at, t.closed_at)}</td>` +
                `<td class="col-mono">${entryText} → ${exitText}</td>` +
                `<td><span class="reason-badge reason-${reason}">${reason}</span></td>` +
                `<td class="${pnlClass}">${pnlSign}$${pnl.toFixed(2)}</td>` +
                `<td class="${pnlClass}">${roiSign}${roi.toFixed(1)}%</td>` +
                `</tr>`;
        }
        body.innerHTML = html;
    }

    function updateHistory(trades) {
        allHistory = trades || [];

        const cityEl   = document.getElementById("history-filter-city");
        const prevCity = cityEl.value;
        const cities   = [...new Set(allHistory.map(t => t.city_name))].sort();
        cityEl.innerHTML = '<option value="">All cities</option>' +
            cities.map(c => `<option value="${c}"${c === prevCity ? " selected" : ""}>${c}</option>`).join("");

        const reasonEl   = document.getElementById("history-filter-reason");
        const prevReason = reasonEl.value;
        const reasons    = [...new Set(allHistory.map(t => t.close_reason).filter(Boolean))].sort();
        reasonEl.innerHTML = '<option value="">All reasons</option>' +
            reasons.map(r => `<option value="${r}"${r === prevReason ? " selected" : ""}>${r}</option>`).join("");

        renderHistory();
    }

    document.getElementById("history-filter-city").addEventListener("change", renderHistory);
    document.getElementById("history-filter-reason").addEventListener("change", renderHistory);
    document.querySelector(".history-sort-col").addEventListener("click", () => {
        historySortDir = historySortDir === null ? "desc" : historySortDir === "desc" ? "asc" : null;
        historyRoiSortDir = null;
        renderHistory();
    });
    document.querySelector(".history-roi-sort-col").addEventListener("click", () => {
        historyRoiSortDir = historyRoiSortDir === null ? "desc" : historyRoiSortDir === "desc" ? "asc" : null;
        historySortDir = null;
        renderHistory();
    });

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
        feed.scrollTop = 0;
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
