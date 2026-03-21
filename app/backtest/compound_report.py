from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


@dataclass
class CompoundedRow:
    row: dict[str, str]
    comp_units_opened: int
    comp_units_closed: int
    comp_pnl_usd: float
    comp_balance: float


def main() -> None:
    args = _parse_args()
    trade_path = _resolve_trade_path(args.trades_csv)
    symbol, timeframe, run_id = _parse_report_identity(trade_path)
    rows = _load_rows(trade_path)
    if not rows:
        raise SystemExit(f"No rows found in {trade_path}")

    compounded_rows, summary = _compound_rows(
        rows,
        starting_balance=args.starting_balance,
        margin_usage_pct=args.margin_usage_pct,
        margin_rate=args.margin_rate,
    )

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    trades_out = reports_dir / f"compounded_trades_{symbol}_{timeframe}_{run_id}.csv"
    equity_out = reports_dir / f"compounded_equity_{symbol}_{timeframe}_{run_id}.csv"
    summary_out = reports_dir / f"compounded_summary_{symbol}_{timeframe}_{run_id}.json"

    _write_compounded_trades(trades_out, compounded_rows)
    _write_compounded_equity(equity_out, compounded_rows)
    _write_summary(
        summary_out,
        source_path=trade_path,
        symbol=symbol,
        timeframe=timeframe,
        run_id=run_id,
        starting_balance=args.starting_balance,
        margin_usage_pct=args.margin_usage_pct,
        margin_rate=args.margin_rate,
        summary=summary,
        trades_csv=trades_out,
        equity_csv=equity_out,
    )
    _print_summary(summary, trades_out, equity_out, summary_out)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a compounded-position report from backtest trades")
    parser.add_argument("--trades_csv", help="Path to backtest_trades_*.csv; latest is used if omitted")
    parser.add_argument("--starting_balance", type=float, required=True)
    parser.add_argument("--margin_usage_pct", type=float, required=True)
    parser.add_argument("--margin_rate", type=float, required=True)
    return parser.parse_args()


