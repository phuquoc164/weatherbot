#!/usr/bin/env python3
"""
strategies/runner.py — Run multiple strategy variants in parallel.

Each variant runs weatherbot.py in its own isolated directory so configs,
data, and calibration files never mix.

Usage:
    python strategies/runner.py setup              # create run dirs, copy files
    python strategies/runner.py start             # start all variants
    python strategies/runner.py start baseline    # start one variant
    python strategies/runner.py status            # show running/stopped variants
    python strategies/runner.py stop              # stop all variants
    python strategies/runner.py stop baseline     # stop one variant
    python strategies/runner.py logs baseline     # tail latest log for a variant
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT       = Path(__file__).parent.parent
RUNS_DIR   = ROOT / "runs"
STRATS_DIR = ROOT / "strategies" / "configs"

VARIANTS = ["baseline", "prob_model", "time_decay", "dynamic_ev"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def variant_dir(name: str) -> Path:
    return RUNS_DIR / name


def pid_file(name: str) -> Path:
    return variant_dir(name) / "weatherbot.pid"


def log_file(name: str) -> Path:
    return variant_dir(name) / "logs" / "weatherbot.out"


def is_running(name: str) -> bool:
    pf = pid_file(name)
    if not pf.exists():
        return False
    pid = int(pf.read_text().strip())
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def read_state(name: str) -> dict:
    state_path = variant_dir(name) / "data" / "state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup(target: str | None = None):
    """Create isolated run directories for each variant."""
    names = [target] if target else VARIANTS
    for name in names:
        cfg_file = STRATS_DIR / f"{name}.json"
        if not cfg_file.exists():
            print(f"[skip] {name}: no strategies/{name}.json found")
            continue

        vdir = variant_dir(name)
        (vdir / "data" / "markets").mkdir(parents=True, exist_ok=True)
        (vdir / "logs").mkdir(parents=True, exist_ok=True)

        shutil.copy(cfg_file, vdir / "config.json")

        wb_link = vdir / "weatherbot.py"
        if wb_link.exists() or wb_link.is_symlink():
            wb_link.unlink()
        wb_link.symlink_to(ROOT / "weatherbot.py")

        print(f"[ok]   {name}: {vdir}")

    print("\nRun  'python strategies/runner.py start'  to launch all variants.")


def cmd_start(target: str | None = None, stagger: int = 120):
    """Start weatherbot subprocesses for each variant.

    stagger: seconds between each variant start (default 120).
    Each bot scans every ~3600s, so a 120s stagger keeps them
    permanently offset and avoids simultaneous API bursts.
    """
    names = [target] if target else VARIANTS

    for idx, name in enumerate(names):
        vdir = variant_dir(name)
        if not (vdir / "config.json").exists():
            print(f"[skip] {name}: not set up — run 'setup' first")
            continue

        if is_running(name):
            print(f"[skip] {name}: already running (pid {pid_file(name).read_text().strip()})")
            continue

        log = log_file(name)
        log.parent.mkdir(parents=True, exist_ok=True)

        with open(log, "a") as fh:
            fh.write(f"\n--- started {datetime.now(timezone.utc).isoformat()} ---\n")

        with open(log, "a") as fh:
            proc = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "weatherbot.py")],
                cwd=str(vdir),
                stdout=fh,
                stderr=fh,
                start_new_session=True,
            )

        pid_file(name).write_text(str(proc.pid))
        flags = _active_flags(name)
        print(f"[start] {name:15s} pid={proc.pid}  flags: {flags or 'none (baseline)'}")

        if idx < len(names) - 1:
            print(f"         (waiting {stagger}s before next variant to avoid API rate limits…)")
            time.sleep(stagger)


def cmd_status(target: str | None = None):
    """Print running/stopped status and key P&L metrics."""
    names = [target] if target else VARIANTS
    print(f"\n{'Variant':<16} {'Status':<10} {'Balance':>10} {'PnL':>10} {'Trades':>7}")
    print("-" * 58)
    for name in names:
        vdir = variant_dir(name)
        if not vdir.exists():
            print(f"{name:<16} {'not setup':<10}")
            continue

        status  = "running" if is_running(name) else "stopped"
        state   = read_state(name)
        balance = state.get("balance", 0.0)
        start   = state.get("starting_balance", balance)
        pnl     = round(balance - start, 2)
        trades  = len(state.get("closed_positions", []))

        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        print(f"{name:<16} {status:<10} {balance:>10.2f} {pnl_str:>10} {trades:>7}")
    print()


def cmd_stop(target: str | None = None):
    """Send SIGTERM to running variants."""
    names = [target] if target else VARIANTS
    for name in names:
        pf = pid_file(name)
        if not pf.exists():
            print(f"[skip] {name}: no pid file")
            continue
        pid = int(pf.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            pf.unlink()
            print(f"[stop] {name}: sent SIGTERM to pid {pid}")
        except ProcessLookupError:
            pf.unlink()
            print(f"[stop] {name}: process already gone, cleaned up pid file")


def cmd_logs(target: str):
    """Tail the log for a variant (last 50 lines)."""
    log = log_file(target)
    if not log.exists():
        print(f"No log found at {log}")
        return
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-50:]:
        print(line)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _active_flags(name: str) -> str:
    cfg_path = variant_dir(name) / "config.json"
    if not cfg_path.exists():
        return ""
    cfg   = json.loads(cfg_path.read_text())
    strat = cfg.get("strategy", {})
    active = [k for k, v in strat.items() if v is True]
    return ", ".join(active)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "setup":  cmd_setup,
    "start":  cmd_start,
    "status": cmd_status,
    "stop":   cmd_stop,
    "logs":   cmd_logs,
}


def main():
    parser = argparse.ArgumentParser(description="Strategy variant runner")
    parser.add_argument("command", choices=COMMANDS.keys())
    parser.add_argument("variant", nargs="?", help="Single variant to target (omit for all)")
    parser.add_argument(
        "--stagger", type=int, default=120, metavar="SECS",
        help="Seconds between variant starts (default: 120). Keeps scan cycles offset "
             "to avoid simultaneous API calls.",
    )
    args = parser.parse_args()

    if args.command == "logs":
        if not args.variant:
            print("Usage: python strategies/runner.py logs <variant>")
            sys.exit(1)
        cmd_logs(args.variant)
    elif args.command == "start":
        cmd_start(args.variant, stagger=args.stagger)
    else:
        COMMANDS[args.command](args.variant)


if __name__ == "__main__":
    main()
