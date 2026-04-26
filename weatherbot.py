#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weatherbot.py — Weather Trading Bot for Polymarket
=====================================================
Tracks weather forecasts from 3 sources (ECMWF, HRRR, METAR),
compares with Polymarket markets, paper trades using Kelly criterion.

Usage:
    python weatherbot.py          # main loop
    python weatherbot.py report   # full report
    python weatherbot.py status   # balance and open positions
"""

import re
import sys
import json
import math
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# =============================================================================
# CONFIG
# =============================================================================

with open("config.json", encoding="utf-8") as f:
    _cfg = json.load(f)

BALANCE          = _cfg.get("balance", 10000.0)
MAX_BET          = _cfg.get("max_bet", 20.0)        # max bet per trade
MIN_EV           = _cfg.get("min_ev", 0.10)
MAX_PRICE        = _cfg.get("max_price", 0.45)
MIN_VOLUME       = _cfg.get("min_volume", 500)
MIN_HOURS        = _cfg.get("min_hours", 2.0)
MAX_HOURS        = _cfg.get("max_hours", 72.0)
KELLY_FRACTION   = _cfg.get("kelly_fraction", 0.25)
MAX_SLIPPAGE     = _cfg.get("max_slippage", 0.03)  # max allowed ask-bid spread
SCAN_INTERVAL    = _cfg.get("scan_interval", 3600)   # every hour
CALIBRATION_MIN  = _cfg.get("calibration_min", 30)
VC_KEY           = _cfg.get("vc_key", "")
SIGMA_F          = _cfg.get("sigma_f", 2.0)   # default forecast error for °F cities
SIGMA_C          = _cfg.get("sigma_c", 1.2)   # default forecast error for °C cities

# --- Strategy flags (all off by default; flip in config.json to test) ---
_strat           = _cfg.get("strategy", {})
STRAT_PROB_MODEL = _strat.get("prob_model_normal_cdf", False)   # improvement #1
STRAT_TIME_DECAY = _strat.get("time_decay", False)              # improvement #3
STRAT_DYNAMIC_EV = _strat.get("dynamic_min_ev", False)          # improvement #6
SIGMA_REF        = _strat.get("sigma_ref", 2.0)                 # reference sigma for dynamic EV scaling

DATA_DIR         = Path("data")
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
CALIBRATION_FILE = DATA_DIR / "calibration.json"

LOCATIONS = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9694,  "lon":    2.4414, "name": "Paris",         "station": "LFPB", "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN", "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc": "America/New_York", "chicago": "America/Chicago",
    "miami": "America/New_York", "dallas": "America/Chicago",
    "seattle": "America/Los_Angeles", "atlanta": "America/New_York",
    "london": "Europe/London", "paris": "Europe/Paris",
    "munich": "Europe/Berlin", "ankara": "Europe/Istanbul",
    "seoul": "Asia/Seoul", "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore",
    "lucknow": "Asia/Kolkata", "tel-aviv": "Asia/Jerusalem",
    "toronto": "America/Toronto", "sao-paulo": "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires", "wellington": "Pacific/Auckland",
}

MONTHS = ["january","february","march","april","may","june",
          "july","august","september","october","november","december"]

MARKET_TYPES = ("highest", "lowest")

# =============================================================================
# MATH
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=None):
    """For edge buckets — normal CDF. Interior buckets: normal CDF if STRAT_PROB_MODEL, else binary."""
    s = sigma or 2.0
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    if STRAT_PROB_MODEL:
        return norm_cdf((t_high - float(forecast)) / s) - norm_cdf((t_low - float(forecast)) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0

def calc_ev(p, price):
    if price <= 0 or price >= 1: return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    if price <= 0 or price >= 1: return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

_HORIZON_MULT = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.4}

def bet_size(kelly, balance, horizon_days=None):
    multiplier = _HORIZON_MULT.get(horizon_days, 1.0) if STRAT_TIME_DECAY and horizon_days is not None else 1.0
    raw = kelly * balance * multiplier
    return round(min(raw, MAX_BET), 2)

# =============================================================================
# CALIBRATION
# =============================================================================

_cal: dict = {}

def load_cal():
    if not CALIBRATION_FILE.exists():
        return {}
    raw = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    migrated = {}
    n_migrated = 0
    for k, v in raw.items():
        if k.count("_") == 1:  # legacy 2-segment key: city_source
            migrated[f"{k}_highest"] = v
            n_migrated += 1
        else:
            migrated[k] = v
    if n_migrated:
        CALIBRATION_FILE.write_text(json.dumps(migrated, indent=2), encoding="utf-8")
        print(f"  [MIGRATE] cal: {n_migrated} keys updated to 3-segment format")
    return migrated

def get_sigma(city_slug, source="ecmwf", market_type="highest"):
    key = f"{city_slug}_{source}_{market_type}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C

def run_calibration(markets):
    """Recalculates sigma from resolved markets, keyed by (city, source, market_type)."""
    resolved = [m for m in markets if m.get("resolved") and m.get("actual_temp") is not None]
    cal = load_cal()
    updated = []

    for market_type in MARKET_TYPES:
        type_resolved = [m for m in resolved if m.get("type", "highest") == market_type]
        for source in ["ecmwf", "hrrr", "metar"]:
            for city in set(m["city"] for m in type_resolved):
                group = [m for m in type_resolved if m["city"] == city]
                errors = []
                for m in group:
                    snap = next((s for s in reversed(m.get("forecast_snapshots", []))
                                 if s["source"] == source), None)
                    if snap and snap.get("temp") is not None:
                        errors.append(abs(snap["temp"] - m["actual_temp"]))
                if len(errors) < CALIBRATION_MIN:
                    continue
                mae  = sum(errors) / len(errors)
                key  = f"{city}_{source}_{market_type}"
                old  = cal.get(key, {}).get("sigma", SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C)
                new  = round(mae, 3)
                cal[key] = {"sigma": new, "n": len(errors), "updated_at": datetime.now(timezone.utc).isoformat()}
                if abs(new - old) > 0.05:
                    updated.append(f"{LOCATIONS[city]['name']} {source} [{market_type}]: {old:.2f}->{new:.2f}")

    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        print(f"  [CAL] {', '.join(updated)}")
    return cal

# =============================================================================
# FORECASTS
# =============================================================================

def get_ecmwf(city_slug, dates):
    """ECMWF via Open-Meteo with bias correction. Returns {date: {"max": v, "min": v}}."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max,temperature_2m_min&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                daily = data["daily"]
                for date, t_max, t_min in zip(
                    daily["time"],
                    daily["temperature_2m_max"],
                    daily["temperature_2m_min"],
                ):
                    if date not in dates:
                        continue
                    def _round(v):
                        return (round(v, 1) if unit == "C" else round(v)) if v is not None else None
                    result[date] = {"max": _round(t_max), "min": _round(t_min)}
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"  [ECMWF] {city_slug}: {e}")
    return result

