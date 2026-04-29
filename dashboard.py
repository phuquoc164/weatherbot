#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — WeatherBot Operations Center Dashboard Backend
=============================================================
Reads JSON files written by weatherbot.py and serves a real-time UI via
FastAPI REST endpoints, WebSocket push, and a file-watcher background task.

Usage:
    python dashboard.py [--port 8050] [--host 0.0.0.0]
"""

import json
import asyncio
import argparse
from contextlib import asynccontextmanager
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

# =============================================================================
# PATH CONSTANTS
# =============================================================================

BASE_DIR         = Path(__file__).parent
DATA_DIR         = BASE_DIR / "data"
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
CALIBRATION_FILE = DATA_DIR / "calibration.json"
RUNS_DIR         = BASE_DIR / "runs"

STRATEGY_VARIANTS = ["baseline", "prob_model", "time_decay", "dynamic_ev"]

# =============================================================================
# LOCATIONS  (mirrored from weatherbot.py)
# =============================================================================

LOCATIONS = {
    "nyc":          {"lat":  40.7772, "lon":  -73.8726, "name": "New York City", "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat":  41.9742, "lon":  -87.9073, "name": "Chicago",       "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat":  25.7959, "lon":  -80.2870, "name": "Miami",         "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat":  32.8471, "lon":  -96.8518, "name": "Dallas",        "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat":  47.4502, "lon": -122.3088, "name": "Seattle",       "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat":  33.6407, "lon":  -84.4277, "name": "Atlanta",       "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat":  51.5048, "lon":    0.0495, "name": "London",        "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat":  48.9962, "lon":    2.5979, "name": "Paris",         "station": "LFPG", "unit": "C", "region": "eu"},
    "munich":       {"lat":  48.3537, "lon":   11.7750, "name": "Munich",        "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat":  40.1281, "lon":   32.9951, "name": "Ankara",        "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat":  37.4691, "lon":  126.4505, "name": "Seoul",         "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat":  35.7647, "lon":  140.3864, "name": "Tokyo",         "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat":  31.1443, "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":   1.3502, "lon":  103.9940, "name": "Singapore",     "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat":  26.7606, "lon":   80.8893, "name": "Lucknow",       "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat":  32.0114, "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat":  43.6772, "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN", "unit": "C", "region": "oc"},
}

# =============================================================================
# IN-MEMORY STATE
# =============================================================================

activity_feed: deque = deque(maxlen=100)   # recent events
previous_markets: dict = {}         # last snapshot keyed by stem
connected_clients: set = set()      # active WebSocket connections

# =============================================================================
# DATA READING HELPERS
# =============================================================================


def read_json(path: Path) -> Optional[dict]:
    """Read a JSON file; return None if missing or corrupt."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_state(state_file: Path = STATE_FILE) -> dict:
    """Read state.json with safe defaults."""
    defaults = {
        "balance": 0.0,
        "starting_balance": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "peak_balance": 0.0,
    }
    data = read_json(state_file)
    if data is None:
        return defaults
    defaults.update(data)
    return defaults


def read_all_markets(markets_dir: Path = MARKETS_DIR) -> dict:
    """Read all data/markets/*.json; keyed by file stem (e.g. 'nyc_2026-03-24')."""
    markets = {}
    if not markets_dir.exists():
        return markets
    for path in sorted(markets_dir.glob("*.json")):
        data = read_json(path)
        if data is not None:
            markets[path.stem] = data
    return markets


def read_calibration(calibration_file: Path = CALIBRATION_FILE) -> Optional[dict]:
    """Read calibration.json; return None if missing."""
    return read_json(calibration_file)


def check_bot_status() -> dict:
    """Return running/stopped status by scanning processes for weatherbot.py."""
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_info", "create_time"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any("weatherbot.py" in arg for arg in cmdline):
                mem_mb = round(proc.info["memory_info"].rss / 1024 / 1024, 1) if proc.info.get("memory_info") else 0
                uptime_s = int(datetime.now().timestamp() - proc.info.get("create_time", 0))
                return {
                    "running": True,
                    "pid": proc.info["pid"],
                    "cpu_percent": proc.info.get("cpu_percent", 0.0),
                    "memory_mb": mem_mb,
                    "uptime_seconds": uptime_s,
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"running": False, "pid": None, "cpu_percent": 0.0, "memory_mb": 0.0, "uptime_seconds": 0}

