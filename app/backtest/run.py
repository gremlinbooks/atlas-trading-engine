from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.engine.strategy import evaluate_candles


@dataclass
class TradeResult:
    entry_ts: str
    exit_ts: str
    side: str
    entry_price: float
    exit_price: float
    pnl_pips: float
    pnl_usd: float
    mae_pips: float


@dataclass
class EquityPoint:
    ts: str
    equity: float


def main() -> None:
    args = _parse_args()
    settings = get_settings()

    if not settings.oanda_api_key or not settings.oanda_account_id or not settings.oanda_env:
        raise SystemExit("OANDA_API_KEY, OANDA_ACCOUNT_ID, and OANDA_ENV are required for backtests")

    client = OandaClient()
    from_dt, to_dt = _resolve_date_range(args.days, args.from_date, args.to_date)
    candles = _fetch_candles(
        client=client,
        symbol=args.symbol,
        timeframe=args.timeframe,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    if len(candles) < 2:
        raise SystemExit("Not enough candles returned for backtest")

    trades, equity_curve = _run_backtest(
        candles=candles,
        symbol=args.symbol,
        timeframe=args.timeframe,
        units=args.units,
        spread_pips=args.spread_pips,
        fill=args.fill,
    )

    metrics = _print_summary(trades, equity_curve)
    _write_reports(
        symbol=args.symbol,
        timeframe=args.timeframe,
        trades=trades,
        equity=equity_curve,
        params={
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "from": _format_date(from_dt),
            "to": _format_date(to_dt),
            "days": args.days,
            "units": args.units,
            "spread_pips": args.spread_pips,
            "fill": args.fill,
        },
        metrics=metrics,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atlas backtest runner")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--units", type=int, default=1000)
    parser.add_argument("--spread_pips", type=float, default=1.2)
    parser.add_argument("--fill", choices=["close", "next_open"], default="next_open")
    return parser.parse_args()


def _fetch_candles(
    *,
    client: OandaClient,
    symbol: str,
    timeframe: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    start_ts = _to_rfc3339(from_dt)
    end_ts = _to_rfc3339(to_dt)

    all_candles: list[dict[str, Any]] = []
    include_first = True
    current_from = start_ts

    while True:
        batch = client.get_candles_range(
            symbol=symbol,
            granularity=timeframe,
            from_ts=current_from,
            to_ts=end_ts,
            count=5000,
            include_first=include_first,
        )
        if not batch:
            break
        all_candles.extend(batch)
        last_time = batch[-1]["time"]
        if last_time == current_from:
            break
        current_from = last_time
        include_first = False
        if len(batch) < 5000:
            break

    return all_candles


def _run_backtest(
    *,
    candles: list[dict[str, Any]],
    symbol: str,
    timeframe: str,
    units: int,
    spread_pips: float,
    fill: str,
) -> tuple[list[TradeResult], list[EquityPoint]]:
    trades: list[TradeResult] = []
    equity_curve: list[EquityPoint] = []

    position_side: Optional[str] = None
    entry_price: Optional[float] = None
    entry_ts: Optional[str] = None
    mae_pips: float = 0.0

    pip_factor = _pip_factor(symbol)
    balance = 0.0

    last_index = len(candles) - 1
    max_index = last_index if fill == "close" else last_index - 1

    for i in range(1, max_index + 1):
        signal, _, _ = evaluate_candles(candles[: i + 1])
        if signal == "HOLD":
            if position_side and entry_price is not None:
                mae_pips = _update_mae(candles[i], entry_price, position_side, pip_factor, mae_pips)
            continue

        if position_side is None:
            entry_price, entry_ts = _fill_price(candles, i, fill)
            position_side = signal
            mae_pips = 0.0
            continue

        if position_side == signal:
            mae_pips = _update_mae(candles[i], entry_price, position_side, pip_factor, mae_pips)
            continue

        exit_price, exit_ts = _fill_price(candles, i, fill)
        pnl_pips, pnl_usd = _calc_pnl(
            entry_price,
            exit_price,
            position_side,
            pip_factor,
            units,
            spread_pips,
        )
        trades.append(
            TradeResult(
                entry_ts=entry_ts or exit_ts,
                exit_ts=exit_ts,
                side=position_side,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                mae_pips=mae_pips,
            )
        )
        balance += pnl_usd
        equity_curve.append(EquityPoint(ts=exit_ts, equity=balance))

        entry_price, entry_ts = _fill_price(candles, i, fill)
        position_side = signal
        mae_pips = 0.0

    return trades, equity_curve


def _fill_price(candles: list[dict[str, Any]], index: int, fill: str) -> tuple[float, str]:
    if fill == "close":
        candle = candles[index]
        return float(candle["c"]), candle["time"]
    candle = candles[index + 1]
    return float(candle["o"]), candle["time"]


def _calc_pnl(
    entry_price: float,
    exit_price: float,
    side: str,
    pip_factor: float,
    units: int,
    spread_pips: float,
) -> tuple[float, float]:
    direction = 1 if side == "LONG" else -1
    raw_pips = (exit_price - entry_price) / pip_factor * direction
    pnl_pips = raw_pips - spread_pips
    raw_usd = (exit_price - entry_price) * units * direction
    spread_usd = spread_pips * pip_factor * units
    pnl_usd = raw_usd - spread_usd
    return pnl_pips, pnl_usd


def _update_mae(
    candle: dict[str, Any],
    entry_price: float,
    side: str,
    pip_factor: float,
    current_mae: float,
) -> float:
    if side == "LONG":
        adverse = (float(candle["l"]) - entry_price) / pip_factor
    else:
        adverse = (entry_price - float(candle["h"])) / pip_factor
    return min(current_mae, adverse)


def _print_summary(trades: list[TradeResult], equity: list[EquityPoint]) -> dict[str, float]:
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    losses = sum(1 for t in trades if t.pnl_usd < 0)
    win_rate = (wins / total * 100) if total else 0.0

    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = sum(t.pnl_usd for t in trades if t.pnl_usd < 0)
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss else 0.0

    avg_win = gross_profit / wins if wins else 0.0
    avg_loss = gross_loss / losses if losses else 0.0
    total_pnl = sum(t.pnl_usd for t in trades)
    total_pnl_pips = sum(t.pnl_pips for t in trades)
    max_dd = _max_drawdown([p.equity for p in equity])
    max_dd_pips = _max_drawdown(_equity_from_trades_pips(trades))
    sharpe = _sharpe_ratio([t.pnl_usd for t in trades])

    print("Backtest Summary")
    print(f"Total trades: {total}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Avg win: {avg_win:.4f} USD")
    print(f"Avg loss: {avg_loss:.4f} USD")
    print(f"Profit factor: {profit_factor:.4f}")
    print(f"Total PnL: {total_pnl:.4f} USD")
    print(f"Max drawdown: {max_dd:.4f} USD")
    print(f"Sharpe (per trade): {sharpe:.4f}")

    return {
        "total_trades": float(total),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_pnl_pips": total_pnl_pips,
        "total_pnl_usd": total_pnl,
        "max_drawdown_pips": max_dd_pips,
        "sharpe": sharpe,
    }


def _write_reports(
    symbol: str,
    timeframe: str,
    trades: list[TradeResult],
    equity: list[EquityPoint],
    params: dict[str, Any],
    metrics: dict[str, float],
) -> None:
    reports_dir = Path("./reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")

    trades_path = reports_dir / f"backtest_trades_{symbol}_{timeframe}_{date_tag}.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "entry_ts",
                "exit_ts",
                "side",
                "entry_price",
                "exit_price",
                "pnl_pips",
                "pnl_usd",
                "mae_pips",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.entry_ts,
                    trade.exit_ts,
                    trade.side,
                    f"{trade.entry_price:.5f}",
                    f"{trade.exit_price:.5f}",
                    f"{trade.pnl_pips:.2f}",
                    f"{trade.pnl_usd:.4f}",
                    f"{trade.mae_pips:.2f}",
                ]
            )

    equity_path = reports_dir / f"equity_{symbol}_{timeframe}_{date_tag}.csv"
    with equity_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "equity"])
        for point in equity:
            writer.writerow([point.ts, f"{point.equity:.4f}"])

    summary_path = reports_dir / f"summary_{symbol}_{timeframe}_{date_tag}.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump({"params": params, "metrics": metrics}, handle, indent=2)


def _max_drawdown(equity: Iterable[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = peak - value
        max_dd = max(max_dd, drawdown)
    return max_dd


def _equity_from_trades_pips(trades: list[TradeResult]) -> list[float]:
    equity = []
    running = 0.0
    for trade in trades:
        running += trade.pnl_pips
        equity.append(running)
    return equity


def _sharpe_ratio(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(len(returns))


def _pip_factor(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def _resolve_date_range(days: int, from_date: Optional[str], to_date: Optional[str]) -> tuple[datetime, datetime]:
    if from_date or to_date:
        if not from_date or not to_date:
            raise SystemExit("Both --from and --to must be provided together")
        from_dt = _parse_utc_date(from_date)
        to_dt = _parse_utc_date(to_date)
        if from_dt >= to_dt:
            raise SystemExit("--from must be earlier than --to")
        return from_dt, to_dt
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def _parse_utc_date(value: str) -> datetime:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("Date must be in YYYY-MM-DD format") from exc
    return dt.replace(tzinfo=timezone.utc)


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _to_rfc3339(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
