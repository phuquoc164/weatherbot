#!/usr/bin/env python3
"""
strategies/compare.py — Side-by-side comparison of running strategy variants.

Reads each variant's state.json and resolved market files to produce
a comparison table: balance, P&L, win rate, avg EV, trade count.

Usage:
    python strategies/compare.py
    python strategies/compare.py --json     # machine-readable output
"""

import json
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent
RUNS_DIR = ROOT / "runs"

VARIANTS = ["baseline", "prob_model", "time_decay", "dynamic_ev", "combined"]

DESCRIPTIONS = {
    "baseline":   "No improvements (control)",
    "prob_model": "Normal CDF for interior buckets (#1)",
    "time_decay": "Horizon multiplier on bet size (#3)",
    "dynamic_ev": "Dynamic MIN_EV by sigma (#6)",
    "combined":   "All three improvements combined",
}


def load_variant(name: str) -> dict | None:
    vdir = RUNS_DIR / name
    if not vdir.exists():
        return None

    state_path = vdir / "data" / "state.json"
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    markets_dir = vdir / "data" / "markets"
    closed = []
    if markets_dir.exists():
        for fp in markets_dir.glob("*.json"):
            try:
                m = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            pos = m.get("position")
            if pos and pos.get("status") == "closed":
                closed.append(pos)

    wins      = [p for p in closed if (p.get("pnl") or 0) > 0]
    total_pnl = sum(p.get("pnl") or 0 for p in closed)
    evs       = [p.get("ev") for p in closed if p.get("ev") is not None]
    avg_ev    = sum(evs) / len(evs) if evs else None

    start_bal = state.get("starting_balance", state.get("balance", 0.0))
    balance   = state.get("balance", 0.0)

    return {
        "name":        name,
        "description": DESCRIPTIONS.get(name, ""),
        "balance":     balance,
        "start_bal":   start_bal,
        "pnl":         round(total_pnl, 2),
        "roi":         round((balance - start_bal) / start_bal * 100, 2) if start_bal else 0.0,
        "trades":      len(closed),
        "wins":        len(wins),
        "win_rate":    round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_ev":      round(avg_ev, 4) if avg_ev is not None else None,
    }


def print_table(rows: list[dict]):
    if not rows:
        print("No variant data found. Run 'python strategies/runner.py setup && start' first.")
        return

    col_w = 16

    header = (
        f"{'Variant':<{col_w}} {'Description':<42} "
        f"{'Balance':>10} {'PnL':>8} {'ROI%':>7} "
        f"{'Trades':>7} {'Wins':>5} {'WinRate':>8} {'AvgEV':>8}"
    )
    print()
    print(header)
    print("-" * len(header))

    for r in rows:
        pnl_str    = f"+{r['pnl']:.2f}" if r["pnl"] >= 0 else f"{r['pnl']:.2f}"
        roi_str    = f"+{r['roi']:.1f}%" if r["roi"] >= 0 else f"{r['roi']:.1f}%"
        win_str    = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "  n/a"
        avg_ev_str = f"{r['avg_ev']:.4f}" if r["avg_ev"] is not None else "   n/a"
        desc       = r["description"][:41]

        print(
            f"{r['name']:<{col_w}} {desc:<42} "
            f"{r['balance']:>10.2f} {pnl_str:>8} {roi_str:>7} "
            f"{r['trades']:>7} {r['wins']:>5} {win_str:>8} {avg_ev_str:>8}"
        )

    print()
    best = max(rows, key=lambda r: r["pnl"])
    print(f"Best P&L so far: {best['name']} ({best['pnl']:+.2f})")

    most_trades = max(rows, key=lambda r: r["trades"])
    if most_trades["trades"] > 0:
        print(f"Most trades:     {most_trades['name']} ({most_trades['trades']} trades)")
    print()


def main():
    as_json = "--json" in sys.argv

    rows = []
    for name in VARIANTS:
        result = load_variant(name)
        if result:
            rows.append(result)
        elif not as_json:
            print(f"[skip] {name}: not set up or no data yet")

    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