# =============================================================================
# ACTIVITY RECONSTRUCTION
# =============================================================================


def detect_changes(old_markets: dict, new_markets: dict) -> list[dict]:
    """Compare market snapshots and generate activity feed events."""
    events = []
    now = datetime.now(timezone.utc).isoformat()

    for key, new_data in new_markets.items():
        old_data = old_markets.get(key)
        city = new_data.get("city_name", key)

        if old_data is None:
            events.append({"ts": now, "type": "scan", "msg": f"SCAN New market: {city} {new_data.get('date', '')}"})
            continue

        old_pos = old_data.get("position")
        new_pos = new_data.get("position")

        # New position opened
        if old_pos is None and new_pos is not None:
            bucket = f"{new_pos.get('bucket_low')}-{new_pos.get('bucket_high')}{new_data.get('unit', '')}"
            events.append({
                "ts": now, "type": "buy",
                "msg": f"BUY {city} ${new_pos.get('cost', 0):.0f} @ {new_pos.get('entry_price', 0):.3f} bucket {bucket} (EV +{new_pos.get('ev', 0):.2f})"
            })

        # Position closed
        if old_pos and new_pos and old_pos.get("status") == "open" and new_pos.get("status") == "closed":
            reason = new_pos.get("close_reason", "unknown")
            pnl = new_pos.get("pnl", 0) or 0
            sign = "+" if pnl >= 0 else ""
            events.append({
                "ts": now, "type": "stop" if pnl < 0 else "resolved",
                "msg": f"EXIT {city} {reason} @ {new_pos.get('exit_price', 0):.3f} ({sign}${pnl:.2f})"
            })

        # New forecast snapshot
        old_snaps = len(old_data.get("forecast_snapshots", []))
        new_snaps = len(new_data.get("forecast_snapshots", []))
        if new_snaps > old_snaps:
            latest = new_data["forecast_snapshots"][-1]
            events.append({
                "ts": now, "type": "monitor",
                "msg": f"FORECAST {city} {(latest.get('best_source') or '').upper()} {latest.get('best')}°"
            })

    return events

# =============================================================================
# DASHBOARD AGGREGATION
# =============================================================================


def _resolve_current_price(market: dict, position: dict) -> tuple[float, float]:
    """Return (current_bid_price, unrealized_pnl) for an open position."""
    entry    = position.get("entry_price", 0)
    current  = entry
    market_id = position.get("market_id")
    if market_id:
        for o in market.get("all_outcomes", []):
            if o.get("market_id") == market_id:
                current = o.get("bid", o.get("price", entry))
                break
    unrealized = round((current - entry) * position.get("shares", 0), 2)
    return current, unrealized


def _project_open_position(market: dict, position: dict) -> dict:
    """Project an open position into the dashboard payload format."""
    current_price, unrealized_pnl = _resolve_current_price(market, position)
    return {
        "city":          market["city"],
        "city_name":     market.get("city_name", market["city"]),
        "date":          market["date"],
        "unit":          market.get("unit", "F"),
        "bucket_low":    position.get("bucket_low"),
        "bucket_high":   position.get("bucket_high"),
        "entry_price":   position.get("entry_price", 0),
        "current_price": current_price,
        "ev":            position.get("ev"),
        "kelly":         position.get("kelly"),
        "cost":          position.get("cost"),
        "pnl":           unrealized_pnl,
        "forecast_src":  position.get("forecast_src"),
        "sigma":         position.get("sigma"),
    }