def get_hrrr(city_slug, dates):
    """HRRR/GFS via Open-Meteo. US cities only, up to 48h horizon. Returns {date: {"max": v, "min": v}}."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=gfs_seamless"  # HRRR+GFS seamless — best option for US
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                daily = data["daily"]
                for date, t_max, t_min in zip(
                    daily["time"],
                    daily["temperature_2m_max"],
                    daily["temperature_2m_min"],
                ):
                    if date not in dates:
                        continue
                    result[date] = {
                        "max": round(t_max) if t_max is not None else None,
                        "min": round(t_min) if t_min is not None else None,
                    }
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"  [HRRR] {city_slug}: {e}")
    return result

def get_metar(city_slug):
    """Current observed temperature from METAR station. D+0 highest markets only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
        data = requests.get(url, timeout=(5, 8)).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                if unit == "F":
                    return round(float(temp_c) * 9/5 + 32)
                return round(float(temp_c), 1)
    except Exception as e:
        print(f"  [METAR] {city_slug}: {e}")
    return None

def _metar_local_date(obs: dict, tz: str) -> str:
    """Convert a METAR observation's obsTime (epoch seconds) to a local date string."""
    epoch = obs.get("obsTime")
    if epoch is None:
        return ""
    dt = datetime.fromtimestamp(int(epoch), tz=ZoneInfo(tz))
    return dt.strftime("%Y-%m-%d")

def get_metar_min(city_slug: str, date_str: str):
    """Lowest METAR-observed temperature over the local-day window for D+0 lowest markets."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    tz = TIMEZONES.get(city_slug, "UTC")
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json&hours=24"
        data = requests.get(url, timeout=(5, 8)).json()
        if not isinstance(data, list) or not data:
            return None
        temps_c = [
            float(obs["temp"])
            for obs in data
            if _metar_local_date(obs, tz) == date_str and obs.get("temp") is not None
        ]
        if not temps_c:
            return None
        min_c = min(temps_c)
        return round(min_c * 9/5 + 32) if unit == "F" else round(min_c, 1)
    except Exception as e:
        print(f"  [METAR-MIN] {city_slug}: {e}")
    return None

def get_actual_temp(city_slug, date_str, market_type="highest"):
    """Actual temperature via Visual Crossing for closed markets."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    vc_unit = "us" if unit == "F" else "metric"
    element = "tempmax" if market_type == "highest" else "tempmin"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements={element}"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        days = data.get("days", [])
        if days and days[0].get(element) is not None:
            return round(float(days[0][element]), 1)
    except Exception as e:
        print(f"  [VC] {city_slug} {date_str}: {e}")
    return None

