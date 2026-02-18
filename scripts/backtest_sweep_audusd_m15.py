#!/usr/bin/env python3
from __future__ import annotations

import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
SWEEPS_DIR = REPORTS_DIR / "sweeps"
SYMBOL = "AUD_USD"
TIMEFRAME = "M15"


@dataclass(frozen=True)
class SweepCase:
    tp1_close_pct: int
    min_hold_bars: int
    block_session_enabled: bool
    block_session: str

    @property
    def case_id(self) -> str:
        session = "block" if self.block_session_enabled else "open"
        return f"tp1_{self.tp1_close_pct}_hold_{self.min_hold_bars}_{session}"


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
            exit_ts = row.get("exit_ts", "")
            day = exit_ts[:10]
            if not day:
                continue
            by_day[day] = by_day.get(day, 0.0) + float(row.get("pnl_usd") or 0.0)
    if not by_day:
        return {"worst_day_pnl_usd": 0.0, "best_day_pnl_usd": 0.0, "positive_days_pct": 0.0}
    values = list(by_day.values())
    positive_days = sum(1 for v in values if v > 0)
    return {
        "worst_day_pnl_usd": min(values),
        "best_day_pnl_usd": max(values),
        "positive_days_pct": positive_days / len(values) * 100,
    }


def _score(result: dict[str, Any]) -> float:
    # Rewards profitability and PF while penalizing drawdown.
    pnl = float(result["total_pnl_usd"])
    pf = float(result["profit_factor"])
    max_dd = max(float(result["max_drawdown_pips"]), 1.0)
    return (pnl * pf) / max_dd


def run() -> int:
    parser = argparse.ArgumentParser(description="Run AUDUSD M15 parameter sweep")
    parser.add_argument("--from", dest="from_date", default="2026-02-09T00:00:00")
    parser.add_argument("--to", dest="to_date", default="2026-02-13T21:45:00")
    parser.add_argument("--exec_profile", choices=["tv_panel", "live_reality"], default="tv_panel")
    parser.add_argument("--magnifier", choices=["off", "m1"], default="off")
    parser.add_argument("--entry_timing", choices=["close", "intrabar"], default="close")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = SWEEPS_DIR / f"audusd_m15_income_sweep_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    sweep_cases = [
        SweepCase(tp1_close_pct=tp1, min_hold_bars=hold, block_session_enabled=block, block_session="1400-1600")
        for tp1 in (30, 40, 50)
        for hold in (3, 5)
        for block in (False, True)
    ]

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
        "--trail_drawdown_pct",
        "2.0",
        "--be_lock_pips",
        "20",
        "--tp1_close_pct",
        "30",
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

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    print(f"Sweep output: {out_dir}")
    for idx, case in enumerate(sweep_cases, start=1):
        print(f"[{idx}/{len(sweep_cases)}] Running {case.case_id}")
        cmd = list(base_cmd)
        cmd[cmd.index("--tp1_close_pct") + 1] = str(case.tp1_close_pct)

        env = os.environ.copy()
        env["STRATEGY_ENABLED"] = "true"
        env["STRATEGY_MIN_HOLD_BARS"] = str(case.min_hold_bars)
        env["STRATEGY_BLOCK_TRADES"] = "true" if case.block_session_enabled else "false"
        env["STRATEGY_BLOCK_SESSION"] = case.block_session

        run_log = out_dir / f"{case.case_id}.log"
        with run_log.open("w", encoding="utf-8") as log_handle:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )

        if proc.returncode != 0:
            failures.append({"case_id": case.case_id, "log": str(run_log)})
            print(f"  FAILED: {case.case_id} (see {run_log})")
            continue

        try:
            summary_path = _latest_file(str(REPORTS_DIR / f"summary_{SYMBOL}_{TIMEFRAME}_*.json"))
            trades_path = _latest_file(str(REPORTS_DIR / f"backtest_trades_{SYMBOL}_{TIMEFRAME}_*.csv"))
            equity_path = _latest_file(str(REPORTS_DIR / f"equity_{SYMBOL}_{TIMEFRAME}_*.csv"))
        except FileNotFoundError as exc:
            failures.append({"case_id": case.case_id, "log": str(run_log), "error": str(exc)})
            print(f"  FAILED: {case.case_id} ({exc})")
            continue

        archived_summary = out_dir / f"{case.case_id}_summary.json"
        archived_trades = out_dir / f"{case.case_id}_trades.csv"
        archived_equity = out_dir / f"{case.case_id}_equity.csv"
        shutil.copy2(summary_path, archived_summary)
        shutil.copy2(trades_path, archived_trades)
        shutil.copy2(equity_path, archived_equity)

        with archived_summary.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        metrics = payload.get("metrics", {})
        params = payload.get("params", {})
        daily = _daily_pnl_stats(archived_trades)
        result = {
            "case_id": case.case_id,
            "tp1_close_pct": case.tp1_close_pct,
            "min_hold_bars": case.min_hold_bars,
            "block_session_enabled": case.block_session_enabled,
            "block_session": case.block_session if case.block_session_enabled else "",
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
        key=lambda r: (
            r["score"],
            r["profit_factor"],
            r["total_pnl_usd"],
            -r["max_drawdown_pips"],
        ),
        reverse=True,
    )

    results_json = out_dir / "results_ranked.json"
    with results_json.open("w", encoding="utf-8") as handle:
        json.dump({"generated_at_utc": stamp, "results": results, "failures": failures}, handle, indent=2)

    results_csv = out_dir / "results_ranked.csv"
    fieldnames = [
        "rank",
        "case_id",
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
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, row in enumerate(results, start=1):
            out = {"rank": rank, **row}
            writer.writerow(out)

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
