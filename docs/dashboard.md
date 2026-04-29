# Dashboard — Documentation

A real-time operations center for WeatherBot. Reads the JSON files written by `weatherbot.py` and serves two browser dashboards via a single FastAPI server.

---

## Table of Contents

1. [Overview](#overview)
2. [Usage](#usage)
3. [Architecture](#architecture)
4. [REST API Endpoints](#rest-api-endpoints)
5. [WebSocket](#websocket)
6. [KPI Calculations](#kpi-calculations)
7. [Strategy Comparison View](#strategy-comparison-view)
8. [Activity Feed](#activity-feed)
9. [Bot Status Detection](#bot-status-detection)
10. [File Watcher](#file-watcher)
11. [Python Patterns & Code Notes](#python-patterns--code-notes)
12. [Function Reference](#function-reference)

---

## Overview

`dashboard.py` is a standalone FastAPI server that reads the bot's `data/` files and serves two dashboards. It never writes to `data/`.

```
weatherbot.py  ──writes──▶  data/state.json
                            data/markets/*.json
                            data/calibration.json
                                    │
                                    ▼ file-watcher detects changes
dashboard.py   ──serves──▶  http://localhost:8050/        ← Bloomberg dark UI
                             http://localhost:8050/retro   ← Retro terminal UI
                             http://localhost:8050/simulation.json  ← retro data feed
                             WebSocket /ws  ──push──▶  browser
```

The Bloomberg dashboard receives a full data snapshot on WebSocket connect, then `full_update` pushes whenever any file in `data/` changes. The retro dashboard polls `/simulation.json` every 10 seconds.

---

## Usage

Run from the project root using the venv Python:

```bash
# default: host 0.0.0.0, port 8050
venv/bin/python dashboard.py

# custom port
venv/bin/python dashboard.py --port 9000

# bind to localhost only
venv/bin/python dashboard.py --host 127.0.0.1 --port 8050
```

Then open in your browser:

```
http://localhost:8050
```

**Run in background:**

```bash
nohup venv/bin/python dashboard.py >> dashboard.log 2>&1 &
echo $! > dashboard.pid

# stop it
kill $(cat dashboard.pid)
```

Both dashboards are available as soon as the server starts — no extra processes needed:

| URL | Dashboard |
|---|---|
| `http://localhost:8050/` | Bloomberg dark — KPIs, map, WebSocket push |
| `http://localhost:8050/retro` | Retro terminal — balance chart, positions, EV log |

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--port` | `8050` | Port to listen on |
| `--host` | `0.0.0.0` | Host to bind (use `127.0.0.1` for local-only access) |

### Requirements

The following packages must be installed (all included in `requirements.txt`):

| Package | Role |
|---|---|
| `fastapi` | Web framework and routing |
| `uvicorn` | ASGI server that runs FastAPI |
| `jinja2` | HTML template rendering |
| `watchfiles` | Async file-system watcher |
| `psutil` | Bot process detection |

Install with:

```bash
venv/bin/pip install -r requirements.txt
```

---

## Architecture

```
dashboard.py
├── Data reading layer
│   ├── read_json()             — safe JSON file loader
│   ├── read_state()            — loads state.json with defaults (path arg optional)
│   ├── read_all_markets()      — loads all markets/*.json (path arg optional)
│   └── read_calibration()      — loads calibration.json (path arg optional)
│
├── Aggregation layer
│   ├── build_dashboard_data(data_dir, *, is_variant)
│   │                           — full payload; is_variant=True skips shared state mutation
│   ├── detect_changes()        — diffs market snapshots → activity events
│   └── check_bot_status()      — inspects running processes via psutil
│
├── Strategy variant helpers
│   ├── _variant_pid_running()  — checks runs/<name>/weatherbot.pid
│   └── _equity_series()        — 50-point equity replay for sparklines
│
├── FastAPI app
│   ├── GET  /                          — Bloomberg dark dashboard (Jinja2)
│   ├── GET  /retro                     — Retro terminal dashboard
│   ├── GET  /simulation.json           — Retro dashboard data feed
│   ├── GET  /api/state                 — raw state.json
│   ├── GET  /api/markets               — all market files
│   ├── GET  /api/markets/{city}/{date} — single market file
│   ├── GET  /api/calibration           — calibration.json
│   ├── GET  /api/bot-status            — process status
│   ├── GET  /api/dashboard             — full aggregated payload (main thread)
│   ├── GET  /api/variants              — configured variants + running status
│   ├── GET  /api/source/{name}/dashboard — variant dashboard payload
│   ├── GET  /api/comparison            — compact P&L summary for all sources
│   └── WS   /ws                        — WebSocket push channel
│
└── Background tasks
    └── watch_data_directory()  — async file watcher, pushes updates to WS clients
```

### In-Memory State

Three module-level structures are maintained across requests:

| Variable | Type | Description |
|---|---|---|
| `balance_history` | `list` | Equity curve — `[{ts, balance}, ...]`, appended when equity changes |
| `activity_feed` | `deque(maxlen=100)` | Recent events (buys, exits, forecasts) — bounded FIFO |
| `previous_markets` | `dict` | Last market snapshot used by `detect_changes()` to diff against |
| `connected_clients` | `set` | Active WebSocket connections for broadcasting |

These reset on server restart. `balance_history` and `activity_feed` are not persisted to disk.

---

## REST API Endpoints

All endpoints return JSON. No authentication is required.

### `GET /`

Renders the main dashboard HTML page using the Jinja2 template at `templates/index.html`. Passes the full `build_dashboard_data()` payload as template context for server-side rendering of the initial state.

### `GET /api/state`

Returns `data/state.json` as-is.

```json
{
  "balance": 10234.50,
  "starting_balance": 10000.0,
  "total_trades": 47,
  "wins": 31,
  "losses": 16,
  "peak_balance": 10450.00
}
```

### `GET /api/markets`

Returns all market files as a dict keyed by file stem (e.g. `"nyc_2026-04-23"`).

### `GET /api/markets/{city}/{date}`

Returns a single market file. Returns HTTP 404 if not found.

```
GET /api/markets/nyc/2026-04-24
```

### `GET /api/calibration`

Returns `data/calibration.json` or `{}` if not yet generated.

### `GET /api/bot-status`

Returns the running state of `weatherbot.py` by scanning system processes.

```json
{
  "running": true,
  "pid": 26946,
  "cpu_percent": 0.3,
  "memory_mb": 42.1,
  "uptime_seconds": 3820
}
```

Returns `"running": false` with zeroed metrics if the bot is not running.

### `GET /api/dashboard`

Returns the full aggregated payload used by the Bloomberg UI. This is the same data served on WebSocket connect and on every file-change push.

See [KPI Calculations](#kpi-calculations) for payload structure.

### `GET /api/variants`

Returns the list of configured strategy variants and their running state.

```json
{
  "main_running": true,
  "variants": [
    { "name": "prob_model", "label": "prob_model", "running": true  },
    { "name": "time_decay", "label": "time_decay", "running": false },
    { "name": "dynamic_ev", "label": "dynamic_ev", "running": false }
  ]
}
```

A variant appears in the list only if `runs/<name>/config.json` exists. `main_running` is `true` if `data/state.json` exists.

### `GET /api/source/{name}/dashboard`

Returns the full dashboard payload for a single strategy variant, reading from `runs/<name>/data/` instead of `data/`. The name must be one of the configured `STRATEGY_VARIANTS` (`prob_model`, `time_decay`, `dynamic_ev`); any other value returns HTTP 404.

`balance_history` and `activity` are always `[]` for variant payloads — only the main thread maintains those in-memory structures.

### `GET /api/comparison`

Returns a compact P&L summary for all active sources (main thread + all configured variants).

```json
{
  "sources": [
    {
      "name":     "main",
      "label":    "Main thread",
      "balance":  10234.50,
      "pnl":      234.50,
      "roi":      2.35,
      "trades":   47,
      "wins":     31,
      "win_rate": 66.0,
      "avg_ev":   0.1420,
      "flags":    [],
      "running":  true,
      "series":   [1000.0, 1012.0, ...]
    },
    {
      "name":    "prob_model",
      "flags":   ["prob_model_normal_cdf"],
      "series":  [1000.0, 1018.0, ...],
      ...
    }
  ],
  "generated_at": "2026-04-25T19:00:00+00:00"
}
```

`series` is a list of up to 50 equity values (starting from 1000.0) used to draw sparklines. `flags` lists only the strategy keys set to `true` in the variant's config. Main thread always shows `flags: []`.

### `GET /simulation.json`

Returns the payload consumed by the retro terminal dashboard (`/retro`). Built on-the-fly from the same `data/` files — no file is written to disk.

```json
{
  "balance": 10234.50,
  "starting_balance": 10000.0,
  "wins": 31,
  "losses": 16,
  "total_trades": 47,
  "peak_balance": 10450.00,
  "positions": {
    "nyc_2026-04-24": {
      "question": "Highest temp in New York City on 2026-04-24: 72-75°F",
      "location": "New York City",
      "entry_price": 0.312,
      "current_price": 0.340,
      "cost": 18.00,
      "pnl": 1.68,
      "ev": 0.14,
      "kelly_pct": 0.08
    }
  },
  "trades": [
    { "type": "entry", "location": "New York City", "ev": 0.14, "cost": 18.0, ... },
    { "type": "exit",  "location": "Chicago",       "pnl": 5.20, ... }
  ]
}
```

### `GET /retro`

Serves `sim_dashboard_report.html` — the retro terminal dashboard. The HTML fetches `/simulation.json` from the same server automatically (relative path).

---

## WebSocket

**Endpoint:** `ws://localhost:8050/ws`

### Connection lifecycle

1. Client connects → server sends a `full_update` with the complete dashboard payload.
2. Server sends a `ping` heartbeat every 30 seconds to keep the connection alive.
3. Any change in `data/` triggers a new `full_update` broadcast to all connected clients.
4. On disconnect, the client is removed from `connected_clients`.

### Message format

All messages are JSON strings.

**`full_update`** — sent on connect and on every file change:

```json
{
  "type": "full_update",
  "data": { ... }
}
```

**`ping`** — heartbeat sent every 30 seconds:

```json
{ "type": "ping" }
```

### Polling fallback

The client-side JavaScript (`static/dashboard.js`) falls back to polling `GET /api/dashboard` every 10 seconds if WebSocket is unavailable or disconnects.

---

## KPI Calculations

`build_dashboard_data()` computes all KPIs from raw market files — it does **not** trust `state.json` for financial metrics (only for trade counts). This ensures the dashboard reflects the actual trade history even if the bot's state file drifts.

### Definitions

| KPI | Formula |
|---|---|
| `realized_pnl` | `sum(pnl for all closed positions)` |
| `unrealized_pnl` | `sum((bid_price − entry_price) × shares for all open positions)` |
| `open_cost` | `sum(cost for all open positions)` |
| `cash` | `starting_balance + realized_pnl − open_cost` |
| `equity` | `cash + open_cost + unrealized_pnl` |
| `win_rate` | `wins / total_closed × 100` (None if no closed trades) |
| `drawdown` | `(equity − peak_equity) / peak_equity × 100` |

**Peak equity** is computed by replaying closed trades chronologically to find the historical high-water mark, then comparing against current equity.

**Unrealized PnL** uses the **bid price** (what you'd actually receive when selling), not the mid price. This is the conservative and correct measure.

### Full payload structure

```json
{
  "state": { ... },
  "kpi": {
    "starting_balance": 10000.0,
    "open_cost": 120.0,
    "realized_pnl": 234.50,
    "cash": 10114.50,
    "unrealized_pnl": -5.20,
    "open_count": 6,
    "win_rate": 66.7,
    "drawdown": -0.8
  },
  "open_positions": [ ... ],
  "closed_positions": [ ... ],
  "forecasts": [ ... ],
  "calibration": { ... },
  "bot_status": { ... },
  "balance_history": [ {"ts": "...", "balance": 10234.50}, ... ],
  "activity": [ ... ],
  "locations": { ... }
}
```

### Open position fields

| Field | Description |
|---|---|
| `city`, `city_name`, `date` | Market identity |
| `unit` | `"F"` or `"C"` |
| `bucket_low`, `bucket_high` | Temperature range |
| `entry_price`, `current_price` | Prices (current = live bid) |
| `ev`, `kelly` | Signal values at entry |
| `cost` | Dollars staked |
| `pnl` | Unrealized PnL at current bid |
| `forecast_src`, `sigma` | Forecast metadata |

### Closed position fields

| Field | Description |
|---|---|
| `entry_price`, `exit_price` | Trade prices |
| `pnl` | Realized profit/loss |
| `cost` | Original stake |
| `close_reason` | `stop_loss` / `trailing_stop` / `forecast_changed` / `take_profit` / `resolved` |
| `opened_at` | ISO 8601 timestamp when the position was opened |
| `closed_at` | ISO 8601 timestamp when the position was closed |

Closed positions are sorted by `closed_at` descending (most recent first).

---

## Strategy Comparison View

When strategy variants are configured (via `strategies/runner.py setup`), a **source selector** dropdown appears in the dashboard status bar. It lists the main thread and all variants whose `runs/<name>/config.json` exists; only running sources are selectable.

Selecting **"comparison"** switches the main panel to a Bloomberg-style table populated from `/api/comparison`:

| Column | Description |
|---|---|
| Source | Variant name and active strategy flags |
| Balance | Current equity |
| P&L | Total realized P&L |
| ROI | Return on starting balance (%) |
| Trades | Number of closed positions |
| Win % | Win rate of closed positions |
| Avg EV | Mean expected value at entry |
| Equity | 50-point sparkline of equity replay |

The row with the highest P&L gets a green left-border highlight and a `←` marker.

Selecting any individual variant (e.g. `"prob_model"`) switches the entire Bloomberg panel to that variant's data, sourced from `/api/source/{name}/dashboard`. The WebSocket feed is suppressed while viewing a variant — it only pushes data for the main thread.

The source selector is hidden when no variants exist. It is populated by `/api/variants` on page load and on each 60-second poll tick.

---

## Activity Feed

`detect_changes(old_markets, new_markets)` diffs two market snapshots and generates events for the activity feed. Three event types are detected:

| Event type | Trigger | Example message |
|---|---|---|
| `"scan"` | A market file appears for the first time | `SCAN New market: New York City 2026-04-24` |
| `"buy"` | A position is opened (old has none, new has one) | `BUY New York City $18 @ 0.312 bucket 72-75F (EV +0.14)` |
| `"stop"` / `"resolved"` | A position closes (status changes from open → closed) | `EXIT Chicago stop_loss @ 0.210 (-$3.60)` |
| `"monitor"` | A new forecast snapshot is appended | `FORECAST Tokyo ECMWF 28.4°` |

The feed is a `deque(maxlen=100)` — the 100 most recent events are kept in memory. New events are prepended (`appendleft`) so the feed is newest-first. The feed resets on dashboard server restart.

---

## Bot Status Detection

`check_bot_status()` uses `psutil.process_iter()` to scan all running processes and look for one whose command-line arguments contain `"weatherbot.py"`. It returns:

- **`running: true`** with PID, CPU %, memory (MB), and uptime in seconds
- **`running: false`** with zeroed metrics if not found

This works whether the bot was started with `python weatherbot.py`, `nohup venv/bin/python weatherbot.py`, or any other invocation — as long as `weatherbot.py` appears in the process arguments.

---

## File Watcher

`watch_data_directory()` is an async background task started at FastAPI app startup via:

```python
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(watch_data_directory())
```

It uses `watchfiles.awatch()` to monitor the entire `data/` directory for any file changes (market files, state.json, calibration.json). On each change:

1. Re-reads all market files.
2. Runs `detect_changes()` to generate activity events.
3. Prepends new events to `activity_feed`.
4. If any WebSocket clients are connected, calls `broadcast()` with a fresh `full_update`.

**Fallback behavior:** If `watchfiles` is not installed, the watcher falls back to a polling loop that checks every 10 seconds, then exits. This means the dashboard still works without `watchfiles`, but updates arrive less promptly.

---

## Python Patterns & Code Notes

### Async patterns (FastAPI + asyncio)

`dashboard.py` is fully async. Key patterns used:

**Background task with `asyncio.create_task`**

```python
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(watch_data_directory())
```

The file watcher runs as a concurrent coroutine alongside request handling — no threads needed.

**WebSocket heartbeat with `asyncio.wait_for`**

```python
try:
    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
except asyncio.TimeoutError:
    await websocket.send_text(json.dumps({"type": "ping"}))
```

`wait_for` with a timeout turns a blocking receive into a non-blocking check, allowing the server to send periodic pings without a separate task.

**Dead-client cleanup in broadcast**

```python
async def broadcast(payload: dict):
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)
```

Collects failed sends into a `dead` set and removes them after the loop — avoids mutating a set while iterating it.

### `Optional[dict]` return type

`read_json()` returns `None` on missing or corrupt files, and callers check for `None` before use. This is the correct EAFP pattern for optional file reads.

### `deque(maxlen=100)` for bounded feed

```python
activity_feed: deque = deque(maxlen=100)
```

`collections.deque` with a max length automatically discards the oldest entry when a new one is appended — no manual slicing needed. Correct and efficient for a fixed-size activity log.

### Synchronous file I/O in async context

`read_json()`, `read_state()`, and `read_all_markets()` are regular synchronous functions called from async route handlers. For this workload (small local JSON files read infrequently) this is acceptable. For high-throughput production use, these would be wrapped with `asyncio.to_thread()` to avoid blocking the event loop:

```python
state = await asyncio.to_thread(read_state)
```

### Type hints

`dashboard.py` uses partial type annotations (`Optional[dict]`, `list[dict]`). The `build_dashboard_data()` function returns an untyped `dict` — adding a `TypedDict` or Pydantic model would make the payload contract explicit and enable validation.

---

## Function Reference

### Data Reading

| Function | Signature | Returns |
|---|---|---|
| `read_json` | `(path: Path) -> Optional[dict]` | Parsed JSON or `None` |
| `read_state` | `(state_file: Path = STATE_FILE) -> dict` | State with safe defaults |
| `read_all_markets` | `(markets_dir: Path = MARKETS_DIR) -> dict` | All market files keyed by stem |
| `read_calibration` | `(calibration_file: Path = CALIBRATION_FILE) -> Optional[dict]` | Calibration data or `None` |
| `check_bot_status` | `() -> dict` | Process status from psutil |

### Aggregation

| Function | Signature | Description |
|---|---|---|
| `detect_changes` | `(old, new)` | Diffs two market dicts, returns list of activity events |
| `build_dashboard_data` | `(data_dir=DATA_DIR, *, is_variant=False)` | Assembles dashboard payload; `is_variant=True` skips `balance_history` / `activity` mutation |

### Strategy Variant Helpers

| Function | Signature | Description |
|---|---|---|
| `_variant_pid_running` | `(name: str) -> bool` | Checks if variant process is alive via `runs/<name>/weatherbot.pid` |
| `_equity_series` | `(markets_dir: Path) -> list[float]` | Replays closed-position PnL from 1000.0, returns last 50 equity points |

### FastAPI Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Bloomberg dark dashboard (`templates/index.html`) |
| `GET` | `/retro` | Retro terminal dashboard (`sim_dashboard_report.html`) |
| `GET` | `/simulation.json` | Retro dashboard data feed (built on-the-fly) |
| `GET` | `/api/state` | Raw `state.json` |
| `GET` | `/api/markets` | All market files |
| `GET` | `/api/markets/{city}/{date}` | Single market file (404 if missing) |
| `GET` | `/api/calibration` | `calibration.json` or `{}` |
| `GET` | `/api/bot-status` | Bot process status |
| `GET` | `/api/dashboard` | Full aggregated payload (main thread) |
| `GET` | `/api/variants` | Configured variants + running status |
| `GET` | `/api/source/{name}/dashboard` | Variant dashboard payload (404 for unknown name) |
| `GET` | `/api/comparison` | Compact P&L summary — all sources with sparkline series |
| `WS` | `/ws` | WebSocket push channel |

### WebSocket & Broadcasting

| Function | Description |
|---|---|
| `broadcast(payload)` | Sends JSON to all connected clients, removes dead connections |
| `websocket_endpoint(websocket)` | Handles connect, initial push, heartbeat, disconnect |

### Background Tasks

| Function | Description |
|---|---|
| `watch_data_directory()` | Async file watcher — detects changes, updates feed, broadcasts |
| `startup_event()` | FastAPI startup hook — launches the file watcher task |