def check_market_resolved(market_id):
    """
    Checks if the market closed on Polymarket and who won.
    Returns: None (still open), True (YES won), False (NO won)
    """
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 8))
        data = r.json()
        closed = data.get("closed", False)
        if not closed:
            return None
        # Check YES price — if ~1.0 then WIN, if ~0.0 then LOSS
        prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
        if yes_price >= 0.95:
            return True   # WIN
        elif yes_price <= 0.05:
            return False  # LOSS
        return None  # not yet determined
    except Exception as e:
        print(f"  [RESOLVE] {market_id}: {e}")
    return None

# =============================================================================
# POLYMARKET
# =============================================================================

def get_polymarket_event(city_slug, month, day, year, market_type="highest"):
    prefix = "highest-temperature" if market_type == "highest" else "lowest-temperature"
    slug = f"{prefix}-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=(5, 8))
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception:
        pass
    return None

def parse_resolution_station(url):
    """Extract ICAO station code from a Wunderground resolution URL.
    e.g. https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB -> LFPB
    """
    if not url:
        return None
    segment = url.rstrip("/").split("/")[-1]
    if len(segment) == 4 and segment.isalpha():
        return segment.upper()
    return None

def get_market_price(market_id):
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(3, 5))
        prices = json.loads(r.json().get("outcomePrices", "[0.5,0.5]"))
        return float(prices[0])
    except Exception:
        return None

def parse_temp_range(question):
    if not question: return None
    num = r'(-?\d+(?:\.\d+)?)'
    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m: return (-999.0, float(m.group(1)))
    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m: return (float(m.group(1)), 999.0)
    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m: return (float(m.group(1)), float(m.group(2)))
    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

def hours_to_resolution(end_date_str):
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0

def in_bucket(forecast, t_low, t_high):
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

# =============================================================================
# MARKET DATA STORAGE
# Each market is stored in a separate file:
#   data/markets/{city}_{date}_{market_type}.json
# Legacy files without the type suffix are migrated to _highest on first load.
# =============================================================================

def _normalize_legacy_filenames():
    """Rename pre-feature market files from {city}_{date}.json to {city}_{date}_highest.json."""
    for f in MARKETS_DIR.glob("*.json"):
        parts = f.stem.rsplit("_", 1)
        if parts[-1] in MARKET_TYPES:
            continue  # already typed
        new_path = f.with_name(f.stem + "_highest.json")
        if new_path.exists():
            print(f"  [WARN] Skipping rename: {new_path.name} already exists")
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        data.setdefault("type", "highest")
        new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        f.unlink()
        print(f"  [MIGRATE] {f.name} → {new_path.name}")

def market_path(city_slug, date_str, market_type="highest"):
    return MARKETS_DIR / f"{city_slug}_{date_str}_{market_type}.json"

def load_market(city_slug, date_str, market_type="highest"):
    p = market_path(city_slug, date_str, market_type)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

def save_market(market):
    mtype = market.get("type", "highest")
    p = market_path(market["city"], market["date"], mtype)
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")

def load_all_markets():
    _normalize_legacy_filenames()
    markets = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            markets.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return markets

def new_market(city_slug, date_str, event, hours, market_type="highest"):
    loc = LOCATIONS[city_slug]
    resolution_url     = event.get("resolutionSource")
    resolution_station = parse_resolution_station(resolution_url)
    if resolution_station:
        LOCATIONS[city_slug]["station"] = resolution_station  # keep in-memory station in sync
    station = resolution_station or loc["station"]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "unit":               loc["unit"],
        "station":            station,
        "resolution_source":  resolution_url,
        "event_end_date":     event.get("endDate", ""),
        "hours_at_discovery": round(hours, 1),
        "status":             "open",           # open | closed | resolved
        "position":           None,             # filled when position opens
        "actual_temp":        None,             # filled after resolution
        "resolved_outcome":   None,             # win / loss / no_position
        "pnl":                None,
        "forecast_snapshots": [],               # list of forecast snapshots
        "market_snapshots":   [],               # list of market price snapshots
        "all_outcomes":       [],               # all market buckets
        "type":               market_type,
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }

# =============================================================================
# STATE (balance and open positions)
# =============================================================================

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance":          BALANCE,
        "starting_balance": BALANCE,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE,
    }

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def calculate_balance_from_trades():
    """Ground truth balance calculated from market files instead of incremental tracking."""
    state = load_state()
    starting = state.get("starting_balance", BALANCE)
    markets = load_all_markets()

    total_cost = 0
    total_returned = 0
    for m in markets:
        pos = m.get("position")
        if not pos:
            continue
        total_cost += pos.get("cost", 0)
        if pos.get("status") == "closed":
            total_returned += pos.get("cost", 0) + (pos.get("pnl", 0) or 0)

    return round(starting - total_cost + total_returned, 2)

# =============================================================================
# CORE LOGIC
# =============================================================================

def _buckets_are_consistent(hi_low, hi_high, lo_low, lo_high):
    """Return True if the highest/lowest bucket pair is physically possible.

    The daily high must be >= the daily low, so the high bucket must not
    sit entirely below the low bucket.
    """
    return hi_high >= lo_low

