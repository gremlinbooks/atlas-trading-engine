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
from app.engine.strategy_base import (
    Candle,
    PositionState,
    StrategyConfig,
    StrategyContext,
    StrategyDecision,
    StrategyState,
    get_strategy,
)


@dataclass
class TradeResult:
    entry_ts: str
    exit_ts: str
    side: str
    units_opened: float
    units_closed: float
    entry_price: float
    exit_price: float
    leg: str
    reason: str
    pnl_pips: float
    pnl_usd: float
    mae_pips: float
    equity: float
    hold_bars: int


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
    strategy = get_strategy(settings.strategy_name)
    strategy_config = StrategyConfig(
        timeframe=args.timeframe,
        min_hold_bars=settings.strategy_min_hold_bars,
        trend_ema_period=settings.strategy_trend_ema_period,
        enabled=settings.strategy_enabled,
        fast_len=settings.strategy_fast_len,
        slow_len=settings.strategy_slow_len,
        use_bias=settings.strategy_use_bias,
        invert_eurcad=settings.strategy_invert_eurcad,
        force_flip=settings.strategy_force_flip,
        tp1_pips=args.tp1_pips,
        sl_pips=args.sl_pips,
        tp1_close_pct=args.tp1_close_pct,
        trail_drawdown_pct=args.trail_drawdown_pct,
        be_lock_pips=args.be_lock_pips,
        stoch_entry_mode=settings.strategy_stoch_entry_mode,
        use_stoch_exit=args.use_stoch_exit,
        st_rsi_len=settings.strategy_st_rsi_len,
        st_stoch_len=settings.strategy_st_stoch_len,
        st_k_len=settings.strategy_st_k_len,
        st_d_len=settings.strategy_st_d_len,
        st_ob=settings.strategy_st_ob,
        st_os=settings.strategy_st_os,
        st_recent=settings.strategy_st_recent,
        st_tight_pips=args.st_tight_pips,
        block_trades=settings.strategy_block_trades,
        block_session=settings.strategy_block_session,
        quick_relax=settings.strategy_quick_relax,
        use_day_mask=settings.strategy_use_day_mask,
        block_mon=settings.strategy_block_mon,
        block_tue=settings.strategy_block_tue,
        block_wed=settings.strategy_block_wed,
        block_thu=settings.strategy_block_thu,
        block_fri=settings.strategy_block_fri,
        block_sat=settings.strategy_block_sat,
        block_sun=settings.strategy_block_sun,
        use_spread_gate=settings.strategy_use_spread_gate,
        max_spread_pips=settings.strategy_max_spread_pips,
        aggr_spread_factor=settings.strategy_aggr_spread_factor,
        hold_signal_bars=settings.strategy_hold_signal_bars,
        apply_on_history=settings.strategy_apply_on_history,
        pb_enabled=settings.strategy_pb_enabled,
        pb_lookback_bars=settings.strategy_pb_lookback_bars,
        cont_enabled=settings.strategy_cont_enabled,
        base_max_bars=settings.strategy_base_max_bars,
        base_max_range_atr=settings.strategy_base_max_range_atr,
        allow_second_chance=settings.strategy_allow_second_chance,
        reenter_within_bars=settings.strategy_reenter_within_bars,
    )
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

    trades, equity_curve, metrics_extra = _run_backtest(
        candles=candles,
        symbol=args.symbol,
        timeframe=args.timeframe,
        units=args.units,
        spread_pips=args.spread_pips,
        fill=args.fill,
        bar_fill_policy=args.bar_fill_policy,
        use_runner=args.use_runner,
        strategy=strategy,
        strategy_config=strategy_config,
    )

    metrics = _print_summary(trades, equity_curve, metrics_extra)
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
            "tp1_pips": args.tp1_pips,
            "sl_pips": args.sl_pips,
            "tp1_close_pct": args.tp1_close_pct,
            "trail_drawdown_pct": args.trail_drawdown_pct,
            "be_lock_pips": args.be_lock_pips,
            "bar_fill_policy": args.bar_fill_policy,
            "use_runner": args.use_runner,
            "use_stoch_exit": args.use_stoch_exit,
            "st_tight_pips": args.st_tight_pips,
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
    parser.add_argument("--tp1_pips", type=int, default=20)
    parser.add_argument("--sl_pips", type=int, default=28)
    parser.add_argument("--tp1_close_pct", type=int, default=30)
    parser.add_argument("--trail_drawdown_pct", type=float, default=2.0)
    parser.add_argument("--be_lock_pips", type=int, default=20)
    parser.add_argument(
        "--bar_fill_policy",
        choices=["conservative", "optimistic"],
        default="conservative",
    )
    parser.add_argument("--use_runner", type=_parse_bool, default=True)
    parser.add_argument("--use_stoch_exit", type=_parse_bool, default=False)
    parser.add_argument("--st_tight_pips", type=int, default=6)
    return parser.parse_args()


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fetch_candles(
    *,
    client: OandaClient,
    symbol: str,
    timeframe: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    timeframe_minutes = _timeframe_to_minutes(timeframe)
    chunk_minutes = 5000 * timeframe_minutes
    all_candles: dict[str, dict[str, Any]] = {}

    current = from_dt
    while current < to_dt:
        chunk_to = min(current + timedelta(minutes=chunk_minutes), to_dt)
        batch = client.get_candles_range(
            symbol=symbol,
            granularity=timeframe,
            from_ts=_to_rfc3339(current),
            to_ts=_to_rfc3339(chunk_to),
            count=None,
            include_first=True,
        )
        for candle in batch:
            all_candles[candle["time"]] = candle
        print(
            f"fetching candles chunk: {_format_date(current)} -> {_format_date(chunk_to)} (n={len(batch)})"
        )
        current = chunk_to

    return [all_candles[k] for k in sorted(all_candles)]


def _run_backtest(
    *,
    candles: list[dict[str, Any]],
    symbol: str,
    timeframe: str,
    units: int,
    spread_pips: float,
    fill: str,
    bar_fill_policy: str,
    use_runner: bool,
    strategy: Any,
    strategy_config: StrategyConfig,
) -> tuple[list[TradeResult], list[EquityPoint], dict[str, float]]:
    trades: list[TradeResult] = []
    equity_curve: list[EquityPoint] = []

    position_side: Optional[str] = None
    entry_price: Optional[float] = None
    entry_ts: Optional[str] = None
    entry_index: Optional[int] = None
    position_units_opened: float = 0.0
    position_units_remaining: float = 0.0
    mae_pips: float = 0.0
    tp1_reached = False
    long_peak: Optional[float] = None
    short_trough: Optional[float] = None

    tp1_hit_trades = 0
    total_trades = 0
    total_hold_bars = 0
    runner_pnl = 0.0

    strategy_state = StrategyState()

    pip_factor = _pip_factor(symbol)
    spread_price = spread_pips * pip_factor
    balance = 0.0

    last_index = len(candles) - 1
    max_index = last_index if fill == "close" else last_index - 1

    for i in range(1, max_index + 1):
        candle_slice = [
            Candle(
                ts=c["time"],
                o=float(c["o"]),
                h=float(c["h"]),
                l=float(c["l"]),
                c=float(c["c"]),
                volume=int(c["volume"]),
            )
            for c in candles[: i + 1]
        ]
        decision: StrategyDecision = strategy.evaluate(
            candle_slice,
            StrategyContext(
                symbol=symbol,
                timeframe=timeframe,
                position=PositionState(
                    side=position_side,
                    units=position_units_remaining,
                    avg_price=entry_price or 0.0,
                    entry_ts=entry_ts,
                ),
                config=strategy_config,
                state=strategy_state,
                bar_index=i,
                spread_pips=None,
                spread_available=False,
                is_realtime=False,
            ),
        )
        if decision.next_state is not None:
            strategy_state = decision.next_state

        signal = _decision_signal(decision)
        st_meta = decision.metadata.get("stoch", {}) if isinstance(decision.metadata, dict) else {}

        candle = candles[i]
        candle_high = float(candle["h"])
        candle_low = float(candle["l"])
        candle_close = float(candle["c"])

        if position_side and entry_price is not None:
            mae_pips = _update_mae(candle, entry_price, position_side, pip_factor, mae_pips)

        if position_side and entry_price is not None:
            tp1_price, sl_price = _tp1_sl_prices(
                position_side, entry_price, strategy_config.tp1_pips, strategy_config.sl_pips, pip_factor
            )
            tp1_hit = _tp1_hit(position_side, candle_high, candle_low, tp1_price)
            sl_hit = _sl_hit(position_side, candle_high, candle_low, sl_price)

            if tp1_hit and sl_hit and not tp1_reached:
                if bar_fill_policy == "conservative":
                    sl_hit = True
                    tp1_hit = False
                else:
                    tp1_hit = True
                    sl_hit = False

            if sl_hit and not tp1_reached:
                exit_price = _exit_price(sl_price, position_side, spread_price)
                balance, trades = _record_exit(
                    trades,
                    equity_curve,
                    balance,
                    entry_ts,
                    candle["time"],
                    position_side,
                    position_units_opened,
                    position_units_remaining,
                    entry_price,
                    exit_price,
                    "EXIT",
                    "SL",
                    pip_factor,
                    mae_pips,
                    i,
                    entry_index,
                )
                total_trades += 1
                total_hold_bars += i - (entry_index or i)
                position_side, entry_price, entry_ts, entry_index = None, None, None, None
                position_units_opened = 0.0
                position_units_remaining = 0.0
                tp1_reached = False
                long_peak = None
                short_trough = None
                mae_pips = 0.0
                continue

            if tp1_hit and position_units_remaining > 0 and not tp1_reached:
                tp1_reached = True
                tp1_hit_trades += 1
                tp1_price = _exit_price(tp1_price, position_side, spread_price)
                units_closed = position_units_opened * (strategy_config.tp1_close_pct / 100)
                units_closed = min(units_closed, position_units_remaining)
                balance, trades = _record_exit(
                    trades,
                    equity_curve,
                    balance,
                    entry_ts,
                    candle["time"],
                    position_side,
                    position_units_opened,
                    units_closed,
                    entry_price,
                    tp1_price,
                    "TP1",
                    "TP1",
                    pip_factor,
                    mae_pips,
                    i,
                    entry_index,
                )
                position_units_remaining -= units_closed
                if position_side == "LONG":
                    long_peak = candle_high
                else:
                    short_trough = candle_low
                if not use_runner or position_units_remaining <= 0:
                    balance, trades = _record_exit(
                        trades,
                        equity_curve,
                        balance,
                        entry_ts,
                        candle["time"],
                        position_side,
                        position_units_opened,
                        position_units_remaining,
                        entry_price,
                        tp1_price,
                        "EXIT",
                        "TP1_FULL",
                        pip_factor,
                        mae_pips,
                        i,
                        entry_index,
                    )
                    total_trades += 1
                    total_hold_bars += i - (entry_index or i)
                    position_side, entry_price, entry_ts, entry_index = None, None, None, None
                    position_units_opened = 0.0
                    position_units_remaining = 0.0
                    tp1_reached = False
                    long_peak = None
                    short_trough = None
                    mae_pips = 0.0
                    continue

            if tp1_reached and position_units_remaining > 0:
                if position_side == "LONG":
                    long_peak = max(long_peak or candle_high, candle_high)
                    trail_candidate = long_peak * (1 - strategy_config.trail_drawdown_pct / 100)
                    be_stop = entry_price + strategy_config.be_lock_pips * pip_factor
                    runner_stop = max(be_stop, trail_candidate)
                    if strategy_config.use_stoch_exit and st_meta.get("stKxDn") and st_meta.get("k", 0) > strategy_config.st_ob:
                        runner_stop = max(runner_stop, candle_close - strategy_config.st_tight_pips * pip_factor)
                    if candle_low <= runner_stop:
                        exit_price = _exit_price(runner_stop, position_side, spread_price)
                        balance, trades = _record_exit(
                            trades,
                            equity_curve,
                            balance,
                            entry_ts,
                            candle["time"],
                            position_side,
                            position_units_opened,
                            position_units_remaining,
                            entry_price,
                            exit_price,
                            "RUNNER",
                            "RUNNER_STOP",
                            pip_factor,
                            mae_pips,
                            i,
                            entry_index,
                        )
                        runner_pnl += trades[-1].pnl_usd
                        total_trades += 1
                        total_hold_bars += i - (entry_index or i)
                        position_side, entry_price, entry_ts, entry_index = None, None, None, None
                        position_units_opened = 0.0
                        position_units_remaining = 0.0
                        tp1_reached = False
                        long_peak = None
                        short_trough = None
                        mae_pips = 0.0
                        continue
                else:
                    short_trough = min(short_trough or candle_low, candle_low)
                    trail_candidate = short_trough * (1 + strategy_config.trail_drawdown_pct / 100)
                    be_stop = entry_price - strategy_config.be_lock_pips * pip_factor
                    runner_stop = min(be_stop, trail_candidate)
                    if strategy_config.use_stoch_exit and st_meta.get("stKxUp") and st_meta.get("k", 0) < strategy_config.st_os:
                        runner_stop = min(runner_stop, candle_close + strategy_config.st_tight_pips * pip_factor)
                    if candle_high >= runner_stop:
                        exit_price = _exit_price(runner_stop, position_side, spread_price)
                        balance, trades = _record_exit(
                            trades,
                            equity_curve,
                            balance,
                            entry_ts,
                            candle["time"],
                            position_side,
                            position_units_opened,
                            position_units_remaining,
                            entry_price,
                            exit_price,
                            "RUNNER",
                            "RUNNER_STOP",
                            pip_factor,
                            mae_pips,
                            i,
                            entry_index,
                        )
                        runner_pnl += trades[-1].pnl_usd
                        total_trades += 1
                        total_hold_bars += i - (entry_index or i)
                        position_side, entry_price, entry_ts, entry_index = None, None, None, None
                        position_units_opened = 0.0
                        position_units_remaining = 0.0
                        tp1_reached = False
                        long_peak = None
                        short_trough = None
                        mae_pips = 0.0
                        continue

        if signal and position_side is None:
            entry_price, entry_ts = _entry_fill(candles, i, fill, signal, spread_price)
            entry_index = i
            position_side = signal
            position_units_opened = float(units)
            position_units_remaining = float(units)
            tp1_reached = False
            long_peak = None
            short_trough = None
            mae_pips = 0.0
            trades.append(
                TradeResult(
                    entry_ts=entry_ts,
                    exit_ts=entry_ts,
                    side=position_side,
                    units_opened=position_units_opened,
                    units_closed=0.0,
                    entry_price=entry_price,
                    exit_price=entry_price,
                    leg="ENTRY",
                    reason="ENTRY",
                    pnl_pips=0.0,
                    pnl_usd=0.0,
                    mae_pips=0.0,
                    equity=balance,
                    hold_bars=0,
                )
            )
            continue

        if signal and position_side and signal != position_side:
            exit_price, exit_ts = _flip_exit_fill(candles, i, fill, position_side, spread_price)
            balance, trades = _record_exit(
                trades,
                equity_curve,
                balance,
                entry_ts,
                exit_ts,
                position_side,
                position_units_opened,
                position_units_remaining,
                entry_price,
                exit_price,
                "EXIT",
                "FLIP",
                pip_factor,
                mae_pips,
                i,
                entry_index,
            )
            total_trades += 1
            total_hold_bars += i - (entry_index or i)
            entry_price, entry_ts = _entry_fill(candles, i, fill, signal, spread_price)
            entry_index = i
            position_side = signal
            position_units_opened = float(units)
            position_units_remaining = float(units)
            tp1_reached = False
            long_peak = None
            short_trough = None
            mae_pips = 0.0
            trades.append(
                TradeResult(
                    entry_ts=entry_ts,
                    exit_ts=entry_ts,
                    side=position_side,
                    units_opened=position_units_opened,
                    units_closed=0.0,
                    entry_price=entry_price,
                    exit_price=entry_price,
                    leg="ENTRY",
                    reason="FLIP_ENTRY",
                    pnl_pips=0.0,
                    pnl_usd=0.0,
                    mae_pips=0.0,
                    equity=balance,
                    hold_bars=0,
                )
            )

    avg_hold = (total_hold_bars / total_trades) if total_trades else 0.0
    tp1_pct = (tp1_hit_trades / total_trades * 100) if total_trades else 0.0
    metrics_extra = {
        "avg_hold_bars": avg_hold,
        "tp1_hit_pct": tp1_pct,
        "runner_pnl_usd": runner_pnl,
    }
    return trades, equity_curve, metrics_extra


def _decision_signal(decision: StrategyDecision) -> Optional[str]:
    if decision.action in {"ENTER_LONG", "FLIP_LONG"}:
        return "LONG"
    if decision.action in {"ENTER_SHORT", "FLIP_SHORT"}:
        return "SHORT"
    return None


def _entry_fill(
    candles: list[dict[str, Any]],
    index: int,
    fill: str,
    side: str,
    spread_price: float,
) -> tuple[float, str]:
    base_price, ts = _fill_price(candles, index, fill)
    if side == "LONG":
        return base_price + spread_price / 2, ts
    return base_price - spread_price / 2, ts


def _flip_exit_fill(
    candles: list[dict[str, Any]],
    index: int,
    fill: str,
    side: str,
    spread_price: float,
) -> tuple[float, str]:
    base_price, ts = _fill_price(candles, index, fill)
    return _exit_price(base_price, side, spread_price), ts


def _fill_price(candles: list[dict[str, Any]], index: int, fill: str) -> tuple[float, str]:
    if fill == "close":
        candle = candles[index]
        return float(candle["c"]), candle["time"]
    candle = candles[index + 1]
    return float(candle["o"]), candle["time"]


def _exit_price(price: float, side: str, spread_price: float) -> float:
    if side == "LONG":
        return price - spread_price / 2
    return price + spread_price / 2


def _tp1_sl_prices(side: str, avg_price: float, tp1_pips: int, sl_pips: int, pip_factor: float) -> tuple[float, float]:
    if side == "LONG":
        return avg_price + tp1_pips * pip_factor, avg_price - sl_pips * pip_factor
    return avg_price - tp1_pips * pip_factor, avg_price + sl_pips * pip_factor


def _tp1_hit(side: str, high: float, low: float, tp1_price: float) -> bool:
    if side == "LONG":
        return high >= tp1_price
    return low <= tp1_price


def _sl_hit(side: str, high: float, low: float, sl_price: float) -> bool:
    if side == "LONG":
        return low <= sl_price
    return high >= sl_price


def _record_exit(
    trades: list[TradeResult],
    equity_curve: list[EquityPoint],
    balance: float,
    entry_ts: Optional[str],
    exit_ts: str,
    side: str,
    units_opened: float,
    units_closed: float,
    entry_price: float,
    exit_price: float,
    leg: str,
    reason: str,
    pip_factor: float,
    mae_pips: float,
    bar_index: int,
    entry_index: Optional[int],
) -> tuple[float, list[TradeResult]]:
    pnl_pips, pnl_usd = _calc_pnl(
        entry_price,
        exit_price,
        side,
        pip_factor,
        units_closed,
        units_opened,
    )
    balance += pnl_usd
    equity_curve.append(EquityPoint(ts=exit_ts, equity=balance))
    trades.append(
        TradeResult(
            entry_ts=entry_ts or exit_ts,
            exit_ts=exit_ts,
            side=side,
            units_opened=units_opened,
            units_closed=units_closed,
            entry_price=entry_price,
            exit_price=exit_price,
            leg=leg,
            reason=reason,
            pnl_pips=pnl_pips,
            pnl_usd=pnl_usd,
            mae_pips=mae_pips,
            equity=balance,
            hold_bars=bar_index - (entry_index or bar_index),
        )
    )
    return balance, trades


def _calc_pnl(
    entry_price: float,
    exit_price: float,
    side: str,
    pip_factor: float,
    units: float,
    total_units: float,
) -> tuple[float, float]:
    direction = 1 if side == "LONG" else -1
    raw_pips = (exit_price - entry_price) / pip_factor * direction
    pnl_pips = raw_pips * (units / total_units) if total_units else 0.0
    raw_usd = (exit_price - entry_price) * units * direction
    return pnl_pips, raw_usd


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


def _print_summary(
    trades: list[TradeResult],
    equity: list[EquityPoint],
    extra: dict[str, float],
) -> dict[str, float]:
    closed_trades = [t for t in trades if t.leg != "ENTRY"]
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t.pnl_usd > 0)
    losses = sum(1 for t in closed_trades if t.pnl_usd < 0)
    win_rate = (wins / total * 100) if total else 0.0

    gross_profit = sum(t.pnl_usd for t in closed_trades if t.pnl_usd > 0)
    gross_loss = sum(t.pnl_usd for t in closed_trades if t.pnl_usd < 0)
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss else 0.0

    avg_win = gross_profit / wins if wins else 0.0
    avg_loss = gross_loss / losses if losses else 0.0
    total_pnl = sum(t.pnl_usd for t in closed_trades)
    total_pnl_pips = sum(t.pnl_pips for t in closed_trades)
    max_dd = _max_drawdown([p.equity for p in equity])
    max_dd_pips = _max_drawdown(_equity_from_trades_pips(closed_trades))
    sharpe = _sharpe_ratio([t.pnl_usd for t in closed_trades])

    print("Backtest Summary")
    print(f"Total trades: {total}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Avg win: {avg_win:.4f} USD")
    print(f"Avg loss: {avg_loss:.4f} USD")
    print(f"Profit factor: {profit_factor:.4f}")
    print(f"Total PnL: {total_pnl:.4f} USD")
    print(f"Max drawdown: {max_dd:.4f} USD")
    print(f"Sharpe (per trade): {sharpe:.4f}")
    print(f"Avg hold bars: {extra.get('avg_hold_bars', 0):.2f}")
    print(f"TP1 hit rate: {extra.get('tp1_hit_pct', 0):.2f}%")
    print(f"Runner PnL: {extra.get('runner_pnl_usd', 0):.4f} USD")

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
        "avg_hold_bars": extra.get("avg_hold_bars", 0.0),
        "tp1_hit_pct": extra.get("tp1_hit_pct", 0.0),
        "runner_pnl_usd": extra.get("runner_pnl_usd", 0.0),
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
                "units_opened",
                "units_closed",
                "entry_price",
                "exit_price",
                "leg",
                "reason",
                "pnl_pips",
                "pnl_usd",
                "mae_pips",
                "equity",
                "hold_bars",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.entry_ts,
                    trade.exit_ts,
                    trade.side,
                    f"{trade.units_opened:.2f}",
                    f"{trade.units_closed:.2f}",
                    f"{trade.entry_price:.5f}",
                    f"{trade.exit_price:.5f}",
                    trade.leg,
                    trade.reason,
                    f"{trade.pnl_pips:.2f}",
                    f"{trade.pnl_usd:.4f}",
                    f"{trade.mae_pips:.2f}",
                    f"{trade.equity:.4f}",
                    trade.hold_bars,
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


def _timeframe_to_minutes(timeframe: str) -> int:
    mapping = {
        "S5": 1,
        "S10": 1,
        "S15": 1,
        "S30": 1,
        "M1": 1,
        "M2": 2,
        "M4": 4,
        "M5": 5,
        "M10": 10,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H2": 120,
        "H3": 180,
        "H4": 240,
        "H6": 360,
        "H8": 480,
        "H12": 720,
        "D": 1440,
    }
    return mapping.get(timeframe.upper(), 15)


def _resolve_date_range(days: int, from_date: Optional[str], to_date: Optional[str]) -> tuple[datetime, datetime]:
    if from_date and to_date:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
        if from_dt >= to_dt:
            raise SystemExit("--from must be earlier than --to")
        return from_dt, to_dt
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    return from_dt, to_dt


def _to_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


if __name__ == "__main__":
    main()
