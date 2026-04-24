# WeatherBot — Polymarket Weather Trading Bot

Automated weather market trading bot for Polymarket. Finds mispriced temperature outcomes using real forecast data from multiple sources across 20 cities worldwide.

No SDK. No black box. Pure Python.

---

## How It Works

Polymarket runs markets like "Will the highest temperature in Chicago be between 46–47°F on March 7?" These markets are often mispriced — the forecast says 78% likely but the market is trading at 8 cents.

The bot:
1. Fetches forecasts from ECMWF and HRRR via Open-Meteo (free, no key required)
2. Gets real-time observations from METAR airport stations
3. Finds the matching temperature bucket on Polymarket
4. Calculates Expected Value — only enters if the math is positive
5. Sizes the position using fractional Kelly Criterion
6. Monitors stops every 10 minutes, full scan every hour
7. Auto-resolves markets by querying the Polymarket API directly

---

## Why Airport Coordinates Matter

Most bots use city center coordinates. That's wrong.

Every Polymarket weather market resolves on a specific airport station. NYC resolves on LaGuardia (KLGA), Dallas on Love Field (KDAL) — not DFW. The difference between city center and airport can be 3–8°F. On markets with 1–2°F buckets, that's the difference between the right trade and a guaranteed loss.

| City | Station | Airport |
|------|---------|---------|
| NYC | KLGA | LaGuardia |
| Chicago | KORD | O'Hare |
| Miami | KMIA | Miami Intl |
| Dallas | KDAL | Love Field |
| Seattle | KSEA | Sea-Tac |
| Atlanta | KATL | Hartsfield |
| London | EGLC | London City |
| Tokyo | RJTT | Haneda |
| ... | ... | ... |

---

## Installation

```bash
git clone https://github.com/alteregoeth-ai/weatherbot
cd weatherbot
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Create `config.json` in the project root:

```json
{
  "balance": 10000.0,
  "max_bet": 20.0,
  "min_ev": 0.05,
  "max_price": 0.45,
  "min_volume": 2000,
  "min_hours": 2.0,
  "max_hours": 72.0,
  "kelly_fraction": 0.25,
  "max_slippage": 0.03,
  "scan_interval": 3600,
  "calibration_min": 30,
  "vc_key": "YOUR_VISUAL_CROSSING_KEY"
}
```

Get a free Visual Crossing API key at [visualcrossing.com](https://www.visualcrossing.com) — used to fetch actual temperatures after market resolution.

---

## Usage

### Bot

```bash
source venv/bin/activate

python weatherbot.py           # start the bot — scans every hour
python weatherbot.py status    # balance and open positions
python weatherbot.py report    # full breakdown of all resolved markets
```

**Run in background:**

```bash
nohup venv/bin/python -u weatherbot.py >> nohup.out 2>&1 &
echo $! > weatherbot.pid       # save PID

tail -f nohup.out              # follow the log
kill $(cat weatherbot.pid)     # stop the bot
```

### Dashboards

Start the dashboard server:

```bash
venv/bin/python dashboard.py
```

Two dashboards are served from the same process:

| URL | Style | Description |
|---|---|---|
| `http://localhost:8050/` | Bloomberg dark | KPIs, world map, WebSocket push |
| `http://localhost:8050/retro` | Retro terminal | Balance chart, open positions, EV log |

**Custom port:**

```bash
venv/bin/python dashboard.py --port 9000
```

**Typical workflow (2 terminals):**

```
Terminal 1:  venv/bin/python weatherbot.py    # bot
Terminal 2:  venv/bin/python dashboard.py     # both dashboards
```

---

## Dashboard Features

### Bloomberg Dashboard (`/`)

- **KPI Strip** — Starting balance, open cost, realized/unrealized P&L, cash, win rate, drawdown
- **World Map** — Interactive Leaflet.js map with 20 city markers showing forecast, EV, and position status
- **Open Positions** — Live table with entry → current price and unrealized P&L
- **Trade History** — Closed positions with close reason and realized P&L
- **Forecast Sources** — Side-by-side comparison of ECMWF, HRRR, and METAR for all cities
- **Calibration** — Forecast accuracy (sigma) per city/source
- **Activity Feed** — Real-time event log (buys, exits, forecasts)
- **Balance Chart** — Equity history over time
- **Real-time updates** via WebSocket (file-watcher based, no polling)

### Retro Terminal Dashboard (`/retro`)

- Balance chart with live delta animations
- Open positions with Kelly % and EV
- Chronological trade log (entry + exit events)
- EV signal log for recent entries
- Polls `/simulation.json` every 10 seconds

---

## Bot Features

- **20 cities** across 6 regions (US, Europe, Asia, South America, Canada, Oceania)
- **3 forecast sources** — ECMWF (global, 7-day), HRRR/GFS (US, 48h), METAR (real-time observed)
- **Expected Value filter** — skips trades where EV < `min_ev`
- **Kelly Criterion** — quarter-Kelly bet sizing, capped at `max_bet`
- **Stop-loss** — 20% stop on every position
- **Trailing stop** — moves to breakeven at +20% gain
- **Take-profit** — dynamic thresholds based on hours to resolution
- **Forecast-change close** — exits early if forecast shifts out of the bet's bucket
- **Slippage filter** — skips markets with spread > `max_slippage`
- **Self-calibration** — learns forecast accuracy (sigma) per city/source over time

---

## Data Storage

All data is saved to `data/` — one JSON file per market. Each file contains:
- Hourly forecast snapshots (ECMWF, HRRR, METAR)
- Market price history
- Position details (entry, stop, PnL)
- Final resolution outcome

This data drives the dashboards and the self-calibration system.

---

## APIs Used

| API | Auth | Purpose |
|-----|------|---------|
| Open-Meteo | None | ECMWF + HRRR/GFS forecasts |
| Aviation Weather (METAR) | None | Real-time station observations |
| Polymarket Gamma | None | Market data and resolution |
| Visual Crossing | Free key | Historical temps for resolution |

---

## Documentation

Full documentation in `docs/`:

- [`docs/weatherbot.md`](docs/weatherbot.md) — bot configuration, trading logic, data persistence, function reference
- [`docs/dashboard.md`](docs/dashboard.md) — dashboard architecture, REST API, WebSocket, KPI calculations

---

## Disclaimer

This is not financial advice. Prediction markets carry real risk. Run the bot in paper-trading mode and study the results thoroughly before committing real capital.