def take_forecast_snapshot(city_slug, dates):
    """Fetches forecasts from all sources. Returns {date: {"highest": snap, "lowest": snap}}."""
    now_str  = datetime.now(timezone.utc).isoformat()
    ecmwf    = get_ecmwf(city_slug, dates)
    hrrr     = get_hrrr(city_slug, dates)
    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff_2 = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    loc      = LOCATIONS[city_slug]

    metar_max = get_metar(city_slug) if today in dates else None
    metar_min = get_metar_min(city_slug, today) if today in dates else None

    snapshots = {}
    for date in dates:
        ecmwf_day = ecmwf.get(date, {})
        hrrr_day  = hrrr.get(date, {}) if date <= cutoff_2 else {}

        t_max = ecmwf_day.get("max")
        t_min = ecmwf_day.get("min")
        if t_max is not None and t_min is not None and t_max < t_min:
            print(f"  [WARN] {city_slug} {date}: max ({t_max}) < min ({t_min}), skipping both")
            t_max = t_min = None
            ecmwf_day = {}
            hrrr_day  = {}

        date_snaps = {}
        for market_type in MARKET_TYPES:
            extreme = "max" if market_type == "highest" else "min"
            ecmwf_v = ecmwf_day.get(extreme)
            hrrr_v  = hrrr_day.get(extreme)
            metar_v = (metar_max if market_type == "highest" else metar_min) if date == today else None

            snap = {"ts": now_str, "ecmwf": ecmwf_v, "hrrr": hrrr_v, "metar": metar_v}
            if loc["region"] == "us" and hrrr_v is not None:
                snap["best"] = hrrr_v
                snap["best_source"] = "hrrr"
            elif ecmwf_v is not None:
                snap["best"] = ecmwf_v
                snap["best_source"] = "ecmwf"
            else:
                snap["best"] = None
                snap["best_source"] = None
            date_snaps[market_type] = snap

        snapshots[date] = date_snaps
    return snapshots

