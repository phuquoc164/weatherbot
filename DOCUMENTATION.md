# WeatherBot — Documentation

A paper-trading bot that monitors daily high-temperature markets on [Polymarket](https://polymarket.com), fetches temperature forecasts from meteorological sources, and manages positions using the Kelly criterion.

---

## Table of Contents

1. [Overview](#overview)
2. [Usage](#usage)
3. [Simulation Dashboard](#simulation-dashboard)
4. [Configuration](#configuration)
5. [Supported Locations](#supported-locations)
6. [Data Sources](#data-sources)
7. [Trading Logic](#trading-logic)
8. [Position Management & Exits](#position-management--exits)
9. [Data Persistence](#data-persistence)
10. [Calibration System](#calibration-system)
11. [Timing & Loop Structure](#timing--loop-structure)
12. [Function Reference](#function-reference)

---

## Overview

WeatherBot scans Polymarket for "highest temperature on date X in city Y" prediction markets, compares current weather forecasts against the market's implied probabilities, and paper-trades YES tokens in the bucket it expects to win. It is a fully autonomous paper-trading system — no real money is involved.

**Core pipeline:**

```
Weather APIs (ECMWF, HRRR, METAR)
         ↓
   Forecast temperature
         ↓
   Probability estimate (bucket_prob)
         ↓
   Expected value check (calc_ev ≥ MIN_EV)
         ↓
   Kelly bet sizing (calc_kelly × KELLY_FRACTION)
         ↓
   Real-time price validation (bestAsk from Polymarket API)
         ↓
   Position opened / managed / closed
```

---

## Usage

Run from the directory containing `config.json`:

```bash
python weatherbot.py          # start main loop (default)
python weatherbot.py run      # same as above
python weatherbot.py status   # print balance and open positions
python weatherbot.py report   # full resolved-trade report
```

---

## Simulation Dashboard

`sim_dashboard_report.html` is a browser-based dashboard that visualises simulation results. It reads data from a `simulation.json` file in the same directory and auto-refreshes every 10 seconds.

### Why a local server is required

The dashboard loads `simulation.json` via the browser's `fetch()` API. Browsers block file-system fetches (`file://` URLs) for security reasons, so opening the HTML file directly will show a warning and no data. A local HTTP server sidesteps this restriction.

### How to run

**1. Start a local HTTP server** from the `weatherbot/` directory:

```bash
python -m http.server 8000
```

**2. Open the dashboard** in your browser:

```
http://localhost:8000/sim_dashboard_report.html
```

The dashboard will auto-refresh every 10 seconds, picking up any changes to `simulation.json` automatically.

### What you need

| File | Required | Description |
|---|---|---|
| `sim_dashboard_report.html` | Yes | The dashboard itself |
| `simulation.json` | Yes | Simulation output data read by the dashboard |

The dashboard also loads Chart.js and Google Fonts from CDNs — an internet connection is required for those assets to render correctly.

### Stopping the server

Press `Ctrl+C` in the terminal running `python -m http.server` to stop it.

---

## Configuration

All parameters are loaded from `config.json` in the working directory. Missing keys fall back to the defaults listed below.

| Key | Default | Description |
|---|---|---|
| `balance` | `10000.0` | Starting paper balance in dollars |
| `max_bet` | `20.0` | Hard cap per trade in dollars |
| `min_ev` | `0.10` | Minimum expected value required to enter a trade |
| `max_price` | `0.45` | Maximum ask price to enter (avoids already-priced-in bets) |
| `min_volume` | `500` | Minimum cumulative market volume required |
| `min_hours` | `2.0` | Won't open a position with fewer than this many hours to resolution |
| `max_hours` | `72.0` | Won't discover a market with more than this many hours remaining |
| `kelly_fraction` | `0.25` | Quarter-Kelly multiplier applied to raw Kelly fraction |
| `max_slippage` | `0.03` | Maximum allowed bid-ask spread at entry |
| `scan_interval` | `3600` | Seconds between full scans (default: 1 hour) |
| `calibration_min` | `30` | Minimum resolved markets before calibration updates sigma |
| `vc_key` | `""` | Visual Crossing API key for historical actual-temperature lookup |

Two sigma constants are hardcoded (not configurable):

| Constant | Value | Role |
|---|---|---|
| `SIGMA_F` | `2.0` | Default forecast uncertainty for Fahrenheit markets |
| `SIGMA_C` | `1.2` | Default forecast uncertainty for Celsius markets |

These are overridden per city-source pair once calibration has enough data.

**Minimal `config.json` example:**

```json
{
  "balance": 10000.0,
  "max_bet": 20.0,
  "min_ev": 0.10,
  "max_price": 0.45,
  "min_volume": 500,
  "vc_key": "YOUR_KEY_HERE"
}
```

---

## Supported Locations

20 cities across 6 regions. HRRR forecasts are only available for US cities.

| Slug | City | Region | Unit | METAR Station |
|---|---|---|---|---|
| `nyc` | New York City | us | °F | KLGA |
| `chicago` | Chicago | us | °F | KORD |
| `miami` | Miami | us | °F | KMIA |
| `dallas` | Dallas | us | °F | KDAL |
| `seattle` | Seattle | us | °F | KSEA |
| `atlanta` | Atlanta | us | °F | KATL |
| `london` | London | eu | °C | EGLC |
| `paris` | Paris | eu | °C | LFPG |
| `munich` | Munich | eu | °C | EDDM |
| `ankara` | Ankara | eu | °C | LTAC |
| `seoul` | Seoul | asia | °C | RKSI |
| `tokyo` | Tokyo | asia | °C | RJTT |
| `shanghai` | Shanghai | asia | °C | ZSPD |
| `singapore` | Singapore | asia | °C | WSSS |
| `lucknow` | Lucknow | asia | °C | VILK |
| `tel-aviv` | Tel Aviv | asia | °C | LLBG |
| `toronto` | Toronto | ca | °C | CYYZ |
| `sao-paulo` | Sao Paulo | sa | °C | SBGR |
| `buenos-aires` | Buenos Aires | sa | °C | SAEZ |
| `wellington` | Wellington | oc | °C | NZWN |

---

## Data Sources

### ECMWF (via Open-Meteo)

- **Coverage:** All 20 cities
- **Horizon:** Up to 7 days (D+0 through D+6)
- **Model:** `ecmwf_ifs025` with bias correction enabled
- **API:** `https://api.open-meteo.com/v1/forecast`
- **Metric:** `temperature_2m_max` in local timezone

### HRRR / GFS Seamless (via Open-Meteo)

- **Coverage:** US cities only (`region == "us"`)
- **Horizon:** D+0 through D+2 (data beyond 48 hours is discarded at snapshot assembly)
- **Model:** `gfs_seamless` (HRRR+GFS blend — best short-range option for US)
- **API:** `https://api.open-meteo.com/v1/forecast`

### METAR (via Aviation Weather Center)

- **Coverage:** All 20 cities (uses ICAO station code per city)
- **Horizon:** D+0 only — current observed temperature, not a forecast
- **API:** `https://aviationweather.gov/api/data/metar`
- **Units:** Returns Celsius; converted to Fahrenheit for US stations

### Visual Crossing (actual temperatures)

- **Purpose:** Fetches historical actual daily high temperature for resolved markets
- **Coverage:** All cities (uses ICAO station code)
- **Required:** `vc_key` must be set in `config.json`
- **API:** `https://weather.visualcrossing.com/...`

### Best Forecast Selection

For each city-date, the bot picks a single "best" temperature from the available sources:

1. **HRRR** — if the city is in the US and HRRR has data for that date (within 48h)
2. **ECMWF** — for all other cases
3. **None** — if both are unavailable (no position is opened)

METAR is recorded in the snapshot for reference but is not used as the primary signal for position decisions.

---

## Trading Logic

### Market Discovery

Each scan cycle covers D+0 through D+3 for all 20 cities (80 city-date combinations). For each:

1. The Polymarket event is fetched via slug: `highest-temperature-in-{city}-on-{month}-{day}-{year}`
2. If no event is found, the date is skipped.
3. If an event is found and no local market file exists, one is created — but only if `MIN_HOURS ≤ hours_remaining ≤ MAX_HOURS`.

Markets discovered outside that window are never tracked. Markets already tracked continue to be updated regardless of hours remaining.

### Temperature Buckets

Polymarket temperature markets are divided into discrete buckets parsed from the question text:

| Pattern | Internal representation |
|---|---|
| `"X°F or below"` | `(-999.0, X)` — lower edge bucket |
| `"X°F or higher"` | `(X, 999.0)` — upper edge bucket |
| `"between X-Y°F"` | `(X, Y)` — interior range bucket |
| `"be X°F on"` | `(X, X)` — exact value bucket |

### Probability Estimation

**`bucket_prob(forecast, t_low, t_high, sigma)`**

- **Edge buckets** (sentinel -999 or 999): uses the normal CDF tail. Models the probability that the actual temperature falls below `t_high` (lower edge) or above `t_low` (upper edge), assuming the actual temperature is normally distributed around the forecast with standard deviation `sigma`.
- **Interior / exact buckets**: returns `1.0` if the forecast falls within the bucket, `0.0` otherwise. No distributional smoothing — if the forecast lands in the bucket, the bot assumes it will win.

### Entry Filters (all must pass)

1. No existing open position on this market
2. Best forecast temperature is available
3. Hours remaining ≥ `MIN_HOURS`
4. Exactly one bucket contains the forecast (`in_bucket` returns True)
5. That bucket's volume ≥ `MIN_VOLUME`
6. `EV = p × (1/ask − 1) − (1 − p) ≥ MIN_EV`
7. Kelly-sized bet ≥ $0.50
8. Real-time re-check: `bestAsk < MAX_PRICE` AND `spread ≤ MAX_SLIPPAGE`

Step 8 makes a separate live API call to Polymarket. If this call fails (network error), a warning is printed and the trade proceeds using the event-level prices — meaning the slippage and max-price guards may be bypassed in that case.

### Bet Sizing

```
raw_kelly  = (p × b − (1 − p)) / b     where b = 1/price − 1
kelly_frac = max(0, raw_kelly) × KELLY_FRACTION
bet        = min(kelly_frac × balance, MAX_BET)
shares     = bet / ask
```

The `MAX_BET` cap is a hard dollar limit. As balance grows, Kelly sizing grows but is always bounded by `MAX_BET`.

---

## Position Management & Exits

Positions are exited via five independent mechanisms:

### 1. Stop-Loss

Triggered in the full scan cycle when the current bid ≤ `stop_price`.

- Default stop: 80% of entry price (implicitly `entry × 0.80`)
- `close_reason`: `"stop_loss"`

### 2. Trailing Stop to Breakeven

When the current price reaches `entry × 1.20` (a 20% unrealized gain), `stop_price` is moved up to `entry`. If the price later falls back to entry, the position closes at breakeven.

- Activated in both `scan_and_update` and `monitor_positions`
- `close_reason`: `"trailing_stop"` / `"trailing_be"` (displayed as `TRAILING BE`)
- Flag `trailing_activated = True` is recorded on the position

### 3. Forecast-Change Close

If the current best forecast shifts away from the bet's bucket, the position is closed early. Both conditions must be true:

- The forecast is no longer inside the bet's bucket (`not in_bucket`)
- The forecast is far enough from the bucket midpoint:
  - Buffer: **2°F** for Fahrenheit markets, **1°C** for Celsius markets
  - This prevents closing on minor forecast oscillations

Edge-bucket positions (sentinel endpoints) are never closed this way — the midpoint calculation falls back to the current forecast, making the distance check always fail.

- `close_reason`: `"forecast_changed"`

### 4. Take-Profit (monitor only)

Dynamic take-profit thresholds applied between full scans based on hours remaining:

| Hours to resolution | Take-profit threshold |
|---|---|
| < 24h | None — hold to resolution |
| 24–48h | $0.85 per share |
| ≥ 48h | $0.75 per share |

The higher threshold at 24–48h reflects that market prices converge toward certainty as resolution approaches.

- `close_reason`: `"take_profit"`

### 5. Auto-Resolution

After every full scan, all markets with open positions are checked against Polymarket's resolution status:

- YES price ≥ 0.95 → **WIN**: `pnl = shares × (1.0 − entry_price)`
- YES price ≤ 0.05 → **LOSS**: `pnl = −cost`
- Otherwise → still open, check again next scan

- `close_reason`: `"resolved"`

### Balance Accounting

All exit paths restore funds as `balance += cost + pnl`. For a full loss, `pnl = -cost` so net returned is zero. Costs are subtracted immediately when a trade is opened.

---

## Data Persistence

All data is stored in the `data/` directory (created automatically), relative to the working directory.

### `data/state.json`

Global bot state. Written after every full scan and whenever a stop or take-profit fires in the monitor loop.

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

### `data/markets/{city_slug}_{date}.json`

One file per city-date pair (e.g., `data/markets/nyc_2026-04-23.json`). Updated every scan cycle. Key fields:

| Field | Description |
|---|---|
| `status` | `"open"` / `"closed"` / `"resolved"` |
| `position` | `null` or a position object (see below) |
| `forecast_snapshots` | List of per-scan forecast readings from all sources |
| `market_snapshots` | List of per-scan price snapshots (top-priced bucket) |
| `all_outcomes` | Full list of buckets with current prices (refreshed each scan) |
| `actual_temp` | Historical actual high temperature (filled after resolution) |
| `hours_at_discovery` | Hours remaining when the market was first found |

**Position object fields:**

| Field | Description |
|---|---|
| `entry_price` | Ask price at time of entry |
| `shares` | Number of YES tokens purchased |
| `cost` | Dollars staked |
| `p`, `ev`, `kelly` | Signal values at entry |
| `forecast_temp`, `forecast_src`, `sigma` | Forecast inputs |
| `bucket_low`, `bucket_high` | Temperature range bet on |
| `stop_price` | Current stop level (updated on trailing activation) |
| `status` | `"open"` / `"closed"` |
| `close_reason` | Exit mechanism that triggered close |
| `pnl` | Realized profit/loss |

### `data/calibration.json`

Stores calibrated sigma values per city-source pair. Updated by `run_calibration()`.

```json
{
  "nyc_ecmwf": { "sigma": 1.87, "n": 45, "updated_at": "2026-04-20T..." },
  "chicago_hrrr": { "sigma": 2.14, "n": 38, "updated_at": "2026-04-18T..." }
}
```

---

## Calibration System

The calibration system refines the `sigma` uncertainty parameter for each city-source combination using historical forecast error.

**How it works:**

1. After each full scan, the number of resolved markets is checked.
2. If the total ≥ `CALIBRATION_MIN` (default 30), `run_calibration()` runs.
3. For each `(city, source)` pair, it collects the absolute errors `|forecast − actual|` across all resolved markets.
4. If a pair has ≥ `CALIBRATION_MIN` samples, its MAE becomes the new `sigma`.
5. If fewer samples exist, the pair keeps the hardcoded default (`SIGMA_F` or `SIGMA_C`).

The calibrated sigma is used in `bucket_prob` for edge buckets — a smaller sigma means the bot is more confident in the forecast, leading to more aggressive bets on edge buckets.

---

## Timing & Loop Structure

```
Startup
  └─ load calibration data
  └─ print startup banner

Main loop:
  ├─ Every 60 min (SCAN_INTERVAL):
  │     scan_and_update()
  │       ├─ For each of 20 cities:
  │       │     take_forecast_snapshot() → ECMWF + HRRR + METAR
  │       │     For each of D+0..D+3:
  │       │       fetch Polymarket event
  │       │       update prices and snapshots
  │       │       stop-loss check
  │       │       forecast-change close check
  │       │       open new position (if conditions met)
  │       └─ Auto-resolution pass over all open positions
  │             run calibration if enough data
  │
  └─ Every 10 min (MONITOR_INTERVAL) between scans:
        monitor_positions()
          └─ For each open position:
               fetch live bestBid
               trailing stop update
               take-profit check
               stop-loss check
```

**Error handling:** `ConnectionError` retries after 60 seconds. Any other exception also waits 60 seconds. `KeyboardInterrupt` exits cleanly and saves state.

**Note:** Both `config.json` and the `data/` directory are resolved relative to the current working directory. Always run from the project root.

---

## Function Reference

### Math

| Function | Signature | Returns |
|---|---|---|
| `norm_cdf` | `(x: float)` | Standard normal CDF value |
| `bucket_prob` | `(forecast, t_low, t_high, sigma=None)` | Probability forecast falls in bucket |
| `calc_ev` | `(p, price)` | Expected value of a YES token bet |
| `calc_kelly` | `(p, price)` | Kelly fraction (after KELLY_FRACTION multiplier) |
| `bet_size` | `(kelly, balance)` | Dollar bet size (capped at MAX_BET) |

### Calibration

| Function | Description |
|---|---|
| `load_cal()` | Reads `data/calibration.json`, returns dict |
| `get_sigma(city_slug, source)` | Returns calibrated sigma or hardcoded default |
| `run_calibration(markets)` | Recalculates sigma from resolved markets, saves file |

### Forecasts

| Function | Description |
|---|---|
| `get_ecmwf(city_slug, dates)` | ECMWF daily max temps for requested dates |
| `get_hrrr(city_slug, dates)` | HRRR/GFS daily max temps (US only, ≤48h) |
| `get_metar(city_slug)` | Current observed temperature (D+0 only) |
| `get_actual_temp(city_slug, date_str)` | Historical actual temp via Visual Crossing |
| `check_market_resolved(market_id)` | `None` / `True` (win) / `False` (loss) |

### Polymarket

| Function | Description |
|---|---|
| `get_polymarket_event(city_slug, month, day, year)` | Fetches event by canonical slug |
| `get_market_price(market_id)` | Current YES price for a single market |
| `parse_temp_range(question)` | Parses question text into `(t_low, t_high)` |
| `hours_to_resolution(end_date_str)` | Floating-point hours until market closes |
| `in_bucket(forecast, t_low, t_high)` | True if forecast is within the temperature range |

### Market Data

| Function | Description |
|---|---|
| `market_path(city_slug, date_str)` | Returns `Path` to the market JSON file |
| `load_market(city_slug, date_str)` | Loads market dict or `None` if not found |
| `save_market(market)` | Writes market dict to its JSON file |
| `load_all_markets()` | Loads all market files from `data/markets/` |
| `new_market(city_slug, date_str, event, hours)` | Creates a fresh market record dict |

### State

| Function | Description |
|---|---|
| `load_state()` | Reads `data/state.json` or returns defaults |
| `save_state(state)` | Writes state dict to `data/state.json` |

### Core Logic

| Function | Description |
|---|---|
| `take_forecast_snapshot(city_slug, dates)` | Fetches all sources, returns per-date snapshots |
| `scan_and_update()` | Main scan: updates all markets, manages positions |
| `monitor_positions()` | Quick inter-scan stop/take-profit check |

### Reporting

| Function | Description |
|---|---|
| `print_status()` | Prints balance, trade counts, and open positions |
| `print_report()` | Prints full resolved-trade breakdown by city |

### Entry Point

| Function | Description |
|---|---|
| `run_loop()` | Starts the main loop with startup banner |