def _project_closed_position(market: dict, position: dict) -> dict:
    """Project a closed position into the dashboard payload format."""
    return {
        "city":         market["city"],
        "city_name":    market.get("city_name", market["city"]),
        "date":         market["date"],
        "unit":         market.get("unit", "F"),
        "bucket_low":   position.get("bucket_low"),
        "bucket_high":  position.get("bucket_high"),
        "entry_price":  position.get("entry_price"),
        "exit_price":   position.get("exit_price"),
        "pnl":          position.get("pnl", 0),
        "cost":         position.get("cost"),
        "close_reason": position.get("close_reason", "unknown"),
        "opened_at":    position.get("opened_at"),
        "closed_at":    position.get("closed_at"),
    }


def _project_latest_forecast(market: dict) -> Optional[dict]:
    """Return the latest forecast snapshot for a market, or None."""
    snaps = market.get("forecast_snapshots", [])
    if not snaps:
        return None
    latest = snaps[-1]
    return {
        "city":        market["city"],
        "city_name":   market.get("city_name", market["city"]),
        "date":        market["date"],
        "unit":        market.get("unit", "F"),
        "horizon":     latest.get("horizon"),
        "ecmwf":       latest.get("ecmwf"),
        "hrrr":        latest.get("hrrr"),
        "metar":       latest.get("metar"),
        "best":        latest.get("best"),
        "best_source": latest.get("best_source"),
    }


def _compute_equity_kpis(starting: float, open_positions: list,
                          closed_positions: list, markets: dict) -> dict:
    """Compute KPI fields: equity, total P&L, realized/unrealized P&L, win rate, max drawdown."""
    realized_pnl   = round(sum(p["pnl"] for p in closed_positions), 2)
    unrealized_pnl = round(sum(p["pnl"] for p in open_positions), 2)
    open_cost      = round(sum(p.get("cost", 0) for p in open_positions), 2)
    cash           = round(starting + realized_pnl - open_cost, 2)
    equity         = round(cash + open_cost + unrealized_pnl, 2)
    total_pnl      = round(equity - starting, 2)
    total_pnl_pct  = round(total_pnl / starting * 100, 2) if starting else 0.0

    wins         = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
    total_closed = len(closed_positions)
    win_rate     = (wins / total_closed * 100) if total_closed > 0 else None

    # Replay equity chronologically to find worst peak-to-trough (max drawdown)
    events = [
        (pos["closed_at"], pos.get("pnl", 0) or 0)
        for m in markets.values()
        if (pos := m.get("position")) and pos.get("status") == "closed" and pos.get("closed_at")
    ]
    events.sort(key=lambda x: x[0])
    running_equity = starting
    peak = starting
    max_drawdown = 0.0
    for _, pnl_val in events:
        running_equity += pnl_val
        if running_equity > peak:
            peak = running_equity
        dd = ((peak - running_equity) / peak * 100) if peak > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd
    current_dd = ((peak - equity) / peak * 100) if equity < peak and peak > 0 else 0.0
    if current_dd > max_drawdown:
        max_drawdown = current_dd

    return {
        "equity":         equity,
        "total_pnl":      total_pnl,
        "total_pnl_pct":  total_pnl_pct,
        "realized_pnl":   realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "win_rate":       round(win_rate, 1) if win_rate is not None else None,
        "max_drawdown":   round(max_drawdown, 1),
    }