def scan_and_update():
    """Main function of one cycle: updates forecasts, opens/closes positions."""
    global _cal
    now      = datetime.now(timezone.utc)
    state    = load_state()
    balance  = state["balance"]
    new_pos  = 0
    closed   = 0
    resolved = 0

    for city_slug, loc in LOCATIONS.items():
        unit = loc["unit"]
        unit_sym = "F" if unit == "F" else "C"
        print(f"  -> {loc['name']}...", end=" ", flush=True)

        try:
            dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(0.3)
        except Exception as e:
            print(f"skipped ({e})")
            continue

        for i, date in enumerate(dates):
            dt      = datetime.strptime(date, "%Y-%m-%d")
            horizon = f"D+{i}"

            for market_type in MARKET_TYPES:
                event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year, market_type)
                if not event:
                    continue

                end_date = event.get("endDate", "")
                hours    = hours_to_resolution(end_date) if end_date else 0

                # Load or create market record
                mkt = load_market(city_slug, date, market_type)
                if mkt is None:
                    if hours < MIN_HOURS or hours > MAX_HOURS:
                        continue
                    mkt = new_market(city_slug, date, event, hours, market_type)

                # Skip if market already resolved
                if mkt["status"] == "resolved":
                    continue

                # Update outcomes list — prices taken directly from event
                outcomes = []
                for market in event.get("markets", []):
                    question = market.get("question", "")
                    mid      = str(market.get("id", ""))
                    volume   = float(market.get("volume", 0))
                    rng      = parse_temp_range(question)
                    if not rng:
                        continue
                    try:
                        prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                        bid = float(prices[0])
                        ask = float(prices[1]) if len(prices) > 1 else bid
                    except Exception:
                        continue
                    outcomes.append({
                        "question":  question,
                        "market_id": mid,
                        "range":     rng,
                        "bid":       round(bid, 4),
                        "ask":       round(ask, 4),
                        "price":     round(bid, 4),   # for compatibility
                        "spread":    round(ask - bid, 4),
                        "volume":    round(volume, 0),
                    })

                outcomes.sort(key=lambda x: x["range"][0])
                mkt["all_outcomes"] = outcomes

                snap = snapshots.get(date, {}).get(market_type, {})
                forecast_snap = {
                    "ts":          snap.get("ts"),
                    "horizon":     horizon,
                    "hours_left":  round(hours, 1),
                    "ecmwf":       snap.get("ecmwf"),
                    "hrrr":        snap.get("hrrr"),
                    "metar":       snap.get("metar"),
                    "best":        snap.get("best"),
                    "best_source": snap.get("best_source"),
                }
                mkt["forecast_snapshots"].append(forecast_snap)

                # Market price snapshot
                top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
                market_snap = {
                    "ts":         snap.get("ts"),
                    "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                    "top_price":  top["price"] if top else None,
                }
                mkt["market_snapshots"].append(market_snap)

                forecast_temp = snap.get("best")
                best_source   = snap.get("best_source")

                # --- STOP-LOSS AND TRAILING STOP ---
                if mkt.get("position") and mkt["position"].get("status") == "open":
                    pos = mkt["position"]
                    current_price = None
                    for o in outcomes:
                        if o["market_id"] == pos["market_id"]:
                            current_price = o["price"]
                            break

                    if current_price is not None:
                        current_price = o.get("bid", current_price)  # sell at bid
                        entry = pos["entry_price"]
                        stop  = pos.get("stop_price", entry * 0.80)  # 20% stop by default

                        # Progressive trailing stop
                        if current_price >= entry * 1.20:
                            if not pos.get("trailing_activated"):
                                new_stop = entry  # first activation: breakeven
                                pos["trailing_activated"] = True
                            else:
                                new_stop = round(current_price * 0.80, 4)  # 80% of current
                            if new_stop > stop:
                                pos["stop_price"] = new_stop

                        # Check stop
                        if current_price <= stop:
                            pnl = round((current_price - entry) * pos["shares"], 2)
                            balance += pos["cost"] + pnl
                            pos["closed_at"]    = snap.get("ts")
                            pos["close_reason"] = "stop_loss" if current_price < entry else "trailing_stop"
                            pos["exit_price"]   = current_price
                            pos["pnl"]          = pnl
                            pos["status"]       = "closed"
                            closed += 1
                            reason = "STOP" if current_price < entry else "TRAILING BE"
                            print(f"  [{reason}] {loc['name']} {date} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

                # --- CLOSE POSITION if forecast shifted 2+ degrees ---
                if mkt.get("position") and forecast_temp is not None:
                    pos = mkt["position"]
                    old_bucket_low  = pos["bucket_low"]
                    old_bucket_high = pos["bucket_high"]
                    buffer = 2.0 if unit == "F" else 1.0
                    mid_bucket = (old_bucket_low + old_bucket_high) / 2 if old_bucket_low != -999 and old_bucket_high != 999 else forecast_temp
                    forecast_far = abs(forecast_temp - mid_bucket) > (abs(mid_bucket - old_bucket_low) + buffer)
                    if not in_bucket(forecast_temp, old_bucket_low, old_bucket_high) and forecast_far:
                        current_price = None
                        for o in outcomes:
                            if o["market_id"] == pos["market_id"]:
                                current_price = o["price"]
                                break
                        if current_price is not None:
                            pnl = round((current_price - pos["entry_price"]) * pos["shares"], 2)
                            balance += pos["cost"] + pnl
                            mkt["position"]["closed_at"]    = snap.get("ts")
                            mkt["position"]["close_reason"] = "forecast_changed"
                            mkt["position"]["exit_price"]   = current_price
                            mkt["position"]["pnl"]          = pnl
                            mkt["position"]["status"]       = "closed"
                            closed += 1
                            print(f"  [CLOSE] {loc['name']} {date} [{market_type}] — forecast changed | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

                # --- OPEN POSITION ---
                if not mkt.get("position") and forecast_temp is not None and hours >= MIN_HOURS:
                    sigma = get_sigma(city_slug, best_source or "ecmwf", market_type)
                    best_signal = None

                    matched_bucket = None
                    for o in outcomes:
                        t_low, t_high = o["range"]
                        if in_bucket(forecast_temp, t_low, t_high):
                            matched_bucket = o
                            break

                    if matched_bucket:
                        o = matched_bucket
                        t_low, t_high = o["range"]

                        # Cross-market consistency check
                        other_type = "lowest" if market_type == "highest" else "highest"
                        other_mkt  = load_market(city_slug, date, other_type)
                        if other_mkt and other_mkt.get("position"):
                            other_pos = other_mkt["position"]
                            if market_type == "highest":
                                consistent = _buckets_are_consistent(t_low, t_high,
                                                                      other_pos["bucket_low"],
                                                                      other_pos["bucket_high"])
                            else:
                                consistent = _buckets_are_consistent(other_pos["bucket_low"],
                                                                      other_pos["bucket_high"],
                                                                      t_low, t_high)
                            if not consistent:
                                print(f"  [SKIP] {loc['name']} {date} [{market_type}]: "
                                      f"bucket {t_low}-{t_high} inconsistent with open "
                                      f"{other_type} {other_pos['bucket_low']}-{other_pos['bucket_high']}")
                                matched_bucket = None

                    if matched_bucket:
                        o = matched_bucket
                        t_low, t_high = o["range"]
                        volume = o["volume"]
                        bid    = o.get("bid", o["price"])
                        ask    = o.get("ask", o["price"])
                        spread = o.get("spread", 0)

                        if volume >= MIN_VOLUME:
                            p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
                            ev = calc_ev(p, ask)
                            effective_min_ev = MIN_EV * max(1.0, sigma / SIGMA_REF) if STRAT_DYNAMIC_EV else MIN_EV
                            if ev >= effective_min_ev:
                                kelly = calc_kelly(p, ask)
                                size  = bet_size(kelly, balance, horizon_days=i)
                                if size >= 0.50:
                                    best_signal = {
                                        "market_id":    o["market_id"],
                                        "question":     o["question"],
                                        "bucket_low":   t_low,
                                        "bucket_high":  t_high,
                                        "entry_price":  ask,
                                        "bid_at_entry": bid,
                                        "spread":       spread,
                                        "shares":       round(size / ask, 2),
                                        "cost":         size,
                                        "p":            round(p, 4),
                                        "ev":           round(ev, 4),
                                        "kelly":        round(kelly, 4),
                                        "forecast_temp":forecast_temp,
                                        "forecast_src": best_source,
                                        "sigma":        sigma,
                                        "opened_at":    snap.get("ts"),
                                        "status":       "open",
                                        "pnl":          None,
                                        "exit_price":   None,
                                        "close_reason": None,
                                        "closed_at":    None,
                                    }

                    if best_signal:
                        # Fetch real bestAsk from Polymarket API for accurate entry price
                        skip_position = False
                        try:
                            r = requests.get(f"https://gamma-api.polymarket.com/markets/{best_signal['market_id']}", timeout=(3, 5))
                            mdata = r.json()
                            real_ask = float(mdata.get("bestAsk", best_signal["entry_price"]))
                            real_bid = float(mdata.get("bestBid", best_signal["bid_at_entry"]))
                            real_spread = round(real_ask - real_bid, 4)
                            if real_spread > MAX_SLIPPAGE or real_ask >= MAX_PRICE:
                                print(f"  [SKIP] {loc['name']} {date} [{market_type}] — real ask ${real_ask:.3f} spread ${real_spread:.3f}")
                                skip_position = True
                            else:
                                best_signal["entry_price"]  = real_ask
                                best_signal["bid_at_entry"] = real_bid
                                best_signal["spread"]       = real_spread
                                best_signal["shares"]       = round(best_signal["cost"] / real_ask, 2)
                                best_signal["ev"]           = round(calc_ev(best_signal["p"], real_ask), 4)
                        except Exception as e:
                            print(f"  [WARN] Could not fetch real ask for {best_signal['market_id']}: {e}")

                        if not skip_position and best_signal["entry_price"] < MAX_PRICE:
                            balance -= best_signal["cost"]
                            mkt["position"] = best_signal
                            state["total_trades"] += 1
                            new_pos += 1
                            tag          = "[HI]" if market_type == "highest" else "[LO]"
                            bucket_label = f"{best_signal['bucket_low']}-{best_signal['bucket_high']}{unit_sym}"
                            print(f"  [BUY]  {tag} {loc['name']} {horizon} {date} | {bucket_label} | "
                                  f"${best_signal['entry_price']:.3f} | EV {best_signal['ev']:+.2f} | "
                                  f"${best_signal['cost']:.2f} ({best_signal['forecast_src'].upper()})")

                # Market closed by time
                if hours < 0.5 and mkt["status"] == "open":
                    mkt["status"] = "closed"

                save_market(mkt)
                time.sleep(0.1)

        print("ok")

    # --- AUTO-RESOLUTION ---
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue

        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue

        market_id = pos.get("market_id")
        if not market_id:
            continue

        # Check if market closed on Polymarket
        won = check_market_resolved(market_id)
        if won is None:
            continue  # market still open

        # Market closed — record result
        price  = pos["entry_price"]
        size   = pos["cost"]
        shares = pos["shares"]
        pnl    = round(shares * (1 - price), 2) if won else round(-size, 2)

        balance += size + pnl
        pos["exit_price"]   = 1.0 if won else 0.0
        pos["pnl"]          = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"]    = now.isoformat()
        pos["status"]       = "closed"
        mkt["pnl"]          = pnl
        mkt["status"]       = "resolved"
        mkt["resolved_outcome"] = "win" if won else "loss"
        mkt["actual_temp"]  = get_actual_temp(mkt["city"], mkt["date"], mkt.get("type", "highest"))

        if won:
            state["wins"] += 1
        else:
            state["losses"] += 1

        result = "WIN" if won else "LOSS"
        tag = "[HI]" if mkt.get("type", "highest") == "highest" else "[LO]"
        print(f"  [{result}] {tag} {mkt['city_name']} {mkt['date']} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
        resolved += 1

        save_market(mkt)
        time.sleep(0.3)

    balance = calculate_balance_from_trades()
    state["balance"]      = balance
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)
    save_state(state)

    # Run calibration if enough data collected
    all_mkts = load_all_markets()
    resolved_count = len([m for m in all_mkts if m["status"] == "resolved"])
    if resolved_count >= CALIBRATION_MIN:
        global _cal
        _cal = run_calibration(all_mkts)

    return new_pos, closed, resolved

# =============================================================================
# REPORT
# =============================================================================

def print_status():
    state    = load_state()
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal     = calculate_balance_from_trades()
    start   = state["starting_balance"]
    ret_pct = (bal - start) / start * 100
    wins    = state["wins"]
    losses  = state["losses"]
    total   = wins + losses

    print(f"\n{'='*55}")
    print(f"  WEATHERBOT — STATUS")
    print(f"{'='*55}")
    print(f"  Balance:     ${bal:,.2f}  (start ${start:,.2f}, {'+'if ret_pct>=0 else ''}{ret_pct:.1f}%)")
    print(f"  Trades:      {total} | W: {wins} | L: {losses} | WR: {wins/total:.0%}" if total else "  No trades yet")
    print(f"  Open:        {len(open_pos)}")
    print(f"  Resolved:    {len(resolved)}")

    if open_pos:
        print(f"\n  Open positions:")
        total_unrealized = 0.0
        for m in open_pos:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"

            # Current price from latest market snapshot
            current_price = pos["entry_price"]
            snaps = m.get("market_snapshots", [])
            if snaps:
                # Find our bucket price in all_outcomes
                for o in m.get("all_outcomes", []):
                    if o["market_id"] == pos["market_id"]:
                        current_price = o["price"]
                        break

            unrealized = round((current_price - pos["entry_price"]) * pos["shares"], 2)
            total_unrealized += unrealized
            pnl_str = f"{'+'if unrealized>=0 else ''}{unrealized:.2f}"
            tag = "[HI]" if m.get("type", "highest") == "highest" else "[LO]"

            print(f"    {tag} {m['city_name']:<14} {m['date']} | {label:<14} | "
                  f"entry ${pos['entry_price']:.3f} -> ${current_price:.3f} | "
                  f"PnL: {pnl_str} | {pos['forecast_src'].upper()}")

        sign = "+" if total_unrealized >= 0 else ""
        print(f"\n  Unrealized PnL: {sign}{total_unrealized:.2f}")

    print(f"{'='*55}\n")

def print_report():
    markets  = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    print(f"\n{'='*55}")
    print(f"  WEATHERBOT — FULL REPORT")
    print(f"{'='*55}")

    if not resolved:
        print("  No resolved markets yet.")
        return

    total_pnl = sum(m["pnl"] for m in resolved)
    wins      = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses    = [m for m in resolved if m["resolved_outcome"] == "loss"]

    print(f"\n  Total resolved: {len(resolved)}")
    print(f"  Wins:           {len(wins)} | Losses: {len(losses)}")
    print(f"  Win rate:       {len(wins)/len(resolved):.0%}")
    print(f"  Total PnL:      {'+'if total_pnl>=0 else ''}{total_pnl:.2f}")

    print(f"\n  By city:")
    for city in sorted(set(m["city"] for m in resolved)):
        group = [m for m in resolved if m["city"] == city]
        w     = len([m for m in group if m["resolved_outcome"] == "win"])
        pnl   = sum(m["pnl"] for m in group)
        name  = LOCATIONS[city]["name"]
        print(f"    {name:<16} {w}/{len(group)} ({w/len(group):.0%})  PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

    print(f"\n  Market details:")
    for m in sorted(resolved, key=lambda x: (x["date"], x.get("type", "highest"))):
        pos      = m.get("position", {})
        unit_sym = "F" if m["unit"] == "F" else "C"
        snaps    = m.get("forecast_snapshots", [])
        first_fc = snaps[0]["best"] if snaps else None
        last_fc  = snaps[-1]["best"] if snaps else None
        label    = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}" if pos else "no position"
        result   = m["resolved_outcome"].upper()
        pnl_str  = f"{'+'if m['pnl']>=0 else ''}{m['pnl']:.2f}" if m["pnl"] is not None else "-"
        fc_str   = f"forecast {first_fc}->{last_fc}{unit_sym}" if first_fc else "no forecast"
        actual   = f"actual {m['actual_temp']}{unit_sym}" if m["actual_temp"] else ""
        tag      = "[HI]" if m.get("type", "highest") == "highest" else "[LO]"
        print(f"    {tag} {m['city_name']:<14} {m['date']} | {label:<14} | {fc_str} | {actual} | {result} {pnl_str}")

    print(f"{'='*55}\n")

# =============================================================================
# MAIN LOOP
# =============================================================================

MONITOR_INTERVAL = 600  # monitor positions every 10 minutes

def monitor_positions():
    """Quick stop check on open positions without full scan."""
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        return 0

    state   = load_state()
    balance = state["balance"]
    closed  = 0

    for mkt in open_pos:
        pos = mkt["position"]
        mid = pos["market_id"]

        # Fetch real bestBid from Polymarket API — actual sell price
        current_price = None
        try:
            r = requests.get(f"https://gamma-api.polymarket.com/markets/{mid}", timeout=(3, 5))
            mdata = r.json()
            best_bid = mdata.get("bestBid")
            if best_bid is not None:
                current_price = float(best_bid)
        except Exception:
            pass

        # Fallback to cached price if API failed
        if current_price is None:
            for o in mkt.get("all_outcomes", []):
                if o["market_id"] == mid:
                    current_price = o.get("bid", o["price"])
                    break

        if current_price is None:
            continue

        entry = pos["entry_price"]
        stop  = pos.get("stop_price", entry * 0.80)
        city_name = LOCATIONS.get(mkt["city"], {}).get("name", mkt["city"])

        # Hours left to resolution
        end_date = mkt.get("event_end_date", "")
        hours_left = hours_to_resolution(end_date) if end_date else 999.0

        # Take-profit threshold based on hours to resolution
        if hours_left < 24:
            take_profit = None        # hold to resolution
        elif hours_left < 48:
            take_profit = 0.85        # 24-48h: take profit at $0.85
        else:
            take_profit = 0.75        # 48h+: take profit at $0.75

        # Progressive trailing stop
        if current_price >= entry * 1.20:
            if not pos.get("trailing_activated"):
                new_stop = entry  # first activation: breakeven
                pos["trailing_activated"] = True
            else:
                new_stop = round(current_price * 0.80, 4)  # 80% of current
            if new_stop > stop:
                pos["stop_price"] = new_stop
                print(f"  [TRAILING] {city_name} {mkt['date']} — stop moved to ${new_stop:.3f}")

        # Check take-profit
        take_triggered = take_profit is not None and current_price >= take_profit
        # Check stop
        stop_triggered = current_price <= stop

        if take_triggered or stop_triggered:
            pnl = round((current_price - entry) * pos["shares"], 2)
            balance += pos["cost"] + pnl
            pos["closed_at"]    = datetime.now(timezone.utc).isoformat()
            if take_triggered:
                pos["close_reason"] = "take_profit"
                reason = "TAKE"
            elif current_price < entry:
                pos["close_reason"] = "stop_loss"
                reason = "STOP"
            else:
                pos["close_reason"] = "trailing_stop"
                reason = "TRAILING BE"
            pos["exit_price"]   = current_price
            pos["pnl"]          = pnl
            pos["status"]       = "closed"
            closed += 1
            print(f"  [{reason}] {city_name} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | {hours_left:.0f}h left | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
            save_market(mkt)

    if closed:
        balance = calculate_balance_from_trades()
        state["balance"] = balance
        state["peak_balance"] = max(state.get("peak_balance", balance), balance)
        save_state(state)

    return closed


def run_loop():
    global _cal
    _normalize_legacy_filenames()
    _cal = load_cal()

    print(f"\n{'='*55}")
    print(f"  WEATHERBOT — STARTING")
    print(f"{'='*55}")
    print(f"  Cities:     {len(LOCATIONS)}")
    print(f"  Balance:    ${BALANCE:,.0f} | Max bet: ${MAX_BET}")
    print(f"  Scan:       {SCAN_INTERVAL//60} min | Monitor: {MONITOR_INTERVAL//60} min")
    print(f"  Sources:    ECMWF + HRRR(US) + METAR(D+0)")
    print(f"  Data:       {DATA_DIR.resolve()}")
    print(f"  Ctrl+C to stop\n")

    last_full_scan = 0

    while True:
        now_ts  = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Full scan once per hour
        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"[{now_str}] full scan...")
            try:
                new_pos, closed, resolved = scan_and_update()
                state = load_state()
                print(f"  balance: ${state['balance']:,.2f} | "
                      f"new: {new_pos} | closed: {closed} | resolved: {resolved}")
                last_full_scan = time.time()
            except KeyboardInterrupt:
                print(f"\n  Stopping — saving state...")
                save_state(load_state())
                print(f"  Done. Bye!")
                break
            except requests.exceptions.ConnectionError:
                print(f"  Connection lost — waiting 60 sec")
                time.sleep(60)
                continue
            except Exception as e:
                print(f"  Error: {e} — waiting 60 sec")
                time.sleep(60)
                continue
        else:
            # Quick stop monitoring
            print(f"[{now_str}] monitoring positions...")
            try:
                stopped = monitor_positions()
                if stopped:
                    state = load_state()
                    print(f"  balance: ${state['balance']:,.2f}")
            except Exception as e:
                print(f"  Monitor error: {e}")

        try:
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n  Stopping — saving state...")
            save_state(load_state())
            print(f"  Done. Bye!")
            break

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_loop()
    elif cmd == "status":
        _cal = load_cal()
        print_status()
    elif cmd == "report":
        _cal = load_cal()
        print_report()
    else:
        print("Usage: python weatherbot.py [run|status|report]")
