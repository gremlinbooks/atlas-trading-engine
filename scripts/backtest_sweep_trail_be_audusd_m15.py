#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
SWEEPS_DIR = REPORTS_DIR / "sweeps"
SYMBOL = "AUD_USD"
TIMEFRAME = "M15"


def _latest_file(pattern: str) -> Path:
    matches = [Path(p) for p in glob.glob(pattern)]
    if not matches:
        raise FileNotFoundError(f"No files match pattern: {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def _daily_pnl_stats(trades_csv: Path) -> dict[str, Any]:
    by_day: dict[str, float] = {}
    with trades_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("leg") == "ENTRY":
                continue
            day = (row.get("exit_ts") or "")[:10]
            if not day:
                continue
            by_day[day] = by_day.get(day, 0.0) + float(row.get("pnl_usd") or 0.0)
    if not by_day:
        return {"worst_day_pnl_usd": 0.0, "best_day_pnl_usd": 0.0, "positive_days_pct": 0.0}
    values = list(by_day.values())
    return {
        "worst_day_pnl_usd": min(values),
        "best_day_pnl_usd": max(values),
        "positive_days_pct": (sum(1 for v in values if v > 0) / len(values)) * 100.0,
    }


def _score(result: dict[str, Any]) -> float:
    pnl = float(result["total_pnl_usd"])
    pf = float(result["profit_factor"])
    max_dd = max(float(result["max_drawdown_pips"]), 1.0)
    return (pnl * pf) / max_dd


def _parse_csv_floats(text: str) -> list[float]:
    vals: list[float] = []
    for part in text.split(","):
        token = part.strip()
        if token:
            vals.append(float(token))
    return vals


def _parse_csv_ints(text: str) -> list[int]:
    vals: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if token:
            vals.append(int(token))
    return vals


def run() -> int:
    parser = argparse.ArgumentParser(description="Sweep trail_drawdown_pct x be_lock_pips for AUDUSD M15")
    parser.add_argument("--from", dest="from_date", default="2026-01-17T00:00:00")
    parser.add_argument("--to", dest="to_date", default="2026-02-16T12:30:00")
    parser.add_argument("--exec_profile", choices=["tv_panel", "live_reality"], default="live_reality")
    parser.add_argument("--magnifier", choices=["off", "m1"], default="m1")
    parser.add_argument("--entry_timing", choices=["close", "intrabar"], default="close")
    parser.add_argument("--trail_values", default="1.0,1.5,2.0")
    parser.add_argument("--be_values", default="10,15,20,25")
    parser.add_argument("--tp1_close_pct", type=int, default=40)
    parser.add_argument("--min_hold_bars", type=int, default=3)
    parser.add_argument("--block_session_enabled", action="store_true")
    parser.add_argument("--block_session", default="1400-1600")
    args = parser.parse_args()

    trail_values = _parse_csv_floats(args.trail_values)
    be_values = _parse_csv_ints(args.be_values)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = SWEEPS_DIR / f"audusd_m15_trail_be_sweep_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_cmd = [
        sys.executable,
        "-m",
        "app.backtest.run",
        "--symbol",
        SYMBOL,
        "--timeframe",
        TIMEFRAME,
        "--from",
        args.from_date,
        "--to",
        args.to_date,
        "--units",
        "7000",
        "--spread_pips",
        "1.4",
        "--tp1_pips",
        "20",
        "--sl_pips",
        "28",
        "--tp1_close_pct",
        str(args.tp1_close_pct),
        "--trail_drawdown_pct",
        "2.0",
        "--be_lock_pips",
        "20",
        "--bar_fill_policy",
        "conservative",
        "--use_runner",
        "true",
        "--use_stoch_exit",
        "false",
        "--exec_profile",
        args.exec_profile,
        "--magnifier",
        args.magnifier,
        "--use_bid_ask",
        "true",
        "--entry_timing",
        args.entry_timing,
    ]

    cases: list[tuple[float, int]] = [(trail, be) for trail in trail_values for be in be_values]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    print(f"Sweep output: {out_dir}")
    for idx, (trail, be_lock) in enumerate(cases, start=1):
        trail_label = str(trail).replace(".", "p")
        case_id = f"trail_{trail_label}_be_{be_lock}"
        print(f"[{idx}/{len(cases)}] Running {case_id}")

        cmd = list(base_cmd)
        cmd[cmd.index("--trail_drawdown_pct") + 1] = str(trail)
        cmd[cmd.index("--be_lock_pips") + 1] = str(be_lock)

        env = os.environ.copy()
        env["STRATEGY_ENABLED"] = "true"
        env["STRATEGY_MIN_HOLD_BARS"] = str(args.min_hold_bars)
        env["STRATEGY_BLOCK_TRADES"] = "true" if args.block_session_enabled else "false"
        env["STRATEGY_BLOCK_SESSION"] = args.block_session

        run_log = out_dir / f"{case_id}.log"
        with run_log.open("w", encoding="utf-8") as log_handle:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            failures.append({"case_id": case_id, "log": str(run_log)})
            print(f"  FAILED: {case_id} (see {run_log})")
            continue

        try:
            summary_path = _latest_file(str(REPORTS_DIR / f"summary_{SYMBOL}_{TIMEFRAME}_*.json"))
            trades_path = _latest_file(str(REPORTS_DIR / f"backtest_trades_{SYMBOL}_{TIMEFRAME}_*.csv"))
            equity_path = _latest_file(str(REPORTS_DIR / f"equity_{SYMBOL}_{TIMEFRAME}_*.csv"))
        except FileNotFoundError as exc:
            failures.append({"case_id": case_id, "log": str(run_log), "error": str(exc)})
            print(f"  FAILED: {case_id} ({exc})")
            continue

        archived_summary = out_dir / f"{case_id}_summary.json"
        archived_trades = out_dir / f"{case_id}_trades.csv"
        archived_equity = out_dir / f"{case_id}_equity.csv"
        shutil.copy2(summary_path, archived_summary)
        shutil.copy2(trades_path, archived_trades)
        shutil.copy2(equity_path, archived_equity)

        with archived_summary.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        metrics = payload.get("metrics", {})
        params = payload.get("params", {})
        daily = _daily_pnl_stats(archived_trades)

        result = {
            "case_id": case_id,
            "trail_drawdown_pct": trail,
            "be_lock_pips": be_lock,
            "tp1_close_pct": args.tp1_close_pct,
            "min_hold_bars": args.min_hold_bars,
            "block_session_enabled": args.block_session_enabled,
            "block_session": args.block_session if args.block_session_enabled else "",
            "from": params.get("from"),
            "to": params.get("to"),
            "total_trades": float(metrics.get("total_trades", 0.0)),
            "win_rate": float(metrics.get("win_rate", 0.0)),
            "profit_factor": float(metrics.get("profit_factor", 0.0)),
            "total_pnl_usd": float(metrics.get("total_pnl_usd", 0.0)),
            "max_drawdown_pips": float(metrics.get("max_drawdown_pips", 0.0)),
            "sharpe": float(metrics.get("sharpe", 0.0)),
            "tp1_hit_pct": float(metrics.get("tp1_hit_pct", 0.0)),
            "runner_pnl_usd": float(metrics.get("runner_pnl_usd", 0.0)),
            "num_stopouts": float(metrics.get("num_stopouts", 0.0)),
            "score": 0.0,
            **daily,
        }
        result["score"] = _score(result)
        results.append(result)
        print(
            "  OK "
            f"PF={result['profit_factor']:.3f} "
            f"PnL={result['total_pnl_usd']:.2f} "
            f"DD={result['max_drawdown_pips']:.2f} "
            f"stopouts={result['num_stopouts']:.0f}"
        )

    results.sort(
        key=lambda r: (r["score"], r["profit_factor"], r["total_pnl_usd"], -r["max_drawdown_pips"]),
        reverse=True,
    )

    results_json = out_dir / "results_ranked.json"
    with results_json.open("w", encoding="utf-8") as handle:
        json.dump({"generated_at_utc": stamp, "results": results, "failures": failures}, handle, indent=2)

    results_csv = out_dir / "results_ranked.csv"
    fields = [
        "rank",
        "case_id",
        "trail_drawdown_pct",
        "be_lock_pips",
        "tp1_close_pct",
        "min_hold_bars",
        "block_session_enabled",
        "block_session",
        "from",
        "to",
        "total_trades",
        "win_rate",
        "profit_factor",
        "total_pnl_usd",
        "max_drawdown_pips",
        "num_stopouts",
        "runner_pnl_usd",
        "tp1_hit_pct",
        "worst_day_pnl_usd",
        "best_day_pnl_usd",
        "positive_days_pct",
        "sharpe",
        "score",
    ]
    with results_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(results, start=1):
            writer.writerow({"rank": rank, **row})

    print("")
    print("Top candidates:")
    for rank, row in enumerate(results[:5], start=1):
        print(
            f"{rank}. {row['case_id']} | PF={row['profit_factor']:.3f} | "
            f"PnL={row['total_pnl_usd']:.2f} | DD={row['max_drawdown_pips']:.2f} | "
            f"Stopouts={row['num_stopouts']:.0f} | Score={row['score']:.4f}"
        )
    if failures:
        print("")
        print(f"Failures: {len(failures)}")
        for failure in failures:
            print(f" - {failure['case_id']} ({failure.get('error', 'see log')})")

    print("")
    print(f"Artifacts: {out_dir}")
    print(f"Ranked CSV: {results_csv}")
    print(f"Ranked JSON: {results_json}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(run())
