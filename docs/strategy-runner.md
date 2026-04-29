# Strategy Runner — Parallel Strategy Testing

## The Problem

Testing a strategy improvement on Polymarket weather markets requires real resolved markets. A market opens, runs for up to 3 days, then resolves. That means a single improvement takes **4–6 weeks** to produce enough data to evaluate.

If we test improvements one after another, comparing 5 variants takes months.

---

## The Solution: Parallel Isolation

Instead of testing improvements sequentially, we run **3 copies of the bot simultaneously** — one per strategy variant. Each copy runs in its own isolated directory with its own config, its own `data/` folder, and its own calibration. They never interfere with each other.

```
runs/
├── prob_model/        ← improvement #1: normal CDF for all buckets
├── time_decay/        ← improvement #3: smaller bets on D+2/D+3
└── dynamic_ev/        ← improvement #6: higher MIN_EV in volatile cities
```

After 4–6 weeks, all variants have traded the **same markets at the same prices**, so the comparison is apples-to-apples. The only difference between variants is the strategy flags in their config. The main bot serves as the control group.

---

## Strategy Variants

| Variant | Flags enabled | What it tests |
|---|---|---|
| main bot | none | Control group — binary p=1.0, no modifications |
| `prob_model` | `prob_model_normal_cdf: true` | Improvement #1: interior buckets use normal CDF instead of binary 0/1 |
| `time_decay` | `time_decay: true` | Improvement #3: bets scaled by horizon (D+0 full size, D+3 = 40%) |
| `dynamic_ev` | `dynamic_min_ev: true` | Improvement #6: MIN_EV scaled by city sigma (more selective in volatile markets) |

---

## What Changed in weatherbot.py

Three strategy flags are loaded from `config.json` at startup, all defaulting to `False` so the live bot is unaffected:

```python
_strat           = _cfg.get("strategy", {})
STRAT_PROB_MODEL = _strat.get("prob_model_normal_cdf", False)
STRAT_TIME_DECAY = _strat.get("time_decay", False)
STRAT_DYNAMIC_EV = _strat.get("dynamic_min_ev", False)
```

**Improvement #1 — `bucket_prob`:**
```python
# main bot: interior bucket returns 1.0 if forecast lands in it, 0.0 otherwise
# prob_model: applies normal distribution to interior buckets too
if STRAT_PROB_MODEL:
    return norm_cdf((t_high - forecast) / s) - norm_cdf((t_low - forecast) / s)
return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
```
A forecast of 74.8°F on a 73–75°F bucket is no longer treated as certain (p=1.0). With sigma=2.0 it becomes p≈0.54, producing a more realistic EV.

**Improvement #3 — `bet_size`:**
```python
_HORIZON_MULT = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.4}

def bet_size(kelly, balance, horizon_days=None):
    multiplier = _HORIZON_MULT.get(horizon_days, 1.0) if STRAT_TIME_DECAY else 1.0
    return round(min(kelly * balance * multiplier, MAX_BET), 2)
```
A D+3 bet is sized at 40% of what a D+0 bet would be — reflecting that 3-day forecasts are less reliable.

**Improvement #6 — `effective_min_ev`:**
```python
effective_min_ev = MIN_EV * max(1.0, sigma / SIGMA_REF) if STRAT_DYNAMIC_EV else MIN_EV
if ev >= effective_min_ev:
    ...
```
Chicago in March (sigma≈2.8°F) requires higher EV to enter than Singapore (sigma≈0.8°C).

---

## How to Use It

### Step 1 — Setup (one time)

Creates the isolated run directories and symlinks:

```bash
python strategy_runner.py setup
```

**What it does:** For each variant, creates `runs/<variant>/`, copies the strategy config as `config.json`, and symlinks `weatherbot.py` from the repo root (so changes to the bot code are reflected immediately without re-running setup).

---

### Step 2 — Add your vc_key

Each `runs/<variant>/config.json` has `"vc_key": ""`. Fill it in (Visual Crossing key is needed for resolution):

```bash
# Quick way — replace in all variants at once
for d in runs/*/; do
  python3 -c "
import json, pathlib
p = pathlib.Path('$d/config.json')
c = json.loads(p.read_text())
c['vc_key'] = 'YOUR_KEY_HERE'
p.write_text(json.dumps(c, indent=2))
"
done
```

---

### Step 3 — Start

```bash
python strategy_runner.py start         # start all 5 variants
python strategy_runner.py start baseline  # or just one
```

Each variant starts as a background subprocess with its own log. Variants are staggered **60 seconds apart** to avoid hammering the forecast APIs simultaneously.

---

### Step 4 — Monitor

```bash
python strategy_runner.py status        # balance, P&L, trade count for all variants
python strategy_runner.py logs prob_model # last 50 lines of a variant's log
```

Compare all variants side-by-side:

```bash
python strategy_compare.py
```

Example output (after 2 weeks):

```
Variant          Description                                Balance      PnL    ROI%  Trades  Wins  WinRate    AvgEV
----------------------------------------------------------------------------------------------------------------------
main             Control group (binary p=1.0)              1023.40   +23.40   +2.3%      18    11    61.1%   0.3421
prob_model       Normal CDF for interior buckets (#1)      1041.20   +41.20   +4.1%      22    14    63.6%   0.2987
time_decay       Horizon multiplier on bet size (#3)       1031.80   +31.80   +3.2%      18    12    66.7%   0.3421
dynamic_ev       Dynamic MIN_EV by sigma (#6)              1028.60   +28.60   +2.9%      14    10    71.4%   0.3812

Best P&L so far: prob_model (+41.20)
Most trades:     prob_model (22 trades)
```

---

### Step 5 — Stop

```bash
python strategy_runner.py stop           # stop all variants
python strategy_runner.py stop prob_model # or just one
```

---

## FAQ

**Will the variants interfere with the live bot?**
No. Each variant runs in its own `runs/<variant>/` directory, with its own `data/` and `config.json`. The live bot runs from the project root. They are completely isolated.

**Why symlink weatherbot.py instead of copying?**
Because if you fix a bug in the main bot during the experiment, the fix automatically applies to all variants — you don't need to re-setup. Only configs are copied (not symlinked) because each variant needs its own flags.

**What if I want to stop and restart a variant mid-experiment?**
Just run `stop` then `start` for that variant. The market files and state.json persist, so it picks up where it left off.

**When is the data meaningful?**
After at least **30 resolved markets per city** (per the `calibration_min` setting). In practice, plan for **4–6 weeks** of live operation before drawing conclusions. After 2 weeks you'll have directional signal; after 6 weeks you'll have statistical confidence.

**Can I add a new variant mid-experiment?**
Yes — create a new JSON in `strategies/`, add it to `VARIANTS` in both runner and compare scripts, run `setup` for just that variant, and `start` it. It starts fresh while the others continue.