def build_dashboard_data(
    data_dir: Path = DATA_DIR,
    *,
    is_variant: bool = False,
) -> dict:
    """Build the complete dashboard payload."""
    state_file       = data_dir / "state.json"
    markets_dir      = data_dir / "markets"
    calibration_file = data_dir / "calibration.json"

    state       = read_state(state_file)
    markets     = read_all_markets(markets_dir)
    calibration = read_calibration(calibration_file)
    bot_status  = check_bot_status() if not is_variant else {
        "running": False, "pid": None, "cpu_percent": 0.0,
        "memory_mb": 0.0, "uptime_seconds": 0,
    }

    open_positions   = []
    closed_positions = []
    forecasts        = []
    for m in markets.values():
        pos = m.get("position")
        if pos and pos.get("status") == "open":
            open_positions.append(_project_open_position(m, pos))
        elif pos and pos.get("status") == "closed":
            closed_positions.append(_project_closed_position(m, pos))
        f = _project_latest_forecast(m)
        if f:
            forecasts.append(f)

    closed_positions.sort(key=lambda x: x.get("closed_at") or "", reverse=True)

    starting = state.get("starting_balance", 1000.0)
    kpi      = _compute_equity_kpis(starting, open_positions, closed_positions, markets)
    equity   = kpi["equity"]

    # Reconstruct balance history from closed positions so period filters work correctly.
    # For the main bot, append a live point so the chart always ends at the current state.
    bh = _build_variant_balance_history(closed_positions, starting)
    if not is_variant:
        now_str = datetime.now(timezone.utc).isoformat()
        if not bh or bh[-1]["balance"] != round(equity, 2):
            bh.append({"ts": now_str, "balance": round(equity, 2)})

    return {
        "state":           state,
        "kpi":             kpi,
        "open_positions":  open_positions,
        "closed_positions": closed_positions,
        "forecasts":       forecasts,
        "calibration":     calibration,
        "bot_status":      bot_status,
        "balance_history": bh,
        "activity":        list(activity_feed) if not is_variant else [],
        "locations":       LOCATIONS,
    }

# =============================================================================
# FASTAPI APP
# =============================================================================

@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(watch_data_directory())
    yield

app = FastAPI(title="WeatherBot Operations Center", version="1.0.0", lifespan=lifespan)

# Mount static files if the directory exists
_static_dir = BASE_DIR / "dashboard_ui" / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Jinja2 templates
_templates_dir = BASE_DIR / "dashboard_ui" / "templates"
_templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(_templates_dir))

# ---------------------------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main dashboard page."""
    data = build_dashboard_data()
    return templates.TemplateResponse(request=request, name="index.html", context={"data": data})


@app.get("/api/state")
async def api_state():
    return read_state()


@app.get("/api/markets")
async def api_markets():
    return read_all_markets()


@app.get("/api/markets/{city}/{date}")
async def api_market_detail(city: str, date: str):
    stem = f"{city}_{date}"
    path = MARKETS_DIR / f"{stem}.json"
    data = read_json(path)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Market {stem} not found")
    return data


@app.get("/api/calibration")
async def api_calibration():
    return read_calibration() or {}


@app.get("/api/bot-status")
async def api_bot_status():
    return check_bot_status()


@app.get("/api/dashboard")
async def api_dashboard():
    return build_dashboard_data()


# ---------------------------------------------------------------------------
# Strategy variant helpers
# ---------------------------------------------------------------------------

def _variant_pid_running(name: str) -> bool:
    pid_path = RUNS_DIR / name / "weatherbot.pid"
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def _build_variant_balance_history(closed_positions: list, starting: float) -> list:
    """Reconstruct [{ts, balance}] from closed positions for variant balance charts."""
    events = sorted(
        [(p["closed_at"], p.get("pnl", 0) or 0)
         for p in closed_positions if p.get("closed_at")],
        key=lambda x: x[0],
    )
    if not events:
        return []
    running = starting
    history = []
    for ts, pnl_val in events:
        running += pnl_val
        history.append({"ts": ts, "balance": round(running, 2)})
    return history


def _equity_series(markets_dir: Path) -> list[float]:
    """Equity replay over closed positions, capped at 50 points for sparklines."""
    if not markets_dir.exists():
        return []
    events = []
    for fp in markets_dir.glob("*.json"):
        try:
            m = json.loads(fp.read_text(encoding="utf-8"))
            pos = m.get("position")
            if pos and pos.get("status") == "closed" and pos.get("closed_at"):
                events.append((pos["closed_at"], pos.get("pnl") or 0))
        except (json.JSONDecodeError, OSError):
            continue
    if len(events) < 2:
        return []
    events.sort(key=lambda x: x[0])
    equity, series = 1000.0, [1000.0]
    for _, pnl in events:
        equity += pnl
        series.append(round(equity, 2))
    return series[-50:]


