# Technical Spec: Lowest-Temperature Market Support

**Branch:** `feature/lowest-temp-markets`
**Status:** Approved — ready for implementation
**Version:** 1.1 — 2026-04-26

---

## 1. Problem Statement

Polymarket runs two parallel market families for every supported city/date:

- **Highest temperature** — `highest-temperature-in-{city}-on-{month}-{day}-{year}`
- **Lowest temperature** — `lowest-temperature-in-{city}-on-{month}-{day}-{year}`

Both families resolve against the same airport station, use the same bucket grammar (`X°F or below`, `between X-Y°F`, etc.), and follow the same lifecycle. The bot currently only queries the highest-temperature slug — `get_polymarket_event()` hardcodes the prefix at line 307 of `weatherbot.py`. Lowest-temperature markets are silently ignored, halving the available trade universe.

This is not a design decision; it is an artifact of how the slug was originally written. The full forecasting and trading machinery already supports the concept of a daily extreme — only the slug, the forecast field name (`temperature_2m_max` vs `temperature_2m_min`), the METAR aggregation, and the market file naming need to fork on type.

**Goal:** Track and trade lowest-temperature markets alongside the existing highest-temperature ones, with no regression to the current code path.

---

## 2. Scope

### In scope (this spec)

- Track Polymarket lowest-temperature events in addition to highest-temperature events
- Fetch `temperature_2m_min` from ECMWF and HRRR/GFS in parallel with `temperature_2m_max`
- Route the correct forecast value to each market based on its type
- Distinguish highest vs lowest in the on-disk market file format and filename
- Aggregate METAR observations into a rolling minimum over the resolution window for D+0 lowest markets (an analogue to the daily-max signal already used for highest markets)
- Update calibration so sigma is tracked separately per `(city, source, market_type)` tuple — forecast error for daily min and daily max are not interchangeable
- Update reporting (`print_status`, `print_report`) to display the market type alongside the bucket
- Default Visual Crossing element from `tempmax` to `tempmin` for lowest markets

### Out of scope (deferred)

- Changes to entry filters, EV math, Kelly sizing, stop-loss, trailing stop, take-profit, forecast-change close — all logic operates on a single forecast number and a bucket; the only change is which forecast number is fed in
- Dashboard styling and layout changes beyond adding `[HI]`/`[LO]` badges — the badge is in scope (see §4.4)
- Strategy flag interactions — `prob_model_normal_cdf`, `time_decay`, `dynamic_min_ev` apply unchanged to both market types
- Cross-market hedging (buying the same bucket on both highest and lowest) — out of scope
- Visual Crossing batched calls — one call per `(city, date, type)` is acceptable; both types resolve on the same date so the daily call returns both `tempmax` and `tempmin` in one response, but the optimization is deferred
- A migration of pre-existing market files from `{city}_{date}.json` to the new typed naming — pre-existing files are highest-temperature by definition; on first read they are normalized in place (see §4.3)

---

## 3. Design Principles

### 3.1 Type is data, not a flag

The market type ("highest" or "lowest") is recorded once on the market dict at creation time and threaded through everywhere it is needed:

```python
mkt["type"] = "highest" | "lowest"
```

There is no global "lowest mode" toggle. A single `scan_and_update()` cycle handles both types for every city/date by iterating over both slugs.

### 3.2 One forecast fetch returns both extremes

ECMWF and HRRR are queried with `daily=temperature_2m_max,temperature_2m_min` in a single request per city. This keeps the API call count constant — adding a feature does not double API load.

### 3.3 Snapshot stores both, position consumes one

Forecast snapshots store both `ecmwf_max`/`ecmwf_min`, `hrrr_max`/`hrrr_min`, and a single `best` field that is type-resolved at write time using the parent market's `type`. The dashboard, calibration, and trading logic only read `best`; they do not branch on type.

### 3.4 Pre-existing market files keep working

The current naming `data/markets/{city}_{date}.json` always referred to highest. The new naming includes a type segment: `data/markets/{city}_{date}_{type}.json`. On startup, any file lacking the `_highest` / `_lowest` suffix is treated as highest and renamed in place (one-time normalization — see §4.3).

---

## 4. Architecture Changes

### 4.1 LOCATIONS and TIMEZONES — unchanged

Same 20 cities, same stations. Both market types resolve against the same ICAO. No new constants.

### 4.2 New module-level constant

```python
MARKET_TYPES = ("highest", "lowest")
```

Used in the city/date loop in `scan_and_update()` and in path normalization.

### 4.3 Market file naming

| Old | New |
|---|---|
| `data/markets/nyc_2026-04-23.json` | `data/markets/nyc_2026-04-23_highest.json` |
| _(none)_ | `data/markets/nyc_2026-04-23_lowest.json` |

`market_path(city_slug, date_str, market_type)` adds a third positional argument. Callers updated:

