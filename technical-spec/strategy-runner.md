# Technical Spec: Parallel Strategy Testing Framework

**Branch:** `feature/strategy-improvements`
**Status:** Draft — awaiting review

---

## 1. Problem Statement

The current strategy has 6 documented potential improvements (`docs/strategy-improvements.md`). Each requires real resolved Polymarket markets to evaluate — a market takes up to 3 days to resolve, and statistical confidence requires at least 30 resolved markets per city. That means **4–6 weeks minimum per improvement**, tested sequentially.

Testing 6 improvements one after another = 6–9 months before picking a winner.

**Goal:** Cut that to 4–6 weeks total by running all variants simultaneously and comparing results at the end.

---

## 2. Scope

### In scope (this spec)

- Three low-effort strategy improvements gated behind config flags (improvements #1, #3, #6 from `docs/strategy-improvements.md`)
- A runner that sets up isolated environments and manages subprocesses
- A CLI comparison script (`strategy_compare.py`) for quick terminal output
- A `/strategy` page in the existing dashboard for the live comparison view
- A `--data-dir` flag on `dashboard.py` for deep-diving into a single variant

### Out of scope

- Improvements #2 (Ensemble Forecasting), #4 (Market Momentum), #5 (Multi-Bucket Hedging) — higher effort, deferred
- Statistical significance testing — results are read manually

---

## 3. Design Principles

### 3.1 Zero refactor of weatherbot.py

The bot must not be restructured. Changes are limited to:
- Reading strategy flags from `config.json` at startup
- Three small conditional branches in existing functions

This ensures tests break immediately if the changes corrupt the main logic.

### 3.2 CWD isolation — no shared state

`weatherbot.py` already reads all paths relative to CWD at import time:
```python
with open("config.json") as f: _cfg = json.load(f)
DATA_DIR = Path("data")
```

Running each variant as a subprocess with `cwd=runs/<variant>/` gives perfect isolation: separate config, separate `data/`, separate calibration. No shared global state, no locking needed, no refactoring.

### 3.3 Flags default to False

The live bot's behavior is unchanged unless a flag is explicitly set in config.json. A missing `"strategy"` key behaves identically to the current code.

---

## 4. Strategy Flags

Three improvements are implemented. Each is independently gated by a boolean in config.json under a `"strategy"` key:

```json
"strategy": {
  "prob_model_normal_cdf": false,
  "time_decay": false,
  "dynamic_min_ev": false,
  "sigma_ref": 2.0
}
```

### 4.1 `prob_model_normal_cdf` — Improvement #1

#### Why this improvement exists

Polymarket temperature markets are divided into buckets like 71–73°F, 73–75°F, 75–77°F, etc. When the bot sees a forecast of 74°F, it needs to estimate the probability that the actual temperature will land in the 73–75°F bucket.

Currently, the bot treats that as **certain**: probability = 1.0. The logic is binary — if the forecast is inside the bucket, p = 1, otherwise p = 0.

The problem: weather forecasts are never exact. A forecast of 74°F doesn't mean the temperature will be exactly 74°F. It could easily be 72°F or 76°F, depending on forecast uncertainty. On a 2°F bucket, that difference decides whether you win or lose.

**Concrete example:**

Forecast = 74.8°F, bucket = 73–75°F (the boundary is at 75°F).

| Behavior | Probability assigned | What the bet looks like |
|---|---|---|
| Current (binary) | p = 1.0 | Certain win — bet confidently |
| Improvement #1 (normal CDF) | p ≈ 0.54 | Coin flip — bet much smaller or skip |

The forecast is 0.2°F away from the bucket edge. The current model calls that a sure thing. Improvement #1 recognizes it is nearly 50/50 given a typical forecast error of 2°F.

#### How it works

We model forecast uncertainty as a normal distribution centered on the forecast, with a standard deviation equal to the calibrated sigma for that city/source. The probability of landing in a bucket `[t_low, t_high]` is the area under that distribution between the two bounds:

```
P = CDF((t_high − forecast) / sigma) − CDF((t_low − forecast) / sigma)
```

For a forecast squarely in the middle of a wide bucket, this gives nearly 1.0 — same as before. For a forecast near a boundary, it gives a realistic probability between 0 and 1.

**Change in `bucket_prob()`:**
```python
if STRAT_PROB_MODEL:
    return norm_cdf((t_high - float(forecast)) / s) - norm_cdf((t_low - float(forecast)) / s)
return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
```

#### Goals and expected benefits

- **Fewer overconfident bets** near bucket boundaries — the bot stops treating 74.9°F on a 73–75°F bucket as a guaranteed win
- **Better EV calibration** — EV numbers will be lower but more honest; the bot will skip marginal trades it currently enters
- **Lower trade count, higher quality** — this variant should trade less than baseline but with a better win rate per trade
- Works best once sigma is calibrated per city (30+ resolved markets)

---

### 4.2 `time_decay` — Improvement #3

#### Why this improvement exists

The bot scans markets that resolve today (D+0), tomorrow (D+1), in 2 days (D+2), and in 3 days (D+3). These four horizons carry very different levels of forecast reliability.

Think of it this way: a weather forecast for today is based on what's already happening. A forecast for Thursday (D+3 on Monday) is a model's best guess about conditions 72 hours from now — it can shift by several degrees before resolution.

Currently, if both a D+0 trade and a D+3 trade have EV = 0.35, the bot bets the same amount on both. That's wrong: the D+3 trade carries much more risk of the forecast moving against the position before the market resolves.

**Concrete example:**

Monday morning. Chicago forecast for Thursday: 72°F → bet on 71–73°F bucket.

By Wednesday evening the forecast has shifted to 68°F → the bot closes early at a loss (forecast-change close). If we had bet less on Monday, the loss would be smaller.

| Horizon | Typical forecast error (MAE) | Current bet | With time decay |
|---|---|---|---|
| D+0 (today) | ~1°F | $2.00 | $2.00 — full confidence |
| D+1 (tomorrow) | ~2°F | $2.00 | $1.60 |
| D+2 (2 days out) | ~3°F | $2.00 | $1.20 |
| D+3 (3 days out) | ~4–5°F | $2.00 | $0.80 — 40% of normal |

**Change in `bet_size()`:**
```python
_HORIZON_MULT = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.4}

def bet_size(kelly, balance, horizon_days=None):
    multiplier = _HORIZON_MULT.get(horizon_days, 1.0) if STRAT_TIME_DECAY and horizon_days is not None else 1.0
    raw = kelly * balance * multiplier
    return round(min(raw, MAX_BET), 2)
```

The existing loop variable `i` (0–3, the day offset in `scan_and_update`) is passed as `horizon_days=i` at the call site.

#### Goals and expected benefits

- **Less capital at risk on uncertain long-horizon bets** — a forecast shift won't cost as much
- **Same number of trade entries** — the bot still enters D+3 markets when EV is good, just smaller
- **Better risk-adjusted returns** — by definition, lower variance on positions that are more likely to need an early exit
- **Natural complement to improvement #1** — when both are active (the `combined` variant), D+3 trades near bucket boundaries get doubly reduced: lower p from normal CDF, smaller size from time decay

---

### 4.3 `dynamic_min_ev` — Improvement #6

#### Why this improvement exists

`MIN_EV = 0.30` means the bot only enters a trade if its expected profit is at least 30% of the amount risked. This threshold is fixed for every city and every season.

The problem is that not all cities are equal. Some cities have very stable, predictable weather — Singapore rarely surprises. Others are highly volatile — Chicago in March can swing 15°F in 24 hours.

When the forecast is unreliable (high sigma), the probability we assign to a bucket could be significantly wrong. The actual outcome might be in a completely different bucket, even if the forecast looked clean at entry. In those cases, a 30% EV minimum is not enough compensation for the extra uncertainty.

Conversely, in a stable city like Singapore where sigma is small, 30% EV is unnecessarily conservative — we're leaving good trades on the table.

**Concrete example with sigma = calibrated forecast error:**

| City | Sigma | Fixed MIN_EV | Dynamic MIN_EV | Effect |
|---|---|---|---|---|
| Singapore | 0.8°C | 0.30 | 0.20 | More entries — stable city, bar is lower |
| London | 1.4°C | 0.30 | 0.28 | Similar to current |
| Chicago (March) | 2.8°F | 0.30 | 0.47 | Fewer entries — volatile city, bar is higher |

The formula scales MIN_EV proportionally to how much the city's sigma exceeds a reference value:

```
effective_min_ev = MIN_EV × max(1.0, sigma / sigma_ref)
```

With `sigma_ref = 2.0` (roughly typical), a city with sigma = 4.0 would need twice the EV to enter.

**Change at EV check in `scan_and_update()`:**
```python
effective_min_ev = MIN_EV * max(1.0, sigma / SIGMA_REF) if STRAT_DYNAMIC_EV else MIN_EV
if ev >= effective_min_ev:
    ...
```

#### Goals and expected benefits

- **Higher win rate in volatile markets** — fewer bad bets in cities where the forecast often misses
- **More opportunities in stable markets** — captures trades that the fixed threshold was rejecting unnecessarily
- **Only meaningful after calibration has data** — sigma values start at the default (2.0°F / 1.2°C) and only become city-specific after 30+ resolved markets. This improvement becomes more powerful over time.
- **Works best combined with improvement #1** — once p is computed honestly via normal CDF, scaling MIN_EV by sigma creates a coherent risk framework: high-uncertainty cities need both a better probability estimate *and* a higher EV bar to enter.

---

## 5. Directory Structure

```
weatherbot/
├── weatherbot.py               ← 3 surgical edits (flags + conditional branches)
├── strategies/
│   ├── runner.py               ← new: setup/start/status/stop subcommands
│   ├── compare.py              ← new: side-by-side results table
│   └── configs/                ← one config per variant
│       ├── baseline.json
│       ├── prob_model.json
│       ├── time_decay.json
│       ├── dynamic_ev.json
│       └── combined.json
├── runs/                       ← gitignored, created by runner
│   ├── baseline/
│   │   ├── weatherbot.py       ← symlink to ../../weatherbot.py
│   │   ├── config.json         ← copied from strategies/configs/baseline.json
│   │   ├── data/               ← isolated data dir
│   │   └── logs/
│   ├── prob_model/
│   ├── time_decay/
│   ├── dynamic_ev/
│   └── combined/
└── docs/
    ├── strategy-improvements.md  ← existing backlog
    └── strategy-runner.md        ← usage documentation
```

---

## 6. Variants

| Variant | `prob_model_normal_cdf` | `time_decay` | `dynamic_min_ev` |
|---|---|---|---|
| `baseline` | false | false | false |
| `prob_model` | **true** | false | false |
| `time_decay` | false | **true** | false |
| `dynamic_ev` | false | false | **true** |
| `combined` | **true** | **true** | **true** |

All variants share the same `balance`, `max_bet`, `kelly_fraction`, and other core settings so capital allocation is comparable.

---

## 7. strategies/runner.py

### Commands

| Command | What it does |
|---|---|
| `setup [variant]` | Creates `runs/<variant>/`, copies config from `strategies/configs/`, symlinks `weatherbot.py` |
| `start [variant]` | Launches subprocess with `cwd=runs/<variant>/`, saves PID, staggered 120s apart |
| `status [variant]` | Reads each variant's `data/state.json`, prints balance/PnL/trade count |
| `stop [variant]` | Sends SIGTERM to the subprocess using stored PID |
| `logs <variant>` | Prints last 50 lines of `runs/<variant>/logs/weatherbot.out` |

### Process management

- PID stored at `runs/<variant>/weatherbot.pid`
- Subprocess launched with `start_new_session=True` so it survives terminal close
- `is_running()` checks via `os.kill(pid, 0)` — no polling
- Stagger of 120 seconds between variant starts (configurable via `--stagger`) to avoid API rate limits

### Key design decision: symlink vs copy for weatherbot.py

`weatherbot.py` is **symlinked**, not copied. Reason: if a bug is fixed in the main bot during the experiment, all variants pick it up automatically on next restart. Only `config.json` is copied (from `strategies/configs/`) because each variant needs different flags.

---

## 8. strategies/compare.py

Reads `runs/*/data/state.json` and `runs/*/data/markets/*.json` and prints a comparison table:

```
Variant          Description                                Balance      PnL    ROI%  Trades  Wins  WinRate    AvgEV
baseline         No improvements (control)                 1023.40   +23.40   +2.3%      18    11    61.1%   0.3421
prob_model       Normal CDF for interior buckets (#1)      1041.20   +41.20   +4.1%      22    14    63.6%   0.2987
...
```

Also supports `--json` flag for machine-readable output.

**Metrics computed:**

| Metric | Source | Formula |
|---|---|---|
| Balance | `state.json` | `state["balance"]` |
| P&L | market files | `sum(pos["pnl"] for closed positions)` |
| ROI% | state.json | `(balance - starting_balance) / starting_balance * 100` |
| Win rate | market files | `wins / total_closed * 100` |
| Avg EV | market files | `mean(pos["ev"] for closed positions)` |

---

## 9. What Is Not Changed

- `scan_and_update()` logic — no structural changes
- `bucket_prob()` edge-bucket behavior (the `t_low == -999` and `t_high == 999` arms) — unchanged
- `calc_ev()`, `calc_kelly()` — unchanged
- Stop-loss, trailing stop, forecast-change close logic — unchanged
- Dashboard, calibration, resolution logic — unchanged

The existing 50 tests still pass without modification.

---

## 10. Risk and Limitations

| Risk | Mitigation |
|---|---|
| API rate limits from 5 concurrent bots + main | 120s stagger between variant starts (configurable via `--stagger`). With 5 variants, the last one starts 8 minutes after the first. Since each bot scans every 3600s and each scan takes roughly the same time, the 120s offset is preserved for the lifetime of the experiment — at most one bot is scanning at any moment. |
| Variants enter the same market at different prices | Unavoidable — each variant fetches live prices at its scan time. The 120s stagger introduces a **systematic** bias: later-started variants consistently see prices that have moved. This is noise at the 6-week timescale but is documented so it is not confused for a real signal difference. |
| One variant crashes and loses its data | Each variant is isolated; a crash in one does not affect others. PID file cleanup handles restarts. |
| D+3 improved variants trade less → harder to compare | `combined` and `dynamic_ev` will have fewer trades; require longer run to reach 30+ resolved per city. |
| `prob_model` reduces p for interior buckets → fewer entries above MIN_EV | Expected and intentional — this variant favors quality over quantity |
| `combined` wins but attribution is unclear | All three flags are on simultaneously. If `combined` outperforms, a follow-up experiment is needed to isolate which flag drives the gain. Document this before claiming a winner. |
| Log disk growth over 6 weeks | Each bot logs to `runs/<variant>/logs/weatherbot.out` with no rotation. At ~1KB/scan × 6 variants × 6 weeks ≈ manageable, but check log size at week 2. Add rotation if needed. |

---

## 11. Success Criteria

After **4–6 weeks** of operation:

- All variants have at least 30 resolved markets
- `strategy_compare.py` produces a complete table
- Results are **directional signal**, not statistical proof — with 30–50 trades per variant, differences of a few percent ROI are within noise
- Positive signals worth acting on: `dynamic_ev` win rate consistently above `baseline`, `prob_model` trade count lower with avg EV higher, `combined` ROI% above all single-flag variants
- Negative signal worth documenting: if `baseline` ties or beats all variants, the improvements add complexity without benefit — revert flags, revisit assumptions in `docs/strategy-improvements.md`

**Do not claim "success" from 4 weeks of data alone.** Use the results to decide which improvement to keep running in production, with 3+ more months of live data as confirmation.

---

## 12. Pre-Merge Checklist

Before merging the `feature/strategy-improvements` branch:

- [ ] Grep `weatherbot.py` for `__file__`, `Path(__file__).resolve()`, `os.path.abspath`, and any hardcoded `data/` paths. CWD isolation breaks silently if any path resolves to the repo root instead of the subprocess CWD.
- [ ] Confirm `config.json` is read at subprocess start time, not cached between runs.
- [ ] Run `python strategies/runner.py setup && python strategies/runner.py status` to confirm dirs are created correctly.
- [ ] Run all 50 existing tests to confirm no regression.

---

## 13. Strategy Monitoring Dashboard

### 13.1 Context

The existing `dashboard.py` is a **FastAPI + Jinja2 + WebSocket** app (588 lines). It serves:
- `/` — Bloomberg dark terminal (single Jinja template, `index.html`)
- `/retro` — static HTML file served directly
- `/ws` — WebSocket pushed by a `watchfiles.awatch` file-watcher on `data/`

The watcher is hardcoded to the live bot's `data/` directory. Dash/Plotly is **not used** — the stack is vanilla FastAPI.

### 13.2 Requirements

1. When **only the main bot is running** (no variants started): the dashboard looks exactly as it does today — no visible change.
2. When **one or more variants are running**: a source selector appears at the top of the Bloomberg dashboard. Options depend on what is actually running:
   - **Main thread** — only shown if the main bot is running (i.e. `data/state.json` exists)
   - One entry per running variant: **baseline**, **prob_model**, etc.
   - **Comparison** — a summary table of all currently running sources (main thread included only if running)
3. When **only variants are running and the main bot is not**: the select appears with variants + Comparison only. "Main thread" is absent. The default selected source is the first running variant.
4. Switching source replaces the dashboard content without a page reload.
5. The **Comparison view** shows a dense summary table — one row per running source. It does not duplicate the full Bloomberg layout per variant; that would be unreadable.
6. Refresh: polling every 60 seconds. No WebSocket extension needed.

### 13.3 Architecture Decision

Everything lives on the single existing `/` page. No new routes, no new templates, no new HTML files.

The select appears and is populated dynamically by a startup JS call to `/api/variants` — a new lightweight endpoint that returns which variant directories exist and are running. If the list is empty, the select is hidden and the page behaves identically to today.

When a source is selected, JS fetches a source-specific API endpoint and re-renders the existing dashboard panels with the new data. When "Comparison" is selected, the standard Bloomberg panels are hidden and the comparison table is shown in their place.

**Why no new template:**
- Less duplication — the Bloomberg layout (KPI strip, positions table, trade history, etc.) is reused for every source, including variants
- One HTML file to maintain, not six
- The select is a progressive enhancement: hidden when unused, visible when needed

### 13.4 New API Endpoints

Three new endpoints are added to `dashboard.py`:

#### `GET /api/variants`

Returns the list of variants that have been set up (i.e. `runs/<name>/config.json` exists), along with whether each is currently running (PID check). Also signals whether the main bot is running. Used by the JS on page load to decide whether to show the select, what options to list, and what the default selection should be.

```python
# Response shape
{
  "main_running": true,          # false if data/state.json does not exist
  "variants": [
    {"name": "baseline",   "label": "baseline",   "running": true},
    {"name": "prob_model", "label": "prob_model",  "running": true},
    {"name": "time_decay", "label": "time_decay",  "running": false},
    ...
  ]
}
```

Rules:
- If `variants` is empty **and** `main_running` is true → no select (normal dashboard)
- If `variants` is empty **and** `main_running` is false → no select, show empty state
- If `variants` is non-empty → show select, include "Main thread" only if `main_running` is true

#### `GET /api/source/{name}/dashboard`

Same response shape as the existing `/api/dashboard`, but reads from `runs/<name>/data/`. Variant name is whitelisted against `STRATEGY_VARIANTS` to prevent path traversal.

```python
@app.get("/api/source/{name}/dashboard")
async def api_variant_dashboard(name: str):
    if name not in STRATEGY_VARIANTS:
        raise HTTPException(status_code=404, detail=f"Unknown variant '{name}'")
    vdir = RUNS_DIR / name
    return build_dashboard_data(data_dir=vdir / "data", is_variant=True)
```

**`build_dashboard_data` refactor — critical detail:**

`build_dashboard_data()` currently uses module-level globals (`STATE_FILE`, `MARKETS_DIR`, `CALIBRATION_FILE`) and mutates shared state (`balance_history`, `activity_feed`). All helper functions (`read_state`, `read_all_markets`, `read_calibration`) also use these globals.

The required signature change:

```python
def build_dashboard_data(
    data_dir: Path = DATA_DIR,
    *,
    is_variant: bool = False,
) -> dict:
    state_file       = data_dir / "state.json"
    markets_dir      = data_dir / "markets"
    calibration_file = data_dir / "calibration.json"
    # pass these explicitly to read_state(), read_all_markets(), read_calibration()
    ...
    if not is_variant:
        # only mutate shared balance_history and activity_feed for the main thread
        balance_history.append(...)
        activity_feed.append(...)
```

When `is_variant=True`:
- Skip mutations to `balance_history` and `activity_feed` (those belong to the main thread only)
- Skip `check_bot_status()` (it scans all `weatherbot.py` processes and cannot distinguish main from variant — return a neutral status dict for variants)
- All path reads use `data_dir`-derived paths, never the module-level constants

If the variant's `data/state.json` does not exist yet (bot set up but no scan completed), return **200 with empty-state defaults** — same values `read_state()` already returns for missing files. Do not return 404, as the frontend tracks running state via `/api/variants` and would need special-case handling for a 404.

#### `GET /api/comparison`

Returns a compact summary of all variants **plus** the main thread for the comparison table view.

```python
# Response shape
{
  "sources": [
    {
      "name":     "main",
      "label":    "Main thread",
      "balance":  1023.40,
      "pnl":      +23.40,
      "roi":       +2.3,
      "trades":   18,
      "wins":     11,
      "win_rate": 61.1,
      "avg_ev":   0.3421,
      "flags":    [],           # main thread: always []
      "series":   [1000, 1005, 1010, ...]  # last 50 equity points; [] if < 2 closed trades
    },
    {
      "name":     "prob_model",
      "label":    "prob_model",
      "balance":  1041.20,
      "pnl":      +41.20,
      ...
      "flags":    ["prob_model_normal_cdf"]
      # flags = keys where value is True in runs/<name>/config.json → "strategy"
    },
    ...
  ],
  "generated_at": "2026-04-25T14:32:00Z"
}
```

**`flags` source:** `runs/<name>/config.json` → `strategy` dict, keys where value is `True`. Main thread reads from `data/../config.json` — has no `strategy` block → `[]`.

**`series` field:** Equity replay of closed positions sorted by `closed_at`, capped at 50 points. Send `[]` if fewer than 2 points — the frontend skips sparkline rendering for empty series.

### 13.5 Frontend Behavior (`index.html` changes)

#### Source selector

Rendered inside the existing top navigation bar. Hidden by CSS (`display: none`) until the JS startup call confirms variants exist.

```html
<select id="source-select" style="display:none">
  <option value="main">Main thread</option>
  <!-- populated dynamically from /api/variants -->
</select>
```

On page load:
```javascript
async function initSourceSelector() {
  const { main_running, variants } = await fetch('/api/variants').then(r => r.json());
  if (variants.length === 0) return;  // no select needed — normal dashboard

  const sel = document.getElementById('source-select');

  // "Main thread" only appears if the main bot is actually running
  if (main_running) {
    sel.appendChild(new Option('Main thread', 'main'));
  }

  variants.forEach(v => {
    const opt = new Option(v.label, v.name);
    if (!v.running) opt.disabled = true;   // set up but not started yet
    sel.appendChild(opt);
  });
  sel.appendChild(new Option('— Comparison —', 'comparison'));

  // Default: main thread if running, otherwise first running variant
  const firstRunning = main_running ? 'main'
    : variants.find(v => v.running)?.name ?? variants[0].name;
  sel.value = firstRunning;

  sel.style.display = 'inline-block';
  sel.addEventListener('change', onSourceChange);
  onSourceChange({ target: sel });  // trigger initial render
}
```

#### Source switching

```javascript
// Module-level variable — tracks current source for the polling loop.
// Do NOT query the DOM on each poll tick (race condition if user changes
// select while a fetch is in flight).
let currentSource = 'main';

async function onSourceChange(e) {
  currentSource = e.target.value;

  if (currentSource === 'comparison') {
    showComparisonView();
    await refreshComparison();
  } else {
    hideComparisonView();
    await refreshDashboard();
  }
}

async function refreshDashboard() {
  const url = currentSource === 'main'
    ? '/api/dashboard'
    : `/api/source/${currentSource}/dashboard`;
  const data = await fetch(url).then(r => r.json());
  renderDashboard(data);   // existing render function, unchanged
}

async function refreshComparison() {
  const data = await fetch('/api/comparison').then(r => r.json());
  renderComparison(data);
}
```

The existing 60-second polling interval is updated to call either `refreshDashboard()` or `refreshComparison()` based on `currentSource` — not always `/api/dashboard`.

#### Comparison view

Two `<div>` sections exist inside the existing layout:
- `#bloomberg-panels` — the existing KPI strip, positions, trade history, etc.
- `#comparison-panel` — new, hidden by default

When "Comparison" is selected:
- `#bloomberg-panels` → `display: none`
- `#comparison-panel` → `display: block`, populated from `/api/comparison`

The comparison panel contains:

**When main bot is running:**
```
SOURCE          FLAGS                     BALANCE      P&L    ROI%  TRADES  WIN%   AVG EV  ▲ EQUITY
────────────────────────────────────────────────────────────────────────────────────────────────────
Main thread     —                        1 023.40   +23.40  +2.3%      18  61.1%  0.3421  ▁▂▃▂▄▃▅
baseline        —                        1 019.20   +19.20  +1.9%      17  58.8%  0.3380  ▁▂▂▃▂▃▄
prob_model      normal_cdf               1 041.20   +41.20  +4.1%      22  63.6%  0.2987  ▁▃▄▅▄▆▇  ←
time_decay      time_decay               1 031.80   +31.80  +3.2%      18  66.7%  0.3421  ▁▂▃▄▄▅▅
dynamic_ev      dynamic_min_ev           1 028.60   +28.60  +2.9%      14  71.4%  0.3812  ▁▂▂▃▄▄▄
combined        all 3                    1 057.40   +57.40  +5.7%      20  70.0%  0.3105  ▁▃▄▅▆▇▇
```

**When only variants are running (no main bot):**
```
SOURCE          FLAGS                     BALANCE      P&L    ROI%  TRADES  WIN%   AVG EV  ▲ EQUITY
────────────────────────────────────────────────────────────────────────────────────────────────────
baseline        —                        1 019.20   +19.20  +1.9%      17  58.8%  0.3380  ▁▂▂▃▂▃▄
prob_model      normal_cdf               1 041.20   +41.20  +4.1%      22  63.6%  0.2987  ▁▃▄▅▄▆▇  ←
time_decay      time_decay               1 031.80   +31.80  +3.2%      18  66.7%  0.3421  ▁▂▃▄▄▅▅
dynamic_ev      dynamic_min_ev           1 028.60   +28.60  +2.9%      14  71.4%  0.3812  ▁▂▂▃▄▄▄
combined        all 3                    1 057.40   +57.40  +5.7%      20  70.0%  0.3105  ▁▃▄▅▆▇▇
```

- `←` marker on the leading row (by P&L)
- P&L in green/red (`#00ff41` / `#ff4136`)
- Variants set up but not started shown in gray with `--` values
- Rows with `series.length < 2` show no sparkline
- Inline SVG sparklines: `<svg viewBox="0 0 100 24" width="100" height="24"><polyline stroke="#00ff41" stroke-width="1" fill="none" vector-effect="non-scaling-stroke" points="..."/></svg>`. Points are **normalized per row** to the `[0, 24]` Y range using that row's own min/max — without per-row normalization, rows with small P&L variance look flat while rows with large swings look identical
- Auto-refreshes every 60s while comparison is active (calls `/api/comparison`, not `/api/dashboard`)

### 13.6 What Is Not Implemented

- No new HTML template files — all changes are inside `index.html` and `dashboard.py`
- No Plotly/Chart.js/Dash — inline SVG sparklines only, no new pip dependencies
- **WebSocket stays main-thread only** — the existing `/ws` + `watchfiles.awatch` pipeline watches `data/` only and is not extended to `runs/*/data/`. Variants are served exclusively by 60s polling via `/api/source/{name}/dashboard` and `/api/comparison`. The WebSocket continues to push live updates for the main thread view and is untouched.
- No auth or multi-user concerns
- No caching — reading 5 small JSON files every 60s is trivial
- No per-variant deep-dive within the comparison — switching the select to that variant's name provides that
- No changes to `/retro` or the existing WebSocket pipeline

### 13.7 Workflow Summary

```bash
# ── Scenario A: main bot + variants running together ──────────────────────────
python weatherbot.py                        # Terminal 1: live bot
python strategies/runner.py start           # Terminal 2: variants (120s stagger)
python dashboard.py                         # Terminal 3: dashboard
# → http://localhost:8050/
#   Select options: Main thread | baseline | prob_model | ... | Comparison
#   Default selection: Main thread

# ── Scenario B: variants only, no main bot ────────────────────────────────────
python strategies/runner.py start           # Terminal 1: variants only
python dashboard.py                         # Terminal 2: dashboard
# → http://localhost:8050/
#   Select options: baseline | prob_model | time_decay | dynamic_ev | combined | Comparison
#   "Main thread" is absent from the select
#   Default selection: first running variant (baseline)

# ── Scenario C: main bot only, no variants (normal daily use) ─────────────────
python weatherbot.py                        # Terminal 1
python dashboard.py                         # Terminal 2
# → http://localhost:8050/
#   No select visible — identical to today's dashboard
```

### 13.8 Affected Files

| File | Change | Lines |
|---|---|---|
| `dashboard.py` | `build_dashboard_data(data_dir)` param, 3 new API routes | ~70 |
| `dashboard_ui/templates/index.html` | Source select element, `#comparison-panel` div, JS init + switch logic | ~80 |

Total: ~150 new lines. Zero new files. Zero changes to `/retro` or the WebSocket.

---

## 14. Open Questions

1. **Should `sigma_ref` default to `2.0` (°F scale) or be unit-aware?** Currently it's a single value. Cities in °C have naturally lower sigma values than °F cities — the dynamic EV scaling might behave differently across unit systems. Could use `sigma_f` / `sigma_c` defaults as reference instead.

2. **~~Should the 60s stagger be configurable?~~** ✅ Resolved — stagger is now 120s by default and configurable via `--stagger SECS`. Example: `python strategy_runner.py start --stagger 180` for more breathing room.

3. **Is 5 variants enough, or should we add an `only_us` variant?** US cities (°F, HRRR available) may behave very differently from EU/Asia cities. A variant that only trades US markets would isolate that signal.