# ---------------------------------------------------------------------------
# Strategy variant endpoints
# ---------------------------------------------------------------------------

@app.get("/api/variants")
async def api_variants():
    """List configured variants and their running status."""
    variants_info = []
    for name in STRATEGY_VARIANTS:
        vdir = RUNS_DIR / name
        if not (vdir / "config.json").exists():
            continue
        variants_info.append({
            "name":    name,
            "label":   name,
            "running": _variant_pid_running(name),
        })
    return {
        "main_running": STATE_FILE.exists(),
        "variants":     variants_info,
    }


@app.get("/api/source/{name}/dashboard")
async def api_variant_dashboard(name: str):
    """Return dashboard data for a specific strategy variant."""
    if name not in STRATEGY_VARIANTS:
        raise HTTPException(status_code=404, detail=f"Unknown variant '{name}'")
    result = build_dashboard_data(data_dir=RUNS_DIR / name / "data", is_variant=True)
    result["bot_status"]["running"] = _variant_pid_running(name)
    return result


def _summarize_source(name: str, label: str, state: dict, closed: list,
                       markets_dir: Path, running: bool, flags: list) -> dict:
    """Build a single-source summary dict for the comparison endpoint."""
    wins      = sum(1 for p in closed if (p.get("pnl") or 0) > 0)
    total_pnl = round(sum(p.get("pnl") or 0 for p in closed), 2)
    evs       = [p["ev"] for p in closed if p.get("ev") is not None]
    start_bal = state.get("starting_balance", 1000.0)
    balance   = state.get("balance", start_bal)
    return {
        "name":     name,
        "label":    label,
        "balance":  balance,
        "pnl":      total_pnl,
        "roi":      round(total_pnl / start_bal * 100, 2) if start_bal else 0.0,
        "trades":   len(closed),
        "wins":     wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "avg_ev":   round(sum(evs) / len(evs), 4) if evs else None,
        "flags":    flags,
        "running":  running,
        "series":   _equity_series(markets_dir),
    }