- `load_market(city_slug, date_str, market_type)`
- `save_market(market)` — derives type from `market["type"]`, no signature change
- `load_all_markets()` — no signature change; recursively loads all `*.json` and uses the `type` field on each dict (filename is just storage)

**One-time normalization** runs in `load_all_markets()` once per process boot:

```python
def _normalize_legacy_filenames():
    for f in MARKETS_DIR.glob("*.json"):
        # New format always has 3 underscore-separated segments before .json
        parts = f.stem.rsplit("_", 1)
        if parts[-1] in MARKET_TYPES:
            continue   # already typed
        # Legacy: rename to *_highest.json and inject "type": "highest" into the JSON
        new_path = f.with_name(f.stem + "_highest.json")
        if new_path.exists():
            continue   # somehow both exist — leave alone, log warning
        data = json.loads(f.read_text(encoding="utf-8"))
        data.setdefault("type", "highest")
        new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        f.unlink()
```

The function is idempotent and safe to call on every boot.

**Log levels for migration and skip events:**

| Event | Level | Example |
|---|---|---|
| Legacy file renamed | `INFO` | `[MIGRATE] nyc_2026-04-23.json → nyc_2026-04-23_highest.json` |
| Both old and new file exist (conflict) | `WARN` | `[WARN] Skipping rename: nyc_2026-04-23_highest.json already exists` |
| Forecast `max < min` (data anomaly) | `WARN` | `[WARN] nyc 2026-04-28: max (61) < min (63), skipping both market types` |
| Bucket consistency guard fires | `INFO` | `[SKIP] nyc 2026-04-28 highest: bucket 58-62 inconsistent with open lowest position 63-65` |
| Calibration key migration | `INFO` | `[MIGRATE] cal: nyc_ecmwf → nyc_ecmwf_highest (2 keys updated)` |

`WARN` lines always indicate something unexpected that the operator should be aware of. `INFO` lines are routine operational events. Both are written to `stdout` using the existing `print()` convention in `weatherbot.py` (no logging module required).

### 4.4 Dashboard — `[HI]` / `[LO]` badges

The market type must be visible everywhere the dashboard surfaces position or forecast data. Without it, two rows for the same city and date (one per type) are indistinguishable.

#### `dashboard.py` — thread `market_type` into payloads

Add `"market_type": m.get("type", "highest")` to every entry built in `build_dashboard_data`:

- `open_positions` dict (line ~240)
- `closed_positions` dict (line ~257)
- `forecasts` dict (line ~276)
- `detect_changes` BUY / EXIT / FORECAST messages — prefix with `[HI]` or `[LO]`:

```python
mtype = new_data.get("type", "highest")
tag   = "[HI]" if mtype == "highest" else "[LO]"

# BUY event:
"msg": f"BUY {tag} {city} ${cost:.0f} @ {entry:.3f} bucket {bucket} (EV +{ev:.2f})"

# EXIT event:
"msg": f"EXIT {tag} {city} {reason} @ {exit_price:.3f} ({sign}${pnl:.2f})"

# FORECAST event:
"msg": f"FORECAST {tag} {city} {source} {best}°"
```

#### `dashboard.js` — render badges

**Open positions table** (`updatePositions`, line ~253):

```javascript
const tag = p.market_type === "lowest"
    ? '<span class="badge badge-lo">LO</span>'
    : '<span class="badge badge-hi">HI</span>';

// Insert tag next to city name:
`<span class="city-name">${tag} ${p.city_name}</span>`
```

**Trade history table** (`updateHistory`, line ~326): same badge pattern next to city name.

**Map popup** (`buildPopupHtml`, line ~68): prepend tag to the position section header:

```javascript
html += `<div class="label">${tag} Position</div>`;
```

**World map city card** (`buildMarkerHtml` / `updateWorldMap`, line ~28 and ~195): when a city has both an open HI and an open LO position, show the one with higher EV in the marker icon and list both in the card detail:

```javascript
// card detail line (updateWorldMap ~line 204):
const hiPos = positions.find(p => p.city === key && p.market_type === "highest");
const loPos = positions.find(p => p.city === key && p.market_type === "lowest");
const bucket = hiPos
    ? `[HI] ${hiPos.bucket_low}-${hiPos.bucket_high}` + (loPos ? ` / [LO] ${loPos.bucket_low}-${loPos.bucket_high}` : "")
    : loPos ? `[LO] ${loPos.bucket_low}-${loPos.bucket_high}` : "—";
```

Note: `positions.find(p => p.city === city)` in `buildMarkerHtml` / `buildPopupHtml` currently returns only the first match. Since a city can now have two open positions (one per type), these calls must be updated to `positions.filter(p => p.city === city)` and the popup rendered for both.

**UI corner cases to verify:**

