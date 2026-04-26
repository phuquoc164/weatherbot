# Strategy Improvements — Backlog

Potential improvements to the trading strategy, to be implemented after the current strategy has been tested and validated.

---

## Table of Contents

1. [Better Probability Model](#1-better-probability-model)
2. [Ensemble Forecasting](#2-ensemble-forecasting)
3. [Time-Decay Confidence](#3-time-decay-confidence)
4. [Market Momentum Signal](#4-market-momentum-signal)
5. [Multi-Bucket Hedging](#5-multi-bucket-hedging)
6. [Dynamic MIN_EV Based on Volatility](#6-dynamic-min_ev-based-on-volatility)
7. [Implementation Priority](#implementation-priority)

---

## 1. Better Probability Model

**Status:** Not implemented

### Problem

Interior buckets currently use a binary probability — `1.0` if the forecast lands in the bucket, `0.0` otherwise. This ignores forecast uncertainty entirely for interior buckets, leading to overconfident EV calculations.

```python
# current behavior (bucket_prob)
# interior bucket: returns 1.0 or 0.0 — no uncertainty modeled
return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
```

### Proposed fix

Apply the normal distribution to **all** buckets, not just edge ones. The probability that the actual temperature lands in `[t_low, t_high]` given a forecast with uncertainty `sigma`:

```
P(t_low ≤ actual ≤ t_high) = norm_cdf((t_high − forecast) / sigma)
                             − norm_cdf((t_low  − forecast) / sigma)
```

```python
# proposed bucket_prob for interior buckets
def bucket_prob(forecast, t_low, t_high, sigma=None):
    s = sigma or 2.0
    if t_low == -999:
        return norm_cdf((t_high - forecast) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - forecast) / s)
    # apply normal distribution to interior buckets too
    return norm_cdf((t_high - forecast) / s) - norm_cdf((t_low - forecast) / s)
```

### Expected impact

- EV calculations become more realistic — a forecast of 74°F on a 73–75°F bucket is no longer treated as certain
- Reduces overconfident bets near bucket boundaries
- Directly benefits from calibrated sigma values

---

## 2. Ensemble Forecasting

**Status:** Not implemented

### Problem

The bot picks one "best" source and ignores the others entirely:
- HRRR if US city, otherwise ECMWF
- METAR (real-time observation) is recorded but never used as a signal

### Proposed fix

Weighted average of all available sources, with weights that shift as resolution approaches:

| Source | Weight (D+2/D+3) | Weight (D+0, near resolution) |
|---|---|---|
| ECMWF | 0.5 | 0.2 |
| HRRR | 0.4 | 0.3 |
| METAR | 0.1 | 0.5 |

As the market gets closer to resolution, METAR (actual current observation) becomes more reliable than model forecasts and should dominate.

```python
def weighted_forecast(snap, hours_left):
    ecmwf = snap.get("ecmwf")
    hrrr  = snap.get("hrrr")
    metar = snap.get("metar")

    if hours_left < 6 and metar is not None:
        # near resolution: heavily weight observation
        weights = {"ecmwf": 0.2, "hrrr": 0.3, "metar": 0.5}
    elif hours_left < 24:
        weights = {"ecmwf": 0.35, "hrrr": 0.4, "metar": 0.25}
    else:
        weights = {"ecmwf": 0.55, "hrrr": 0.45, "metar": 0.0}

    sources = {"ecmwf": ecmwf, "hrrr": hrrr, "metar": metar}
    total_w, total_v = 0.0, 0.0
    for name, val in sources.items():
        if val is not None:
            total_w += weights[name]
            total_v += weights[name] * val
    return round(total_v / total_w, 1) if total_w > 0 else None
```

### Expected impact

- Better forecast accuracy, especially in the last 6–12 hours before resolution
- METAR observations (already being fetched) become a meaningful signal
- Reduces losses from late forecast shifts

---

## 3. Time-Decay Confidence

**Status:** Not implemented

### Problem

A D+3 trade and a D+0 trade are sized identically if they have the same EV. But forecast error grows significantly with horizon — a 3-day forecast is substantially less reliable than a same-day one.

### Proposed fix

Apply a horizon multiplier to Kelly bet sizing:

| Horizon | Multiplier |
|---|---|
| D+0 | 1.0 (no reduction) |
| D+1 | 0.8 |
| D+2 | 0.6 |
| D+3 | 0.4 |

```python
HORIZON_MULTIPLIER = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.4}

def bet_size(kelly, balance, horizon_day=0):
    multiplier = HORIZON_MULTIPLIER.get(horizon_day, 0.4)
    raw = kelly * balance * multiplier
    return round(min(raw, MAX_BET), 2)
```

### Expected impact

- Smaller bets on uncertain long-horizon trades
- Larger bets on high-confidence near-term trades
- Better risk-adjusted returns over time

---

## 4. Market Momentum Signal

**Status:** Not implemented

### Problem

The bot only looks at the current price snapshot in isolation. It has no awareness of whether the market is moving toward or away from the forecast bucket.

The data is already available — `market_snapshots` stores price history for every market.

### Proposed fix

Calculate price momentum from stored snapshots and use it as a confidence modifier:

```python
def price_momentum(market_snapshots, market_id):
    """Returns recent price trend for a specific bucket.
    Positive = market moving in our favor.
    Negative = market moving against us.
    """
    prices = []
    for snap in market_snapshots[-5:]:  # last 5 snapshots
        if snap.get("market_id") == market_id:
            prices.append(snap["price"])
    if len(prices) < 2:
        return 0.0
    return prices[-1] - prices[0]
```

**Usage:**
- Momentum > 0 (market moving toward bucket): slight EV boost, or confirm entry
- Momentum < 0 (market moving away): require higher EV threshold before entry, or close early

### Expected impact

- Avoids entering trades where the market is already pricing out the forecast
- Potential early exit signal before forecast-change close is triggered

---

## 5. Multi-Bucket Hedging

**Status:** Not implemented

### Problem

When the forecast sits near a bucket boundary (e.g. 74.8°F on a 73–75 / 75–77 split), the bot bets entirely on one bucket. A small forecast error in either direction means a loss.

### Proposed fix

When the forecast is within `sigma/2` of a bucket boundary, split the position across the two adjacent buckets proportionally to their probability:

```python
def find_hedge_buckets(forecast, outcomes, sigma):
    candidates = []
    for o in outcomes:
        t_low, t_high = o["range"]
        p = bucket_prob(forecast, t_low, t_high, sigma)
        if p >= 0.15:  # meaningful probability
            candidates.append((o, p))
    return candidates  # if 2 returned, split bet proportionally
```

**Allocation example** (forecast = 74.8°F, sigma = 2.0):
- Bucket 73–75°F: p = 0.54 → 54% of budget
- Bucket 75–77°F: p = 0.31 → 31% of budget
- Remaining buckets: p < 0.15 → skip

### Expected impact

- Reduces binary loss on boundary forecasts
- Slightly lower EV per dollar but meaningfully lower variance
- Most useful in cities with high sigma (volatile forecast error)

---

## 6. Dynamic MIN_EV Based on Volatility

**Status:** Not implemented

### Problem

`MIN_EV = 0.10` is fixed for all cities and all seasons. But a trade in Singapore (stable tropical climate, low sigma) carries far less uncertainty than a trade in Chicago in March (high sigma, volatile weather).

Requiring the same EV threshold regardless of volatility means:
- Accepting too much risk in high-sigma markets
- Being too conservative in low-sigma markets

### Proposed fix

Scale `MIN_EV` with the calibrated sigma for that city-source pair:

```python
def dynamic_min_ev(sigma, base_min_ev=0.10):
    """Require higher EV in high-uncertainty markets."""
    # sigma_ref: typical well-calibrated sigma
    sigma_ref = 1.5
    scale = max(1.0, sigma / sigma_ref)
    return round(base_min_ev * scale, 3)
```

**Examples:**
| City | Sigma | Dynamic MIN_EV |
|---|---|---|
| Singapore | 0.8°C | 0.053 |
| London | 1.4°C | 0.093 |
| Chicago (March) | 2.8°F | 0.187 |

### Expected impact

- More trades in stable, predictable markets
- Higher bar in volatile markets where forecast errors are large
- Only meaningful after calibration has enough data (30+ resolved markets per city)

---

## Implementation Priority

When ready to extend the strategy, implement in this order:

| Priority | Improvement | Effort | Impact |
|---|---|---|---|
| 1 | Better Probability Model | Low — 5-line change to `bucket_prob` | High — fixes core modeling flaw |
| 2 | Ensemble Forecasting | Medium — new `weighted_forecast` function | High — uses already-fetched data |
| 3 | Time-Decay Confidence | Low — multiplier in `bet_size` | Medium — better risk sizing |
| 4 | Dynamic MIN_EV | Low — formula using calibrated sigma | Medium — requires calibration data |
| 5 | Market Momentum Signal | Medium — requires snapshot analysis | Medium — needs more data to validate |
| 6 | Multi-Bucket Hedging | High — changes position logic | Medium — reduces variance |

**Prerequisites before implementing any of these:**
- At least 30 resolved markets per city for calibration to be meaningful
- Baseline win rate and PnL data from the current strategy to compare against