@app.get("/api/comparison")
async def api_comparison():
    """Compact P&L summary of all variants and the main thread."""
    sources = []

    if STATE_FILE.exists():
        state   = read_state()
        markets = read_all_markets()
        closed  = [
            pos for m in markets.values()
            if (pos := m.get("position")) and pos.get("status") == "closed"
        ]
        sources.append(_summarize_source("main", "Main thread", state, closed, MARKETS_DIR, True, []))

    for name in STRATEGY_VARIANTS:
        vdir = RUNS_DIR / name
        if not (vdir / "config.json").exists():
            continue

        data_dir    = vdir / "data"
        state_path  = data_dir / "state.json"
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        markets_dir = data_dir / "markets"
        closed = []
        if markets_dir.exists():
            for fp in markets_dir.glob("*.json"):
                try:
                    m   = json.loads(fp.read_text(encoding="utf-8"))
                    pos = m.get("position")
                    if pos and pos.get("status") == "closed":
                        closed.append(pos)
                except (json.JSONDecodeError, OSError):
                    continue

        flags = []
        try:
            cfg   = json.loads((vdir / "config.json").read_text(encoding="utf-8"))
            strat = cfg.get("strategy", {})
            flags = [k for k, v in strat.items() if v is True]
        except (json.JSONDecodeError, OSError):
            pass

        sources.append(_summarize_source(
            name, name, state, closed, markets_dir,
            _variant_pid_running(name), flags,
        ))

    return {
        "sources":      sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/simulation.json")
async def simulation_json():
    """Build the payload expected by sim_dashboard_report.html (retro dashboard)."""
    state = read_state()
    markets = read_all_markets()

    positions = {}
    trades = []

    for key, mkt in markets.items():
        pos = mkt.get("position")
        if not pos:
            continue

        city_name = mkt.get("city_name", mkt.get("city", ""))
        date      = mkt.get("date", "")
        unit_sym  = "°F" if mkt.get("unit", "F") == "F" else "°C"
        b_low     = pos.get("bucket_low")
        b_high    = pos.get("bucket_high")
        question  = pos.get("question") or f"Highest temp in {city_name} on {date}: {b_low}-{b_high}{unit_sym}"

        if pos.get("status") == "open":
            current_price, unrealized = _resolve_current_price(mkt, pos)

            positions[key] = {
                "question":      question,
                "location":      city_name,
                "date":          date,
                "entry_price":   pos.get("entry_price", 0),
                "current_price": current_price,
                "cost":          pos.get("cost", 0),
                "pnl":           unrealized,
                "ev":            pos.get("ev", 0),
                "kelly_pct":     pos.get("kelly", 0),
            }
            trades.append({
                "type":        "entry",
                "question":    question,
                "location":    city_name,
                "date":        date,
                "entry_price": pos.get("entry_price", 0),
                "our_prob":    pos.get("p", 0),
                "ev":          pos.get("ev", 0),
                "kelly_pct":   pos.get("kelly", 0),
                "cost":        pos.get("cost", 0),
                "opened_at":   pos.get("opened_at", ""),
            })

        elif pos.get("status") == "closed":
            trades.append({
                "type":         "exit",
                "question":     question,
                "location":     city_name,
                "date":         date,
                "pnl":          pos.get("pnl", 0),
                "ev":           pos.get("ev", 0),
                "kelly_pct":    pos.get("kelly", 0),
                "cost":         pos.get("cost", 0),
                "opened_at":    pos.get("opened_at", ""),
                "closed_at":    pos.get("closed_at", ""),
                "close_reason": pos.get("close_reason", ""),
            })

    trades.sort(key=lambda t: t.get("opened_at") or t.get("closed_at") or "")

    return {
        "balance":          state.get("balance", 0.0),
        "starting_balance": state.get("starting_balance", 0.0),
        "wins":             state.get("wins", 0),
        "losses":           state.get("losses", 0),
        "total_trades":     state.get("total_trades", 0),
        "peak_balance":     state.get("peak_balance", state.get("balance", 0.0)),
        "positions":        positions,
        "trades":           trades,
    }


@app.get("/retro", response_class=HTMLResponse)
async def retro():
    """Serve the retro terminal dashboard (sim_dashboard_report.html)."""
    html_path = BASE_DIR / "dashboard_ui" / "sim_dashboard_report.html"
    if not html_path.exists():
        return HTMLResponse(content="sim_dashboard_report.html not found", status_code=404)
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


async def broadcast(payload: dict):
    """Push JSON payload to all connected WebSocket clients."""
    message = json.dumps(payload)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Send full state on connect
        data = build_dashboard_data()
        await websocket.send_text(json.dumps({"type": "full_update", "data": data}))
        # Keep alive — receive messages (ping/close) until disconnect
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a heartbeat ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(websocket)

# ---------------------------------------------------------------------------
# File-watcher background task
# ---------------------------------------------------------------------------


async def watch_data_directory():
    """Monitor data/ directory with watchfiles and push updates to clients."""
    global previous_markets

    try:
        from watchfiles import awatch
    except ImportError:
        # watchfiles not available — fall back to polling every 10 s
        while True:
            await asyncio.sleep(10)
            new_markets = read_all_markets()
            events = detect_changes(previous_markets, new_markets)
            for ev in events:
                activity_feed.appendleft(ev)
            previous_markets = new_markets
            if connected_clients:
                data = build_dashboard_data()
                await broadcast({"type": "full_update", "data": data})
            return

    previous_markets = read_all_markets()

    async for changes in awatch(str(DATA_DIR)):
        new_markets = read_all_markets()
        events = detect_changes(previous_markets, new_markets)
        for ev in events:
            activity_feed.appendleft(ev)
        previous_markets = new_markets
        if connected_clients:
            data = build_dashboard_data()
            await broadcast({"type": "full_update", "data": data})



# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WeatherBot Dashboard Server")
    parser.add_argument("--port", type=int, default=8050, help="Port to listen on (default: 8050)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args = parser.parse_args()

    uvicorn.run("dashboard:app", host=args.host, port=args.port, reload=False)
