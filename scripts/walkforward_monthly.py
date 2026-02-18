#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT_ROOT = REPORTS / "walkforward"


@dataclass(frozen=True)
class MonthWindow:
    label: str
    from_dt: str
    to_dt: str


def month_windows(start: date, end: date) -> list[MonthWindow]:
    windows: list[MonthWindow] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first_day = date(y, m, 1)
        last_day_num = calendar.monthrange(y, m)[1]
        last_day = date(y, m, last_day_num)
        from_day = start if (y, m) == (start.year, start.month) else first_day
        to_day = end if (y, m) == (end.year, end.month) else last_day
        windows.append(
            MonthWindow(
                label=f"{y:04d}-{m:02d}",
                from_dt=f"{from_day.isoformat()}T00:00:00",
                to_dt=f"{to_day.isoformat()}T23:59:00",
            )
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return windows


def latest_file(pattern: str) -> Path:
    matches = [Path(p) for p in glob.glob(pattern)]
    if not matches:
        raise FileNotFoundError(pattern)
    return max(matches, key=lambda p: p.stat().st_mtime)


def score_pass(row: dict, cfg: argparse.Namespace) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if row["profit_factor"] < cfg.min_profit_factor:
        reasons.append(f"pf<{cfg.min_profit_factor}")
    if row["total_pnl_usd"] <= 0:
        reasons.append("pnl<=0")
    if row["max_drawdown_pips"] > cfg.max_drawdown_pips:
        reasons.append(f"dd>{cfg.max_drawdown_pips}")
    if row["tp1_hit_pct"] < cfg.min_tp1_hit_pct:
        reasons.append(f"tp1<{cfg.min_tp1_hit_pct}")
    return (len(reasons) == 0), reasons


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly walk-forward backtest matrix")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbol", default="AUD_USD")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--units", type=int, default=7000)
    ap.add_argument("--spread_pips", type=float, default=1.4)
    ap.add_argument("--tp1_pips", type=int, default=20)
    ap.add_argument("--sl_pips", type=int, default=28)
    ap.add_argument("--tp1_close_pct", type=int, default=40)
    ap.add_argument("--trail_drawdown_pct", type=float, default=1.0)
    ap.add_argument("--be_lock_pips", type=int, default=25)
    ap.add_argument("--exec_profile", choices=["live_reality", "tv_panel"], default="live_reality")
    ap.add_argument("--magnifier", choices=["m1", "off"], default="m1")
    ap.add_argument("--entry_timing", choices=["close", "intrabar"], default="close")
    ap.add_argument("--use_bid_ask", choices=["true", "false"], default="true")
    ap.add_argument("--use_runner", choices=["true", "false"], default="true")
    ap.add_argument("--use_stoch_exit", choices=["true", "false"], default="true")
    ap.add_argument("--bar_fill_policy", choices=["conservative", "optimistic"], default="conservative")

    # Strategy env controls
    ap.add_argument("--strategy_enabled", default="true")
    ap.add_argument("--strategy_min_hold_bars", type=int, default=3)
    ap.add_argument("--strategy_use_bias", default="true")
    ap.add_argument("--strategy_stoch_entry_mode", default="StrictFilter")
    ap.add_argument("--strategy_st_recent", type=int, default=3)
    ap.add_argument("--strategy_cont_enabled", default="false")
    ap.add_argument("--strategy_block_trades", default="true")
    ap.add_argument("--strategy_block_session", default="2200-2330")
    ap.add_argument("--profit_floor1_trigger_pips", type=int, default=10)
    ap.add_argument("--profit_floor1_lock_pips", type=int, default=10)
    ap.add_argument("--profit_floor2_trigger_pips", type=int, default=15)
    ap.add_argument("--profit_floor2_lock_pips", type=int, default=15)

    # Pass/fail thresholds
    ap.add_argument("--min_profit_factor", type=float, default=1.10)
    ap.add_argument("--max_drawdown_pips", type=float, default=200.0)
    ap.add_argument("--min_tp1_hit_pct", type=float, default=15.0)
    cfg = ap.parse_args()

    start = date.fromisoformat(cfg.start)
    end = date.fromisoformat(cfg.end)
    windows = month_windows(start, end)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"{cfg.symbol}_{cfg.timeframe}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["STRATEGY_ENABLED"] = cfg.strategy_enabled
    env["STRATEGY_MIN_HOLD_BARS"] = str(cfg.strategy_min_hold_bars)
    env["STRATEGY_USE_BIAS"] = cfg.strategy_use_bias
    env["STRATEGY_STOCH_ENTRY_MODE"] = cfg.strategy_stoch_entry_mode
    env["STRATEGY_ST_RECENT"] = str(cfg.strategy_st_recent)
    env["STRATEGY_CONT_ENABLED"] = cfg.strategy_cont_enabled
    env["STRATEGY_BLOCK_TRADES"] = cfg.strategy_block_trades
    env["STRATEGY_BLOCK_SESSION"] = cfg.strategy_block_session
    env["STRATEGY_PROFIT_FLOOR1_TRIGGER_PIPS"] = str(cfg.profit_floor1_trigger_pips)
    env["STRATEGY_PROFIT_FLOOR1_LOCK_PIPS"] = str(cfg.profit_floor1_lock_pips)
    env["STRATEGY_PROFIT_FLOOR2_TRIGGER_PIPS"] = str(cfg.profit_floor2_trigger_pips)
    env["STRATEGY_PROFIT_FLOOR2_LOCK_PIPS"] = str(cfg.profit_floor2_lock_pips)

    rows: list[dict] = []
    for i, w in enumerate(windows, start=1):
        print(f"[{i}/{len(windows)}] {w.label} {w.from_dt} -> {w.to_dt}")
        cmd = [
            sys.executable, "-m", "app.backtest.run",
            "--symbol", cfg.symbol,
            "--timeframe", cfg.timeframe,
            "--from", w.from_dt,
            "--to", w.to_dt,
            "--units", str(cfg.units),
            "--spread_pips", str(cfg.spread_pips),
            "--tp1_pips", str(cfg.tp1_pips),
            "--sl_pips", str(cfg.sl_pips),
            "--tp1_close_pct", str(cfg.tp1_close_pct),
            "--trail_drawdown_pct", str(cfg.trail_drawdown_pct),
            "--be_lock_pips", str(cfg.be_lock_pips),
            "--bar_fill_policy", cfg.bar_fill_policy,
            "--use_runner", cfg.use_runner,
            "--use_stoch_exit", cfg.use_stoch_exit,
            "--exec_profile", cfg.exec_profile,
            "--magnifier", cfg.magnifier,
            "--use_bid_ask", cfg.use_bid_ask,
            "--entry_timing", cfg.entry_timing,
        ]
        log_path = out_dir / f"{w.label}.log"
        with log_path.open("w", encoding="utf-8") as lf:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            print(f"  FAILED {w.label} (see {log_path})")
            rows.append({"month": w.label, "pass": False, "fail_reasons": "run_failed"})
            continue

        summary = latest_file(str(REPORTS / f"summary_{cfg.symbol}_{cfg.timeframe}_*.json"))
        trades = latest_file(str(REPORTS / f"backtest_trades_{cfg.symbol}_{cfg.timeframe}_*.csv"))
        equity = latest_file(str(REPORTS / f"equity_{cfg.symbol}_{cfg.timeframe}_*.csv"))
        shutil.copy2(summary, out_dir / f"{w.label}_summary.json")
        shutil.copy2(trades, out_dir / f"{w.label}_trades.csv")
        shutil.copy2(equity, out_dir / f"{w.label}_equity.csv")

        payload = json.load(open(summary, encoding="utf-8"))
        m = payload["metrics"]
        row = {
            "month": w.label,
            "total_trades": float(m.get("total_trades", 0)),
            "win_rate": float(m.get("win_rate", 0)),
            "profit_factor": float(m.get("profit_factor", 0)),
            "total_pnl_usd": float(m.get("total_pnl_usd", 0)),
            "max_drawdown_pips": float(m.get("max_drawdown_pips", 0)),
            "tp1_hit_pct": float(m.get("tp1_hit_pct", 0)),
            "runner_pnl_usd": float(m.get("runner_pnl_usd", 0)),
            "num_stopouts": float(m.get("num_stopouts", 0)),
            "sharpe": float(m.get("sharpe", 0)),
        }
        ok, reasons = score_pass(row, cfg)
        row["pass"] = ok
        row["fail_reasons"] = "" if ok else ";".join(reasons)
        rows.append(row)
        print(
            f"  {'PASS' if ok else 'FAIL'} PF={row['profit_factor']:.3f} "
            f"PnL={row['total_pnl_usd']:.2f} DD={row['max_drawdown_pips']:.2f} "
            f"TP1={row['tp1_hit_pct']:.2f}% reasons={row['fail_reasons']}"
        )

    pass_count = sum(1 for r in rows if r.get("pass") is True)
    total_count = len(rows)
    pass_rate = (pass_count / total_count * 100.0) if total_count else 0.0
    pnl_sum = sum(float(r.get("total_pnl_usd", 0.0) or 0.0) for r in rows)

    ranked = sorted(rows, key=lambda r: (r.get("profit_factor", 0.0), r.get("total_pnl_usd", 0.0)), reverse=True)
    with (out_dir / "walkforward_results.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at_utc": stamp,
                "params": vars(cfg),
                "summary": {
                    "months": total_count,
                    "passed_months": pass_count,
                    "pass_rate_pct": pass_rate,
                    "total_pnl_usd_sum": pnl_sum,
                },
                "results": ranked,
            },
            f,
            indent=2,
        )

    fields = [
        "month", "pass", "fail_reasons", "total_trades", "win_rate", "profit_factor",
        "total_pnl_usd", "max_drawdown_pips", "tp1_hit_pct", "runner_pnl_usd", "num_stopouts", "sharpe",
    ]
    with (out_dir / "walkforward_results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in ranked:
            w.writerow(r)

    print("")
    print(f"Walk-forward complete: {out_dir}")
    print(f"Months passed: {pass_count}/{total_count} ({pass_rate:.1f}%)")
    print(f"Total PnL sum: {pnl_sum:.2f} USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