| Case | Expected behaviour |
|---|---|
| City has no open positions of either type | Marker icon shows forecast only; no position section in popup; city card shows `—` for bucket |
| City has only `[HI]` position | Only one badge shown; no `[LO]` row; no empty placeholder |
| City has only `[LO]` position | Symmetric to above |
| City has both `[HI]` and `[LO]` positions | Both rows shown in positions table; popup renders two position sections; city card shows both bucket ranges on separate lines |
| `market_type` field absent (pre-feature data) | Default to `"highest"`; render `[HI]` badge; do not crash |
| `open_positions` empty for all cities | "No open positions" empty state unchanged; no badge-related JS errors |
| Positions table very wide (long bucket ranges + two badges) | Verify no horizontal overflow on 1280px viewport |

#### `style.css` — badge styles

```css
.badge-hi {
    background: rgba(59, 130, 246, 0.25);   /* blue */
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.4);
    font-size: 0.65rem;
    padding: 1px 4px;
    border-radius: 3px;
    font-weight: 700;
    letter-spacing: 0.04em;
    vertical-align: middle;
}
.badge-lo {
    background: rgba(249, 115, 22, 0.25);   /* orange */
    color: #fdba74;
    border: 1px solid rgba(249, 115, 22, 0.4);
    font-size: 0.65rem;
    padding: 1px 4px;
    border-radius: 3px;
    font-weight: 700;
    letter-spacing: 0.04em;
    vertical-align: middle;
}
```

---

## 5. Forecast Source Changes

### 5.1 `get_ecmwf` and `get_hrrr`

Both functions currently request `daily=temperature_2m_max`. They are extended to request both extremes in one call and return a dict keyed by date with both values:

```python
def get_ecmwf(city_slug, dates):
    ...
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    ...
    daily = data["daily"]
    for date, t_max, t_min in zip(daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"]):
        if date in dates:
            result[date] = {
                "max": round(t_max, 1) if unit == "C" else round(t_max) if t_max is not None else None,
                "min": round(t_min, 1) if unit == "C" else round(t_min) if t_min is not None else None,
            }
    return result
```

The return shape changes from `{date: float}` to `{date: {"max": float, "min": float}}`. All callers must be updated — only `take_forecast_snapshot()` calls these functions, so the blast radius is small.

`get_hrrr` is extended identically. Beyond-48h pruning is unchanged.

### 5.2 `take_forecast_snapshot`

Currently returns one snapshot per date with a single `best`. Extended to produce two snapshots per date — one per market type:

```python
snapshots = {date: {"highest": {...}, "lowest": {...}} for date in dates}
```

Each per-type snapshot has the same shape as today: `ts`, `ecmwf`, `hrrr`, `metar`, `best`, `best_source`. The selection rule is unchanged (HRRR for US D+0/D+1 if available, else ECMWF) — applied independently to max and min.