def _resolve_trade_path(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if not path.exists():
            raise SystemExit(f"Trades CSV not found: {path}")
        return path
    reports = Path("reports")
    candidates = sorted(reports.glob("backtest_trades_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("No reports/backtest_trades_*.csv found")
    return candidates[0]


def _parse_report_identity(path: Path) -> tuple[str, str, str]:
    name = path.stem
    if not name.startswith("backtest_trades_"):
        now_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
        return "UNKNOWN", "UNKNOWN", now_tag
    rest = name.removeprefix("backtest_trades_")
    parts = rest.split("_")
    if len(parts) < 4:
        now_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
        return "UNKNOWN", "UNKNOWN", now_tag
    run_id = parts[-1]
    timeframe = parts[-2]
    symbol = "_".join(parts[:-2])
    return symbol, timeframe, run_id


def _load_rows(trade_path: Path) -> list[dict[str, str]]:
    with trade_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _compound_rows(
    rows: list[dict[str, str]],
    *,
    starting_balance: float,
    margin_usage_pct: float,
    margin_rate: float,
) -> tuple[list[CompoundedRow], dict[str, Any]]:
    if starting_balance <= 0:
        raise SystemExit("--starting_balance must be > 0")
    if not (0 < margin_usage_pct <= 100):
        raise SystemExit("--margin_usage_pct must be between 0 and 100")
    if margin_rate <= 0:
        raise SystemExit("--margin_rate must be > 0")

    balance = starting_balance
    position_units = 0
    position_opened = 0
    compounded_rows: list[CompoundedRow] = []
    trade_pnls: list[float] = []
    current_trade_pnl = 0.0
    peak_balance = balance
    max_drawdown = 0.0
    min_entry_units: int | None = None
    max_entry_units = 0

    for row in rows:
        leg = row.get("leg", "").strip().upper()
        entry_price = _to_float(row.get("entry_price", "0"))
        orig_units_opened = _to_int(row.get("units_opened", "0"))
        orig_units_closed = _to_int(row.get("units_closed", "0"))
        orig_pnl_usd = _to_float(row.get("pnl_usd", "0"))

        comp_units_opened = 0
        comp_units_closed = 0
        comp_pnl_usd = 0.0

        if leg in {"ENTRY", "FLIP_ENTRY"}:
            comp_units_opened = _compute_entry_units(
                balance=balance,
                margin_usage_pct=margin_usage_pct,
                entry_price=entry_price,
                margin_rate=margin_rate,
            )
            position_units = comp_units_opened
            position_opened = comp_units_opened
            current_trade_pnl = 0.0
            min_entry_units = comp_units_opened if min_entry_units is None else min(min_entry_units, comp_units_opened)
            max_entry_units = max(max_entry_units, comp_units_opened)
        else:
            comp_units_closed = _compute_closed_units(
                orig_units_opened=orig_units_opened,
                orig_units_closed=orig_units_closed,
                position_opened=position_opened,
                position_units=position_units,
            )
            if orig_units_closed > 0:
                comp_pnl_usd = orig_pnl_usd * (comp_units_closed / orig_units_closed)
            balance += comp_pnl_usd
            current_trade_pnl += comp_pnl_usd
            position_units = max(0, position_units - comp_units_closed)
            peak_balance = max(peak_balance, balance)
            max_drawdown = max(max_drawdown, peak_balance - balance)
            if position_units == 0 and position_opened > 0:
                trade_pnls.append(current_trade_pnl)
                position_opened = 0

        compounded_rows.append(
            CompoundedRow(
                row=row,
                comp_units_opened=comp_units_opened,
                comp_units_closed=comp_units_closed,
                comp_pnl_usd=comp_pnl_usd,
                comp_balance=balance,
            )
        )

    summary = _build_summary(
        starting_balance=starting_balance,
        ending_balance=balance,
        trade_pnls=trade_pnls,
        max_drawdown=max_drawdown,
        min_entry_units=min_entry_units or 0,
        max_entry_units=max_entry_units,
        source_rows=len(rows),
    )
    return compounded_rows, summary


def _compute_entry_units(*, balance: float, margin_usage_pct: float, entry_price: float, margin_rate: float) -> int:
    if entry_price <= 0:
        raise SystemExit("Encountered entry row with entry_price <= 0")
    target_margin = balance * (margin_usage_pct / 100.0)
    units = math.floor(target_margin / (entry_price * margin_rate))
    return max(units, 1)


def _compute_closed_units(
    *,
    orig_units_opened: int,
    orig_units_closed: int,
    position_opened: int,
    position_units: int,
) -> int:
    if position_units <= 0 or orig_units_closed <= 0:
        return 0
    if orig_units_opened <= 0 or orig_units_closed >= orig_units_opened:
        return position_units
    fraction = orig_units_closed / orig_units_opened
    units = max(1, round(position_opened * fraction))
    return min(units, position_units)


def _build_summary(
    *,
    starting_balance: float,
    ending_balance: float,
    trade_pnls: list[float],
    max_drawdown: float,
    min_entry_units: int,
    max_entry_units: int,
    source_rows: int,
) -> dict[str, Any]:
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "source_rows": source_rows,
        "completed_trades": len(trade_pnls),
        "starting_balance": round(starting_balance, 6),
        "ending_balance": round(ending_balance, 6),
        "net_pnl_usd": round(ending_balance - starting_balance, 6),
        "return_pct": round(((ending_balance / starting_balance) - 1.0) * 100.0, 6),
        "win_rate_pct": round((len(wins) / len(trade_pnls) * 100.0), 6) if trade_pnls else 0.0,
        "avg_win_usd": round(mean(wins), 6) if wins else 0.0,
        "avg_loss_usd": round(mean(losses), 6) if losses else 0.0,
        "profit_factor": round((gross_win / gross_loss), 6) if gross_loss > 0 else 0.0,
        "max_drawdown_usd": round(max_drawdown, 6),
        "min_entry_units": min_entry_units,
        "max_entry_units": max_entry_units,
    }


def _write_compounded_trades(path: Path, rows: list[CompoundedRow]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].row.keys()) + [
        "comp_units_opened",
        "comp_units_closed",
        "comp_pnl_usd",
        "comp_balance",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows:
            out = dict(item.row)
            out["comp_units_opened"] = str(item.comp_units_opened)
            out["comp_units_closed"] = str(item.comp_units_closed)
            out["comp_pnl_usd"] = f"{item.comp_pnl_usd:.6f}"
            out["comp_balance"] = f"{item.comp_balance:.6f}"
            writer.writerow(out)


def _write_compounded_equity(path: Path, rows: list[CompoundedRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "comp_balance"])
        for item in rows:
            if item.comp_units_closed <= 0:
                continue
            writer.writerow([item.row.get("exit_ts", ""), f"{item.comp_balance:.6f}"])


def _write_summary(
    path: Path,
    *,
    source_path: Path,
    symbol: str,
    timeframe: str,
    run_id: str,
    starting_balance: float,
    margin_usage_pct: float,
    margin_rate: float,
    summary: dict[str, Any],
    trades_csv: Path,
    equity_csv: Path,
) -> None:
    payload = {
        "source": {
            "trades_csv": str(source_path),
            "symbol": symbol,
            "timeframe": timeframe,
            "run_id": run_id,
        },
        "assumptions": {
            "starting_balance": starting_balance,
            "margin_usage_pct": margin_usage_pct,
            "margin_rate": margin_rate,
        },
        "outputs": {
            "compounded_trades_csv": str(trades_csv),
            "compounded_equity_csv": str(equity_csv),
        },
        "metrics": summary,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _print_summary(summary: dict[str, Any], trades_out: Path, equity_out: Path, summary_out: Path) -> None:
    print("Compounded Summary")
    print(f"Completed trades: {summary['completed_trades']}")
    print(f"Ending balance: {summary['ending_balance']:.4f}")
    print(f"Net PnL USD: {summary['net_pnl_usd']:.4f}")
    print(f"Return: {summary['return_pct']:.2f}%")
    print(f"Win rate: {summary['win_rate_pct']:.2f}%")
    print(f"Profit factor: {summary['profit_factor']:.4f}")
    print(f"Max drawdown: {summary['max_drawdown_usd']:.4f}")
    print(f"Entry size range: {summary['min_entry_units']} to {summary['max_entry_units']} units")
    print(f"Compounded trades CSV: {trades_out}")
    print(f"Compounded equity CSV: {equity_out}")
    print(f"Compounded summary JSON: {summary_out}")


def _to_float(value: str) -> float:
    return float(value or 0)


def _to_int(value: str) -> int:
    return int(float(value or 0))


if __name__ == "__main__":
    main()
