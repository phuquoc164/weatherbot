# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Tests
source venv/bin/activate
pytest tests/                                          # full suite
pytest tests/test_weatherbot.py::TestBucketProb        # single class
pytest tests/test_weatherbot.py::TestBucketProb::test_value_inside_range  # single test
pytest tests/ --cov=weatherbot --cov-report=term-missing  # with coverage

# Bot (run from project root — config.json and data/ are resolved from CWD)
python weatherbot.py             # start main loop
python weatherbot.py status      # show balance and open positions
python weatherbot.py report      # full resolved-trade breakdown

# Dashboard (separate process, same project root)
python dashboard.py              # serves port 8050; restart needed to pick up new variants
python dashboard.py --port 9000

# Strategy variants
python strategies/runner.py setup              # create runs/<name>/ from strategies/configs/
python strategies/runner.py start [name]       # start one or all variants
python strategies/runner.py status             # show balance/PnL per variant
python strategies/runner.py stop [name]
python strategies/runner.py logs <name>        # tail last 50 lines of variant log
python strategies/compare.py                   # side-by-side P&L table
python strategies/compare.py --json
```

`conftest.py` creates an empty `config.json` if one is missing, so tests run without a real config.

## Architecture

### Two independent processes sharing the filesystem

`weatherbot.py` writes JSON files; `dashboard.py` reads them. There is no shared memory, no IPC, no database. The dashboard's WebSocket push is driven by `watchfiles.awatch()` watching `data/` — any file write triggers a broadcast.

### weatherbot.py — single-file bot

All logic lives here. Config is read at module import time from `config.json` in the current working directory and assigned to module-level constants (`MAX_BET`, `MIN_EV`, `STRAT_PROB_MODEL`, etc.). Running from a directory other than the project root breaks it silently.

**Scan cycle (every 60 min):** `scan_and_update()` is the outer loop — it calls `take_forecast_snapshot()` per city, then for each city-date combination fetches the Polymarket event, updates prices, runs `_check_stops_and_exits()`, and calls `_try_open_position()` if no position is open. After all cities, it runs `_auto_resolve()` and `run_calibration()`.

**Monitor cycle (every 5 min):** `monitor_positions()` hits only the Polymarket Gamma API (no weather APIs) to check trailing stops and take-profits between full scans.

**Exit ordering matters:** `_check_stops_and_exits()` runs stop-loss first, setting `pos["status"] = "closed"`. The forecast-change block re-reads `pos = mkt.get("position")` and guards with `pos.get("status") == "open"` — this prevents double-crediting balance and close_reason overwrites when both conditions fire simultaneously.

**Polymarket price conventions:**
- `outcomePrices` array: `[0]` = YES price, `[1]` = NO price
- `bestBid` / `bestAsk` from the CLOB endpoint are both for YES tokens
- Entry price is set from `bestAsk` (YES ask). If the CLOB API call fails, the trade is aborted (`skip_position = True`) — falling through with `outcomePrices[1]` (the NO price) would open a phantom position with near-zero entry and inflated PnL

**Calibration:** `data/calibration.json` stores MAE-derived sigma per `{city}_{source}` key. `get_sigma()` returns the calibrated value or falls back to `SIGMA_F`/`SIGMA_C`. Calibration only updates once ≥ `CALIBRATION_MIN` resolved markets exist.

### dashboard.py — FastAPI server

`build_dashboard_data()` is the central function that assembles everything the front end needs from raw JSON files. The WebSocket (`/ws`) broadcasts a full payload on every file-change event. The server runs with `reload=False` — a restart is required when new strategy variants are added.

`_discover_variants()` globs `runs/*/config.json` on every request — no in-memory cache.

### Strategy variants

Each variant is an isolated copy of the bot running in its own directory (`runs/<name>/`). `runs/<name>/weatherbot.py` is a symlink to the root `weatherbot.py`, so all variants always run the same bot code. Config comes from `runs/<name>/config.json`, which is populated by `runner.py setup` from `strategies/configs/<name>.json`. Variants write to `runs/<name>/data/` and never touch the main `data/`.

A valid variant config requires `description`, `vc_key`, and `strategy` fields — `_discover_variants()` in `runner.py` skips configs missing any of these.

### Data layout

```
data/state.json                        # balance, trade counts, peak_balance
data/calibration.json                  # sigma per city-source pair
data/markets/{city}_{YYYY-MM-DD}.json  # one file per city-date; contains position,
                                       # forecast_snapshots, market_snapshots, actual_temp
runs/{variant}/                        # isolated variant directories
strategies/configs/{variant}.json      # source of truth for variant config
```

### Tests

Three test files, all using `unittest.TestCase`:
- `test_weatherbot.py` — pure unit tests for math, parsing, calibration, trading logic, and `_check_stops_and_exits`
- `test_dashboard.py` — tests for `build_dashboard_data()` and KPI calculations using fake market dicts
- `test_runner.py` — tests for `_discover_variants()` using `tempfile` directories

Tests never make real API calls and do not require `vc_key` or a running bot.