`metar` is type-aware:
- For `"highest"` snapshots — `get_metar(city_slug)` returns the current observed temperature (unchanged behavior; it's a reference-only field for highest markets too).
- For `"lowest"` snapshots — see §7 for the rolling-min logic.

### 5.3 Forecast snapshot record (per market file)

The `forecast_snapshots` list inside a market file stores per-snapshot rows — one row per scan cycle, scoped to that market's type. The schema gains no new keys; only the values reflect the right extreme:

```jsonc
{
  "ts":          "2026-04-26T12:00:00+00:00",
  "horizon":     "D+1",
  "hours_left":  29.4,
  "ecmwf":       8.2,            // min for lowest market, max for highest
  "hrrr":        null,           // non-US
  "metar":       9.1,            // rolling min over window for lowest, current obs for highest
  "best":        8.2,
  "best_source": "ecmwf"
}
```

Calibration sees the right error simply by reading `best` against the right `actual_temp` (see §8).

---

## 6. Slug / Market Detection Changes

### 6.1 `get_polymarket_event` signature

```python
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
```

Default `"highest"` preserves any existing call sites that have not been updated yet (defensive — there should be none after this change lands).

### 6.2 `scan_and_update` city/date loop

The inner loop gains an extra dimension:

```python
for city_slug, loc in LOCATIONS.items():
    ...
    snapshots = take_forecast_snapshot(city_slug, dates)  # now returns per-type snapshots
    for i, date in enumerate(dates):
        for market_type in MARKET_TYPES:
            event = get_polymarket_event(city_slug, MONTHS[dt.month-1], dt.day, dt.year, market_type)
            if not event:
                continue   # this city/date may have only one of the two types — see §9
            mkt = load_market(city_slug, date, market_type)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours, market_type)
            ...
            snap = snapshots[date][market_type]
            ...
```

This doubles the Polymarket event API calls per scan (from 80 to 160 for 20 cities × 4 days × 2 types). Open-Meteo calls are unchanged because §5.1 fetches both extremes in one request. Per-city `time.sleep(0.3)` after `take_forecast_snapshot()` is unchanged; an additional `time.sleep(0.1)` between the two market-type lookups in the inner loop keeps the Gamma API call rate consistent with today.

### 6.3 Bucket parsing

`parse_temp_range()` is **unchanged**. The grammar (`X°F or below`, `between X-Y°F`, `X°F or higher`) is identical for both market types. The semantics differ — for a lowest-temperature market, "75°F or higher" means the daily low was 75°F+ (rare), while "or below" is the populated end of the distribution — but the parser only extracts the numeric range; the forecast-vs-bucket comparison is identical.

### 6.4 Resolution station

Polymarket includes a `resolutionSource` Wunderground URL on both the highest and lowest events for the same city/date. Both URLs encode the same ICAO. `parse_resolution_station()` and the dynamic LOCATIONS update logic in `new_market()` work without changes.

---

## 7. METAR — Rolling Minimum

### 7.1 The problem

METAR is a point-in-time observation, not a daily aggregate. For highest-temperature markets, the bot uses METAR only as a reference reading on D+0 (it is not the trading signal — `best` defaults to ECMWF/HRRR daily max). For lowest-temperature markets the analogous problem is harder: the daily minimum typically occurs around sunrise, which may have already passed (METAR is below the eventual day's min) or not yet occurred (METAR is well above it).

### 7.2 Solution: rolling minimum from METAR history

For D+0 lowest markets, `get_metar_min(city_slug, date_str)` queries METAR history for the resolution window and returns the lowest observed temperature so far that day in the station's local timezone:

```python
def get_metar_min(city_slug, date_str):
    """Lowest METAR-observed temperature over the local-day window
    [00:00, now] in the station's timezone. Returns None if no obs."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    tz = TIMEZONES.get(city_slug, "UTC")
    # Window: from 00:00 local on date_str to now
    # Aviation Weather supports `hours` parameter (max 36)
    url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json&hours=24"
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        if not isinstance(data, list) or not data:
            return None
        # Filter to observations whose local date == date_str
        local_today_obs = [
            d for d in data
            if _metar_local_date(d, tz) == date_str and d.get("temp") is not None
        ]
        if not local_today_obs:
            return None
        min_c = min(float(d["temp"]) for d in local_today_obs)
        return round(min_c * 9/5 + 32) if loc["unit"] == "F" else round(min_c, 1)
    except Exception as e:
        print(f"  [METAR-MIN] {city_slug}: {e}")
    return None
```

Helper `_metar_local_date(obs, tz)` parses the obs's `obsTime` (epoch seconds) and converts to a local date string using `zoneinfo.ZoneInfo(tz)`.

`get_metar(city_slug)` is preserved unchanged for highest markets and as a reference reading.

### 7.3 When rolling min is used

- **D+0 lowest market:** `take_forecast_snapshot()` populates `snap["metar"]` with `get_metar_min(city, today)`.
- **D+1/D+2/D+3 lowest market:** `snap["metar"] = None` (no observations exist for future dates).
- **Highest markets, all horizons:** unchanged — `get_metar(city_slug)` for D+0, `None` otherwise.

### 7.4 Reference-only, not a primary signal

Like METAR for highest markets, `metar` for lowest markets is recorded for transparency and downstream analysis but does **not** override `best` (which is always ECMWF or HRRR daily-min for lowest markets). This preserves the principle that the model-based forecast drives positions, while the observation provides a sanity check during the resolution window.

### 7.5 Edge cases

- A scan that fires before midnight UTC but after midnight local time (e.g., a 23:30 UTC scan in Tokyo) needs to consider the **local** `date_str`, not UTC. `_metar_local_date()` uses the city's local timezone — same data the forecast APIs already use.
- If the bot starts mid-day, `hours=24` covers the full preceding day plus elapsed today. A late-day station with sparse METAR coverage may still have only 4-6 observations for the day; that is acceptable for a reference reading.
- Aviation Weather's `hours` parameter caps at 36. `hours=24` is sufficient given lowest-temperature markets resolve once per local day.

---

## 8. Calibration Changes

### 8.1 Sigma is now per `(city, source, market_type)`

The current calibration key is `f"{city}_{source}"` (e.g., `nyc_ecmwf`). It becomes `f"{city}_{source}_{market_type}"` (e.g., `nyc_ecmwf_highest`, `nyc_ecmwf_lowest`).

Daily-min and daily-max forecast errors are not statistically equivalent — the diurnal cycle, model bias, and even airport siting affect them differently. Pooling them would corrupt sigma in both directions.

### 8.2 `get_sigma` signature

```python
def get_sigma(city_slug, source="ecmwf", market_type="highest"):
    key = f"{city_slug}_{source}_{market_type}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C
```

The default fallback (`SIGMA_F` / `SIGMA_C`) is shared. If lowest markets turn out to have systematically different sigma defaults, separate `SIGMA_F_MIN` / `SIGMA_C_MIN` constants can be added later — deferred.

### 8.3 `run_calibration` loop

Adds an outer loop over `MARKET_TYPES`:

```python
for market_type in MARKET_TYPES:
    for source in ("ecmwf", "hrrr", "metar"):
        for city in cities_with_resolved_markets_of_type(market_type):
            ...
            cal[f"{city}_{source}_{market_type}"] = {...}
```

`resolved` is filtered by `m["type"] == market_type` for each iteration.

### 8.4 Migration

Existing entries in `data/calibration.json` use the old 2-segment key. On load, any 2-segment key is migrated in place to the 3-segment form with `_highest` appended:

```python
def load_cal():
    if not CALIBRATION_FILE.exists():
        return {}
    raw = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    migrated = {}
    for k, v in raw.items():
        # 2-segment legacy → tag as highest
        if k.count("_") == 1:
            migrated[f"{k}_highest"] = v
        else:
            migrated[k] = v
    if migrated != raw:
        CALIBRATION_FILE.write_text(json.dumps(migrated, indent=2), encoding="utf-8")
    return migrated
```

Idempotent and safe.

### 8.5 `get_actual_temp` market-type awareness

```python
def get_actual_temp(city_slug, date_str, market_type="highest"):
    ...
    element = "tempmax" if market_type == "highest" else "tempmin"
    url = (
        f"https://weather.visualcrossing.com/.../timeline/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements={element}"
    )
    ...
    if days and days[0].get(element) is not None:
        return round(float(days[0][element]), 1)
    return None
```

The auto-resolution branch in `scan_and_update()` passes `mkt["type"]` when fetching `actual_temp`.

---

## 9. Edge Cases

### 9.1 City has highest market but no lowest market on the same date

`get_polymarket_event(..., "lowest")` returns `None`. The inner loop body executes `continue` and no lowest market file is created. The highest market is processed normally. No error, no warning.

### 9.2 City has lowest market but no highest market on the same date

Symmetric to 9.1 — the highest branch is skipped, the lowest branch is processed. This is rare in practice (Polymarket typically lists both together) but supported.

### 9.3 Both markets exist, both have positions, position management is independent

The two markets are stored in separate files and have separate `position` objects. The forecast-change close, stop-loss, trailing stop, and take-profit logic operate on each market independently — subject to the consistency constraint in §9.5.

### 9.4 The same city/date appears twice in `print_status` open positions

Acceptable. The output adds a bracketed `[HI]` / `[LO]` tag (see §11) so the two rows are distinguishable.

### 9.5 Cross-market bucket consistency — hard constraint

**The daily low temperature is always ≤ the daily high temperature.** This is a physical invariant, not a probabilistic one. It directly constrains which bucket combinations are valid across the two market types for the same city/date:

- If the **lowest market** position is in bucket `[L_low, L_high]`, then the daily high **must** be ≥ L_low. A highest-market position in any bucket `[H_low, H_high]` where `H_high < L_low` is **physically impossible** — both cannot resolve YES simultaneously.
- Symmetrically, if the **highest market** position is in bucket `[H_low, H_high]`, any lowest-market bucket where `L_low > H_high` is physically impossible.

**Example of an impossible combination:** lowest bucket "63-65°F" + highest bucket "58-62°F". If the high is at most 62°F, the low cannot be 63°F or above.

**Example of a valid but correlated combination:** lowest bucket "58-62°F" + highest bucket "73-77°F". These are consistent (high > low), but still correlated through the physical relationship.

**Guard at entry time (`scan_and_update`):**

Before opening a new position, check whether the other market type already has an open position for the same city/date:

```python
def _buckets_are_consistent(hi_low, hi_high, lo_low, lo_high):
    """Return True if the bucket pair is physically possible."""
    # The day's high must be >= the day's low, so the high bucket
    # must not be entirely below the low bucket.
    return hi_high >= lo_low  # at least some overlap is possible

# In scan_and_update, before calling place_order:
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
        print(f"  [SKIP] {city} {date} {market_type}: bucket {t_low}-{t_high} "
              f"inconsistent with open {other_type} position "
              f"{other_pos['bucket_low']}-{other_pos['bucket_high']}")
        continue
```

**Why this matters in practice:**

In normal operation, `forecast_max ≥ forecast_min` is guaranteed by the API (ECMWF/HRRR always return physically consistent daily extremes), so the bot naturally selects consistent buckets. The guard defends against:

1. **Rounding edge cases** — both extremes round to the same value, causing ambiguous bucket selection.
2. **Stale positions** — forecast shifts after entry; the highest position's bucket may drift below an open lowest position's bucket over time.
3. **Data anomalies** — a corrupted forecast snapshot produces `max < min`; the guard prevents acting on it.

**Forecast consistency pre-check (`take_forecast_snapshot`):**

If after fetching, `forecast_max < forecast_min` for a date, log a warning and set both to `None` for that date — skip entry on both market types rather than propagating bad data:

```python
if t_max is not None and t_min is not None and t_max < t_min:
    print(f"  [WARN] {city_slug} {date}: max ({t_max}) < min ({t_min}), skipping both")
    t_max = t_min = None
```

### 9.6 Polymarket runs a single resolution station for both market types

Confirmed — the same Wunderground URL appears in both events. The dynamic LOCATIONS station update in `new_market()` may fire twice per scan for the same city; the second call is a no-op. Acceptable.

### 9.7 ECMWF returns null for one extreme but not the other

`get_ecmwf` produces `{"max": 78, "min": None}`. `take_forecast_snapshot` propagates the null; `best` for the lowest market becomes `None`; entry is skipped. No crash.

### 9.8 Legacy market file on disk with no `type` field

Handled by `_normalize_legacy_filenames()` and `setdefault("type", "highest")`. Idempotent.

### 9.9 The same `(city, date)` has both an old `nyc_2026-04-23.json` and a new `nyc_2026-04-23_highest.json`

Possible if a user manually downgrades the bot mid-experiment. Normalization logs a warning and leaves the legacy file untouched. Operator must manually merge or delete.

---

## 10. Files Changed — Function/Constant List

### `weatherbot.py`

| Function / Constant | Change | Approx. lines |
|---|---|---|
| `MARKET_TYPES` (new constant) | Add `("highest", "lowest")` | +1 |
| `get_ecmwf` | Request both extremes; return shape `{date: {max, min}}` | ~10 |
| `get_hrrr` | Same change as `get_ecmwf` | ~10 |
| `get_metar_min` (new) | Rolling-min over local-day window | ~25 |
| `_metar_local_date` (new helper) | Epoch + tz → local date string | ~5 |
| `get_actual_temp` | Add `market_type="highest"` param; route to `tempmax` / `tempmin` | ~5 |
| `get_polymarket_event` | Add `market_type="highest"` param; switch slug prefix | ~3 |
| `take_forecast_snapshot` | Return per-type snapshots; pick min vs max from new return shape; route METAR; pre-check `max ≥ min` | ~35 |
| `new_market` | Add `market_type="highest"` param; set `mkt["type"]` | ~3 |
| `market_path` | Add `market_type="highest"` param; new naming | ~3 |
| `load_market` | Add `market_type="highest"` param | ~3 |
| `save_market` | Derive type from `market["type"]` (no param — type is always present on the dict) | ~2 |
| `load_all_markets` | Call `_normalize_legacy_filenames()` first; no param needed (reads all files) | ~3 |
| `_normalize_legacy_filenames` (new) | Migrate legacy `*_<date>.json` → `*_<date>_highest.json` | ~15 |
| `get_sigma` | Add `market_type="highest"` param; new key format | ~5 |
| `run_calibration` | Add outer loop over `MARKET_TYPES`; filter resolved by type | ~10 |
| `load_cal` | Migrate 2-segment legacy keys to 3-segment | ~10 |
| `_buckets_are_consistent` (new) | Guard: returns False if highest bucket is entirely below lowest bucket | ~5 |
| `scan_and_update` | Inner loop over `MARKET_TYPES`; pass type to all updated functions; cross-market bucket consistency check before entry | ~35 |
| `print_status` | Display `[HI]` / `[LO]` tag in open-positions block | ~3 |
| `print_report` | Display type column; group resolved by `(city, type)` | ~10 |

**Total:** ~240 lines changed/added, ~50 lines deleted.

**`market_type` defaulting rule:** every function that touches a per-market file or uses a type-specific value must accept `market_type="highest"` as a keyword argument. This keeps the existing call sites (tests, CLI commands, and any future tooling) working without modification while the inner loop in `scan_and_update` passes the explicit value. The only exceptions are `save_market` (reads from `market["type"]`) and `load_all_markets` (glob — type is on each dict). After implementation, run `grep -n "def.*market" weatherbot.py` and confirm every function in this table has the default.

### `config.json`

No required changes. Optional future keys (out of scope here):
- `sigma_f_min`, `sigma_c_min` for asymmetric defaults
- `disable_market_types` to opt out of one type globally

### `dashboard.py`

| Function | Change |
|---|---|
| `build_dashboard_data` | Add `"market_type"` field to `open_positions`, `closed_positions`, `forecasts` entries |
| `detect_changes` | Prefix BUY / EXIT / FORECAST activity messages with `[HI]` or `[LO]` |

### `dashboard_ui/static/dashboard.js`

| Location | Change |
|---|---|
| `updatePositions` (~line 253) | Render `[HI]`/`[LO]` badge next to city name |
| `updateHistory` (~line 326) | Same badge in closed-position rows |
| `buildPopupHtml` (~line 54) | Badge in position section; update `find` → `filter` to handle both types per city |
| `buildMarkerHtml` (~line 28) | Pick highest-EV position for marker icon when both types are open |
| `updateWorldMap` (~line 195) | Show both bucket ranges in city card when both types are open |

### `dashboard_ui/static/style.css`

Add `.badge-hi` (blue) and `.badge-lo` (orange) — ~14 lines.

### `docs/weatherbot.md`

Updated alongside the code change. Add a `**Version note (feature/lowest-temp-markets, 2026-04-26):**` callout at the top of each affected section so future readers can identify when the behaviour changed:

- **Market Discovery** section — document both slug patterns and the `market_type` parameter on `get_polymarket_event`
- **Data Storage** section — document new `{city}_{date}_{type}.json` naming, the one-time legacy migration, and the `"type"` field on each market dict
- **Forecast Sources** section — document that `get_ecmwf`/`get_hrrr` now return `{max, min}` per date and that `take_forecast_snapshot` routes the correct extreme per market type
- **METAR** section — document `get_metar_min` and the rolling-minimum approach for D+0 lowest markets
- **Calibration** section — document `(city, source, market_type)` key format and `load_cal` migration
- **Function Reference** table — update signatures for all changed functions with `market_type="highest"` defaults

---

## 11. Reporting Output Examples

`print_status` (open positions block):

```
  Open positions:
    New York City    2026-04-27 [HI] 75-77F   | entry $0.310 → $0.420 | PnL: +0.55 | ECMWF
    New York City    2026-04-27 [LO] 55-57F   | entry $0.280 → $0.250 | PnL: -0.15 | ECMWF
    Chicago          2026-04-28 [HI] 65-67F   | ...
```

`print_report` (by-city section):

```
  By city:
    New York City    HI: 12/18 (67%)  PnL: +18.40
    New York City    LO:  8/14 (57%)  PnL:  +6.20
    Chicago          HI:  9/16 (56%)  PnL:  -2.10
```

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Doubled Polymarket API call rate (160 vs 80 per scan) hits Gamma rate limits | Existing `time.sleep(0.1)` between inner-loop calls is preserved; total scan time grows ~1 minute. If 429s appear, increase the sleep to 0.2s. |
| Lowest markets have lower volume than highest, increasing `MIN_VOLUME` filter rejects | Acceptable — the filter exists for a reason. Document in `docs/weatherbot.md` that lowest markets may produce fewer entries until liquidity builds. |
| Calibration MAE for lowest markets diverges from highest, but defaults are shared | Acceptable initially — defaults (`SIGMA_F=2.0`, `SIGMA_C=1.2`) are conservative for both. After 30+ resolved lowest markets per city, calibration takes over. |
| METAR rolling-min API (`hours=24`) is heavier than the current single-obs call | Only fired for D+0 lowest markets — at most 20 calls per scan. Aviation Weather has no published rate limit; acceptable. |
| Legacy file normalization runs on a busy `data/markets/` (1000s of files) | Idempotent and runs once per process boot. O(n) glob completes in well under a second. |
| Two market files for same city/date can have inconsistent station codes | Each market stores its own `station` field; they resolve independently. Harmless. |
| Existing tests assume old `market_path(city, date)` 2-arg signature | Tests get a `market_type="highest"` default to keep them passing. New tests for `"lowest"` paths are added. |

---

## 13. Testing Strategy

### Unit tests

| Function | Cases |
|---|---|
| `get_ecmwf` (mocked) | Returns both max and min; preserves rounding rules; null tolerance |
| `get_hrrr` (mocked) | Same as above; non-US returns `{}` unchanged |
| `get_polymarket_event` | Builds `highest-...` and `lowest-...` slugs correctly |
| `_normalize_legacy_filenames` | Renames legacy file; idempotent on second run; skips if both exist |
| `load_cal` migration | 2-segment keys → 3-segment with `_highest`; idempotent |
| `get_metar_min` (mocked) | Filters obs by local date; converts °C → °F for US; returns None on empty |
| `get_actual_temp` | Picks `tempmax` for highest, `tempmin` for lowest |
| `bucket_prob`, `calc_ev`, `calc_kelly`, `bet_size` | Unchanged; existing tests still pass |

### Integration tests

Fixtures and golden outputs to be created in `tests/fixtures/` alongside the implementation:

| Fixture file | Contents | Used by |
|---|---|---|
| `tests/fixtures/gamma_nyc_highest.json` | Captured Polymarket event response for `highest-temperature-in-nyc-on-apr-28-2026` | `get_polymarket_event` unit test |
| `tests/fixtures/gamma_nyc_lowest.json` | Captured Polymarket event response for `lowest-temperature-in-nyc-on-apr-28-2026` | `get_polymarket_event` unit test |
| `tests/fixtures/open_meteo_nyc.json` | Captured Open-Meteo response with both `temperature_2m_max` and `temperature_2m_min` | `get_ecmwf` / `get_hrrr` unit tests |
| `tests/fixtures/metar_klga_history.json` | 24h METAR history for KLGA with `obsTime` + `temp` fields | `get_metar_min` unit test |
| `tests/fixtures/markets_mixed_type/` | Directory with one `*_highest.json` and one `*_lowest.json` for the same city/date, plus one legacy `*.json` | Integration test for full scan + normalization |
| `tests/fixtures/calibration_legacy.json` | `calibration.json` with 2-segment keys | `load_cal` migration unit test |
| `tests/fixtures/calibration_migrated_golden.json` | Expected output after migration | `load_cal` migration assertion |

**Integration test scenarios:**

- Full scan against `markets_mixed_type/` fixture: assert both typed files updated, legacy file renamed, no duplicate snapshot rows.
- Calibration run against `markets_mixed_type/` with resolved outcomes: assert 3-segment keys written and match `calibration_migrated_golden.json`.
- Cross-market consistency guard: mock a scan where the lowest forecast rounds above the highest forecast → assert both market entries skipped with a `[WARN]` line.

### Manual smoke test

- Run one full scan in a clean `data/` and inspect `data/markets/`:
  - Two files per city/date that exists on Polymarket
  - Each file has `"type": "highest"` or `"type": "lowest"`
  - `forecast_snapshots[*].best` matches the expected extreme for the type
- Run a second scan against the same `data/` — confirm idempotency (no duplicate snapshots beyond the second scan's expected one).

---

## 14. Pre-Merge Checklist

**API / bot correctness**
- [ ] `grep -n "def.*market\|market_type" weatherbot.py` — every function in §10 table has `market_type="highest"` default.
- [ ] `grep -n '"highest"' weatherbot.py` — no hardcoded `"highest"` string outside the slug builder and the default parameter values.
- [ ] `grep -n 'temperature_2m_max' weatherbot.py` — appears only inside `get_ecmwf` / `get_hrrr` URL strings, nowhere else.
- [ ] `_normalize_legacy_filenames` tested against a copy of production `data/markets/` — no errors, all files renamed, idempotent on second run.
- [ ] `load_cal` migration tested against a copy of production `data/calibration.json` — all 2-segment keys become 3-segment, no data loss.

**Tests**
- [ ] All existing tests pass with `market_type="highest"` defaults (no signature changes needed at call sites).
- [ ] New unit tests for lowest-market paths pass (see §13 fixture table).
- [ ] Integration test: fresh `data/` dir produces both `*_highest.json` and `*_lowest.json` for at least one city after one scan.
- [ ] Calibration integration test produces golden output matching `calibration_migrated_golden.json`.
- [ ] Cross-market consistency guard test: `[WARN]` line emitted and both entries skipped when `max < min`.

**Dashboard**
- [ ] Dashboard shows `[HI]`/`[LO]` badges in open positions table, trade history, map popups, and city cards.
- [ ] City with both types open shows both bucket ranges in the world map card without layout overflow at 1280px.
- [ ] City with no open positions of either type renders cleanly — no badge errors, no empty placeholder rows.
- [ ] Pre-feature position data (no `market_type` field) defaults to `[HI]` without crashing.
- [ ] Activity feed BUY / EXIT / FORECAST messages include `[HI]`/`[LO]` prefix.

**Docs**
- [ ] `docs/weatherbot.md` version callouts added to all affected sections.
- [ ] Function reference table in `docs/weatherbot.md` updated with new signatures.
- [ ] This spec updated to `Status: Merged` and `Version: 1.2` after merge.

---

## 15. Success Criteria

After this change is live for one week:

- `data/markets/` contains both `*_highest.json` and `*_lowest.json` files for cities where Polymarket lists both
- `data/calibration.json` keys all have 3-segment `(city, source, market_type)` form
- No regressions in highest-market trading metrics (compare PnL, win rate, trade count to the prior week)
- At least one lowest-market position has been opened, managed (snapshots updated each scan), and either closed by an exit mechanism or held to resolution

After 30+ resolved lowest markets per city:

- Calibration writes per-type sigma values; `_min` keys exist alongside `_max` keys for at least 5 cities
- Per-type sigma values diverge meaningfully from the shared default (>0.2 delta), validating the decision to track them separately

---

## 16. Open Questions

1. **Should `sigma_f_min` / `sigma_c_min` be added now or later?** Recommend deferring until calibration shows divergence — defaults are conservative for both types.
2. **Is the `_metar_local_date` helper worth a `zoneinfo` dependency?** Python 3.9+ has `zoneinfo` in stdlib — check CI Python version (currently 3.13 per `ci.yml`). No issue.
3. **Should the dashboard panel header include `[HI]/[LO]`?** ✅ Resolved — in scope. See §4.4.
4. **Should the bot opt out of one type via config?** A `disable_market_types: ["lowest"]` flag would let an operator A/B the addition. Deferred — easy to add later.
