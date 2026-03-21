from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

import requests

from app.backtest.candle_cache import CandleCache
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
    pnl_pips_weighted: float
    pnl_usd: float
    mae_pips: float
    equity: float
    hold_bars: int
    entry_components: str = ""


@dataclass
class EquityPoint:
    ts: str
    equity: float


@dataclass
class CompoundedAuditRow:
    entry_ts: str
    exit_ts: str
    side: str
    leg: str
    reason: str
    base_units_opened: float
    dynamic_units_opened: float
    scale: float
    base_pnl_usd: float
    compounded_pnl_usd: float
    balance_before: float
    balance_after: float
    liquidated: bool


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
        max_hold_bars=settings.strategy_max_hold_bars,
        drawdown_stop_pips=settings.strategy_drawdown_stop_pips,
        drawdown_stop_bars=settings.strategy_drawdown_stop_bars,
        tp1_close_pct=args.tp1_close_pct,
        trail_drawdown_pct=args.trail_drawdown_pct,
        be_lock_pips=args.be_lock_pips,
        profit_floor1_trigger_pips=settings.strategy_profit_floor1_trigger_pips,
        profit_floor1_lock_pips=settings.strategy_profit_floor1_lock_pips,
        profit_floor2_trigger_pips=settings.strategy_profit_floor2_trigger_pips,
        profit_floor2_lock_pips=settings.strategy_profit_floor2_lock_pips,
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
        block_entry_hours_utc=settings.strategy_block_entry_hours_utc,
        no_intent_override_enabled=settings.strategy_no_intent_override_enabled,
        no_intent_override_hours_utc=settings.strategy_no_intent_override_hours_utc,
        no_intent_override_atr_mult=settings.strategy_no_intent_override_atr_mult,
        no_intent_override_body_ratio_min=settings.strategy_no_intent_override_body_ratio_min,
        no_intent_override_close_extreme_frac=settings.strategy_no_intent_override_close_extreme_frac,
        no_intent_override_volume_lookback=settings.strategy_no_intent_override_volume_lookback,
        no_intent_override_volume_percentile=settings.strategy_no_intent_override_volume_percentile,
        no_intent_override_risk_scale=settings.strategy_no_intent_override_risk_scale,
        hour_strict_mode_enabled=settings.strategy_hour_strict_mode_enabled,
        hour_strict_hours_utc=settings.strategy_hour_strict_hours_utc,
        hour_strict_require_cross_or_continuation=settings.strategy_hour_strict_require_cross_or_continuation,
        hour_strict_risk_scale=settings.strategy_hour_strict_risk_scale,
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
        pb_enabled_long=settings.strategy_pb_enabled_long,
        pb_enabled_short=settings.strategy_pb_enabled_short,
        pb_lookback_bars=settings.strategy_pb_lookback_bars,
        cont_enabled=settings.strategy_cont_enabled,
        base_max_bars=settings.strategy_base_max_bars,
        base_max_range_atr=settings.strategy_base_max_range_atr,
        rejoin_enabled=settings.strategy_rejoin_enabled,
        rejoin_enabled_long=settings.strategy_rejoin_enabled_long,
        rejoin_enabled_short=settings.strategy_rejoin_enabled_short,
        allow_second_chance=settings.strategy_allow_second_chance,
        reenter_within_bars=settings.strategy_reenter_within_bars,
        early_loss_cut_pips=settings.strategy_early_loss_cut_pips,
        momentum_fail_exit_pips=settings.strategy_momentum_fail_exit_pips,
    )
    from_dt, to_dt = _resolve_date_range(args.days, args.from_date, args.to_date)
    tv_panel_mode = args.exec_profile == "tv_panel"
    if args.magnifier:
        if args.magnifier == "off":
            args.magnify_tf = None
        else:
            args.magnify_tf = "M1"
    if tv_panel_mode:
        args.tv_parity = False
        args.magnify_tf = None
        args.entry_timing = "close"
    if args.tv_parity:
        if not args.magnify_tf:
            args.magnify_tf = "M1"
        if not args.entry_timing:
            args.entry_timing = "close"
        if not args.magnify_policy:
            args.magnify_policy = "conservative"
    if args.exec_profile == "live_reality" and not args.entry_timing:
        args.entry_timing = "close"
    if args.exec_profile == "live_reality" and args.magnify_tf is None:
        if _timeframe_to_minutes(args.timeframe) >= 5:
            args.magnify_tf = "M1"
    if args.compounding_start_balance is None:
        args.compounding_start_balance = float(args.units)

    candles = _fetch_candles(
        client=client,
        symbol=args.symbol,
        timeframe=args.timeframe,
        from_dt=from_dt,
        to_dt=to_dt,
        cache=None if args.no_cache else CandleCache(Path(args.cache_db)),
        refresh_cache=args.refresh_cache,
    )
    if len(candles) < 2:
        raise SystemExit("Not enough candles returned for backtest")

    magnifier_candles: list[dict[str, Any]] | None = None
    if args.magnify_tf and args.magnify_tf.upper() != args.timeframe.upper():
        magnifier_candles = _fetch_candles(
            client=client,
            symbol=args.symbol,
            timeframe=args.magnify_tf,
            from_dt=from_dt,
            to_dt=to_dt,
            cache=None if args.no_cache else CandleCache(Path(args.cache_db)),
            refresh_cache=args.refresh_cache,
        )
    exit_inspect_tf = args.exit_inspect_tf.upper() if args.exit_inspect_tf else None
    exit_inspect_candles: list[dict[str, Any]] | None = None
    if exit_inspect_tf and exit_inspect_tf != args.timeframe.upper():
        if args.magnify_tf and args.magnify_tf.upper() == exit_inspect_tf:
            exit_inspect_candles = magnifier_candles
        else:
            exit_inspect_candles = _fetch_candles(
                client=client,
                symbol=args.symbol,
                timeframe=exit_inspect_tf,
                from_dt=from_dt,
                to_dt=to_dt,
                cache=None if args.no_cache else CandleCache(Path(args.cache_db)),
                refresh_cache=args.refresh_cache,
            )

    effective_fill = args.fill
    if args.entry_timing:
        if args.entry_timing == "intrabar":
            effective_fill = args.fill
        else:
            effective_fill = "close"

    spread_pips_effective = 0.0 if tv_panel_mode else args.spread_pips
    use_bid_ask_effective = False if tv_panel_mode else args.use_bid_ask
    if args.tv_parity:
        use_bid_ask_effective = True

    _print_config_table(
        exec_profile=args.exec_profile,
        entry_timing=(args.entry_timing or args.fill),
        entries_close_only=(effective_fill == "close"),
        magnifier=("M1" if args.magnify_tf else "OFF"),
        magnify_policy=(args.magnify_policy if args.magnify_tf else "n/a"),
        use_bid_ask=use_bid_ask_effective,
        spread_effective=spread_pips_effective,
        bar_fill_policy=args.bar_fill_policy,
        parity_debug=args.parity_debug,
    )
    if tv_panel_mode:
        print("Entry timing: close")
        print("Magnifier: OFF")
        print("Standard OHLC: OFF")
        print("Commission: 0")
        print("Slippage: 0")

    trades, equity_curve, metrics_extra = _run_backtest(
        candles=candles,
        symbol=args.symbol,
        timeframe=args.timeframe,
        units=args.units,
        spread_pips=spread_pips_effective,
        fill=effective_fill,
        bar_fill_policy=args.bar_fill_policy,
        use_runner=args.use_runner,
        strategy=strategy,
        strategy_config=strategy_config,
        magnify_tf=args.magnify_tf,
        magnify_policy=args.magnify_policy,
        magnifier_candles=magnifier_candles,
        tv_parity=args.tv_parity,
        exec_profile=args.exec_profile,
        use_bid_ask=use_bid_ask_effective,
        exit_inspect_tf=exit_inspect_tf,
        exit_inspect_candles=exit_inspect_candles,
    )
    _propagate_entry_components(trades)
    comp_metrics, compounded_equity_curve, compounded_audit = _compute_compounded_metrics(
        trades=trades,
        start_balance=float(args.compounding_start_balance),
        participation=float(args.compounding_participation),
        model=args.compounding_model,
        leverage=float(args.compounding_leverage),
        liquidation_floor=float(args.compounding_liquidation_floor),
    )
    metrics_extra.update(comp_metrics)

    metrics = _print_summary(trades, equity_curve, metrics_extra)
    if args.tv_parity:
        print("TV parity: enabled")
        print(f"Entry timing: {effective_fill}")
        print("Spread model: bid/ask half-spread")
        print(f"Spread pips: {args.spread_pips}")
        print("Parity sanity:")
        print(f"  Entries long: {int(metrics_extra.get('entries_long', 0))}")
        print(f"  Entries short: {int(metrics_extra.get('entries_short', 0))}")
        print(f"  TP1 hits: {int(metrics_extra.get('tp1_hits', 0))}")
        print(f"  Runner exits: {int(metrics_extra.get('runner_exits', 0))}")
        print(f"  Same-subbar conflicts: {int(metrics_extra.get('num_same_bar_tp1_and_runner', 0))}")
    if args.magnify_tf:
        print(f"Magnifier TF: {args.magnify_tf}")
        print(f"Magnifier Policy: {args.magnify_policy}")
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
            "spread_pips_effective": spread_pips_effective,
            "use_bid_ask": use_bid_ask_effective,
            "fill": args.fill,
            "tp1_pips": args.tp1_pips,
            "sl_pips": args.sl_pips,
            "tp1_close_pct": args.tp1_close_pct,
            "trail_drawdown_pct": args.trail_drawdown_pct,
            "be_lock_pips": args.be_lock_pips,
            "profit_floor1_trigger_pips": settings.strategy_profit_floor1_trigger_pips,
            "profit_floor1_lock_pips": settings.strategy_profit_floor1_lock_pips,
            "profit_floor2_trigger_pips": settings.strategy_profit_floor2_trigger_pips,
            "profit_floor2_lock_pips": settings.strategy_profit_floor2_lock_pips,
            "bar_fill_policy": args.bar_fill_policy,
            "use_runner": args.use_runner,
            "use_stoch_exit": args.use_stoch_exit,
            "st_tight_pips": args.st_tight_pips,
            "exec_profile": args.exec_profile,
            "magnifier": args.magnifier,
            "exit_inspect_tf": args.exit_inspect_tf,
            "parity_debug": args.parity_debug,
            "cache_db": None if args.no_cache else args.cache_db,
            "refresh_cache": args.refresh_cache,
            "no_intent_override_enabled": settings.strategy_no_intent_override_enabled,
            "no_intent_override_hours_utc": settings.strategy_no_intent_override_hours_utc,
            "no_intent_override_atr_mult": settings.strategy_no_intent_override_atr_mult,
            "no_intent_override_body_ratio_min": settings.strategy_no_intent_override_body_ratio_min,
            "no_intent_override_close_extreme_frac": settings.strategy_no_intent_override_close_extreme_frac,
            "no_intent_override_volume_lookback": settings.strategy_no_intent_override_volume_lookback,
            "no_intent_override_volume_percentile": settings.strategy_no_intent_override_volume_percentile,
            "no_intent_override_risk_scale": settings.strategy_no_intent_override_risk_scale,
            "hour_strict_mode_enabled": settings.strategy_hour_strict_mode_enabled,
            "hour_strict_hours_utc": settings.strategy_hour_strict_hours_utc,
            "hour_strict_require_cross_or_continuation": settings.strategy_hour_strict_require_cross_or_continuation,
            "hour_strict_risk_scale": settings.strategy_hour_strict_risk_scale,
            "compounding_participation": args.compounding_participation,
            "compounding_start_balance": args.compounding_start_balance,
            "compounding_model": args.compounding_model,
            "compounding_leverage": args.compounding_leverage,
            "compounding_liquidation_floor": args.compounding_liquidation_floor,
        },
        metrics=metrics,
        compounded_equity=compounded_equity_curve,
        compounded_audit=compounded_audit,
    )
    if args.parity_debug:
        _write_parity_debug(
            symbol=args.symbol,
            timeframe=args.timeframe,
            trades=trades,
            from_dt=from_dt,
            to_dt=to_dt,
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
    parser.add_argument("--magnify_tf")
    parser.add_argument("--magnifier", choices=["off", "m1"])
    parser.add_argument(
        "--magnify_policy",
        choices=["conservative", "optimistic"],
        default="conservative",
    )
    parser.add_argument("--use_bid_ask", type=_parse_bool, default=True)
    parser.add_argument("--tv_parity", type=_parse_bool, default=False)
    parser.add_argument("--entry_timing", choices=["close", "intrabar"])
    parser.add_argument("--exit_inspect_tf")
    parser.add_argument(
        "--exec_profile",
        choices=["tv_panel", "live_reality"],
        default="live_reality",
    )
    parser.add_argument("--parity_debug", type=_parse_bool, default=False)
    parser.add_argument("--no_cache", action="store_true")
    parser.add_argument("--refresh_cache", action="store_true")
    parser.add_argument("--cache_db", default="data/candles_cache.db")
    parser.add_argument("--compounding_model", choices=["notional", "margin"], default="notional")
    parser.add_argument("--compounding_leverage", type=float, default=1.0)
    parser.add_argument("--compounding_liquidation_floor", type=float, default=0.0)
    parser.add_argument("--compounding_participation", type=float, default=0.95)
    parser.add_argument("--compounding_start_balance", type=float)
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
    cache: CandleCache | None = None,
    refresh_cache: bool = False,
) -> list[dict[str, Any]]:
    timeframe_minutes = _timeframe_to_minutes(timeframe)
    chunk_minutes = 5000 * timeframe_minutes
    all_candles: dict[str, dict[str, Any]] = {}
    missing_windows: list[tuple[datetime, datetime]] = [(from_dt, to_dt)]

    if cache and not refresh_cache:
        cached = cache.load_range(
            symbol=symbol,
            timeframe=timeframe,
            from_dt=from_dt,
            to_dt=to_dt,
        )
        for candle in cached:
            all_candles[candle["time"]] = candle
        missing_windows = _missing_windows(
            from_dt=from_dt,
            to_dt=to_dt,
            timeframe=timeframe,
            cached_candles=cached,
        )
        print(
            f"cache status: symbol={symbol} timeframe={timeframe} "
            f"cached={len(cached)} missing_windows={len(missing_windows)}"
        )
    elif cache and refresh_cache:
        print(f"cache refresh enabled: symbol={symbol} timeframe={timeframe}")

    for window_from, window_to in missing_windows:
        current = window_from
        while current < window_to:
            chunk_to = min(current + timedelta(minutes=chunk_minutes), window_to)
            try:
                batch = client.get_candles_range(
                    symbol=symbol,
                    granularity=timeframe,
                    from_ts=_to_rfc3339(current),
                    to_ts=_to_rfc3339(chunk_to),
                    count=None,
                    include_first=True,
                )
            except requests.RequestException as exc:
                raise SystemExit(
                    "Failed to fetch candles from OANDA. "
                    f"symbol={symbol}, timeframe={timeframe}, "
                    f"window={_format_date(current)}->{_format_date(chunk_to)}. "
                    "Check internet/DNS access and OANDA API settings."
                ) from exc
            if cache and batch:
                cache.upsert(symbol=symbol, timeframe=timeframe, candles=batch)
            for candle in batch:
                all_candles[candle["time"]] = candle
            print(
                f"fetching candles chunk: {_format_date(current)} -> {_format_date(chunk_to)} (n={len(batch)})"
            )
            current = chunk_to

    return [all_candles[k] for k in sorted(all_candles)]


def _missing_windows(
    *,
    from_dt: datetime,
    to_dt: datetime,
    timeframe: str,
    cached_candles: list[dict[str, Any]],
) -> list[tuple[datetime, datetime]]:
    if not cached_candles:
        return [(from_dt, to_dt)]

    step = timedelta(seconds=_timeframe_to_seconds(timeframe))
    cached_times = sorted(_parse_candle_time(c) for c in cached_candles)
    windows: list[tuple[datetime, datetime]] = []

    first = cached_times[0]
    if first - from_dt > step:
        windows.append((from_dt, first))

    for prev, current in zip(cached_times, cached_times[1:]):
        expected_next = prev + step
        if current - expected_next > step / 2:
            windows.append((expected_next, current))

    last = cached_times[-1]
    if to_dt - last > step:
        windows.append((last + step, to_dt))

    return [(w_from, w_to) for w_from, w_to in windows if w_from < w_to]


def _build_magnifier_map(
    *,
    signal_candles: list[dict[str, Any]],
    magnifier_candles: list[dict[str, Any]],
    timeframe: str,
) -> list[list[dict[str, Any]]]:
    window_seconds = _timeframe_to_seconds(timeframe)
    buckets: list[list[dict[str, Any]]] = []
    mag_index = 0
    for candle in signal_candles:
        start = _parse_candle_time(candle)
        end = start + timedelta(seconds=window_seconds)
        bucket: list[dict[str, Any]] = []
        while mag_index < len(magnifier_candles):
            mag_candle = magnifier_candles[mag_index]
            mag_time = _parse_candle_time(mag_candle)
            if mag_time < start:
                mag_index += 1
                continue
            if mag_time >= end:
                break
            bucket.append(mag_candle)
            mag_index += 1
        buckets.append(bucket)
    return buckets


def _resolve_tp1_sl_order(tp1_hit: bool, sl_hit: bool, policy: str) -> tuple[bool, bool]:
    # Ordering assumption when both TP1 and SL are touched in the same bar/sub-bar.
    # conservative => SL first, optimistic => TP1 first.
    if tp1_hit and sl_hit:
        if policy == "conservative":
            return False, True
        return True, False
    return tp1_hit, sl_hit


def _allow_runner_same_candle(tp1_hit: bool, policy: str) -> bool:
    # Conservative still allows same-candle TP1 + runner if range permits,
    # but ordering is handled by policy.
    return True


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
    magnify_tf: str | None = None,
    magnify_policy: str = "conservative",
    magnifier_candles: list[dict[str, Any]] | None = None,
    tv_parity: bool = False,
    exec_profile: str = "live_reality",
    use_bid_ask: bool = True,
    exit_inspect_tf: str | None = None,
    exit_inspect_candles: list[dict[str, Any]] | None = None,
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
    num_ambiguous_bars = 0
    num_same_bar_tp1_and_runner = 0
    num_stopouts = 0
    num_entries_long = 0
    num_entries_short = 0
    num_runner_exits = 0

    strategy_state = StrategyState()

    pip_factor = _pip_factor(symbol)
    spread_price = spread_pips * pip_factor
    balance = 0.0
    tv_panel = exec_profile == "tv_panel"

    magnifier_map: list[list[dict[str, Any]]] | None = None
    if magnify_tf and magnifier_candles:
        magnifier_map = _build_magnifier_map(
            signal_candles=candles,
            magnifier_candles=magnifier_candles,
            timeframe=timeframe,
        )
    exit_inspect_map: list[list[dict[str, Any]]] | None = None
    if exit_inspect_tf and exit_inspect_tf != timeframe.upper():
        if magnify_tf and magnify_tf.upper() == exit_inspect_tf and magnifier_map is not None:
            exit_inspect_map = magnifier_map
        elif exit_inspect_candles:
            exit_inspect_map = _build_magnifier_map(
                signal_candles=candles,
                magnifier_candles=exit_inspect_candles,
                timeframe=timeframe,
            )
    exit_inspect_series: list[Candle] = []
    inspect_exit_reasons = {
        "max hold stop",
        "drawdown time stop",
        "early loss cut",
        "momentum fail stop",
    }

    last_index = len(candles) - 1
    max_index = last_index if fill == "close" else last_index - 1

    pending_entry: dict[str, Any] | None = None
    pending_flip: dict[str, Any] | None = None
    last_entry_bar_ts: Optional[str] = None

    for i in range(1, max_index + 1):
        if magnifier_map is not None:
            if pending_entry and pending_entry["activate_index"] == i:
                entry_price = pending_entry["entry_price"]
                entry_ts = pending_entry["entry_ts"]
                entry_index = i
                position_side = pending_entry["side"]
                entry_units = float(pending_entry.get("units", float(units)))
                position_units_opened = entry_units
                position_units_remaining = entry_units
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
                        reason=pending_entry.get("reason", "ENTRY"),
                        pnl_pips=0.0,
                        pnl_pips_weighted=0.0,
                        pnl_usd=0.0,
                        mae_pips=0.0,
                        equity=balance,
                        hold_bars=0,
                        entry_components=pending_entry.get("entry_components", ""),
                    )
                )
                if position_side == "LONG":
                    num_entries_long += 1
                else:
                    num_entries_short += 1
                pending_entry = None
            if pending_flip and pending_flip["activate_index"] == i and position_side:
                base_price = pending_flip["base_price"]
                exit_price = _exit_price(base_price, position_side, spread_price) if use_bid_ask else base_price
                balance, trades = _record_exit(
                    trades,
                    equity_curve,
                    balance,
                    entry_ts,
                    pending_flip["entry_ts"],
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
                entry_price = _entry_price(base_price, pending_flip["side"], spread_price) if use_bid_ask else base_price
                entry_ts = pending_flip["entry_ts"]
                entry_index = i
                position_side = pending_flip["side"]
                entry_units = float(pending_flip.get("units", float(units)))
                position_units_opened = entry_units
                position_units_remaining = entry_units
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
                        pnl_pips_weighted=0.0,
                        pnl_usd=0.0,
                        mae_pips=0.0,
                        equity=balance,
                        hold_bars=0,
                        entry_components=pending_flip.get("entry_components", ""),
                    )
                )
                if position_side == "LONG":
                    num_entries_long += 1
                else:
                    num_entries_short += 1
                pending_flip = None

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

        if magnifier_map is not None:
            magnifier_candles_for_bar = magnifier_map[i] if i < len(magnifier_map) else []
            for mag_candle in magnifier_candles_for_bar:
                if not position_side or entry_price is None:
                    break
                mag_high = float(mag_candle["h"])
                mag_low = float(mag_candle["l"])
                mag_close = float(mag_candle["c"])
                half = spread_price / 2
                bid_high = mag_high - half
                bid_low = mag_low - half
                ask_high = mag_high + half
                ask_low = mag_low + half

                mae_pips = _update_mae(mag_candle, entry_price, position_side, pip_factor, mae_pips)
                tp1_price, sl_price = _tp1_sl_prices(
                    position_side,
                    entry_price,
                    strategy_config.tp1_pips,
                    strategy_config.sl_pips,
                    pip_factor,
                )
                if use_bid_ask:
                    if position_side == "LONG":
                        tp1_hit = bid_high >= tp1_price
                        sl_hit = bid_low <= sl_price
                    else:
                        tp1_hit = ask_low <= tp1_price
                        sl_hit = ask_high >= sl_price
                else:
                    tp1_hit = _tp1_hit(position_side, mag_high, mag_low, tp1_price)
                    sl_hit = _sl_hit(position_side, mag_high, mag_low, sl_price)
                if tp1_hit and sl_hit and not tp1_reached:
                    num_ambiguous_bars += 1
                    tp1_hit, sl_hit = _resolve_tp1_sl_order(tp1_hit, sl_hit, magnify_policy)
                if sl_hit and not tp1_reached:
                    exit_price = sl_price if not use_bid_ask else _exit_price(sl_price, position_side, spread_price)
                    balance, trades = _record_exit(
                        trades,
                        equity_curve,
                        balance,
                        entry_ts,
                        mag_candle["time"],
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
                    num_stopouts += 1
                    total_trades += 1
                    total_hold_bars += i - (entry_index or i)
                    position_side, entry_price, entry_ts, entry_index = None, None, None, None
                    position_units_opened = 0.0
                    position_units_remaining = 0.0
                    tp1_reached = False
                    long_peak = None
                    short_trough = None
                    mae_pips = 0.0
                    break

                if position_side and tp1_hit and position_units_remaining > 0 and not tp1_reached:
                    tp1_reached = True
                    tp1_hit_trades += 1
                    tp1_exec_price = tp1_price if not use_bid_ask else _exit_price(tp1_price, position_side, spread_price)
                    units_closed = position_units_opened * (strategy_config.tp1_close_pct / 100)
                    units_closed = min(units_closed, position_units_remaining)
                    balance, trades = _record_exit(
                        trades,
                        equity_curve,
                        balance,
                        entry_ts,
                        mag_candle["time"],
                        position_side,
                        position_units_opened,
                        units_closed,
                        entry_price,
                        tp1_exec_price,
                        "TP1",
                        "TP1",
                        pip_factor,
                        mae_pips,
                        i,
                        entry_index,
                    )
                    position_units_remaining -= units_closed
                    if position_side == "LONG":
                        long_peak = mag_high
                    else:
                        short_trough = mag_low
                    if not use_runner or position_units_remaining <= 0:
                        balance, trades = _record_exit(
                            trades,
                            equity_curve,
                            balance,
                            entry_ts,
                            mag_candle["time"],
                            position_side,
                            position_units_opened,
                            position_units_remaining,
                            entry_price,
                            tp1_exec_price,
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
                        break

                if position_side and tp1_reached and position_units_remaining > 0 and use_runner:
                    if position_side == "LONG":
                        long_peak = max(long_peak or mag_high, mag_high)
                        trail_candidate = long_peak * (1 - strategy_config.trail_drawdown_pct / 100)
                        be_stop = entry_price + strategy_config.be_lock_pips * pip_factor
                        runner_stop = max(be_stop, trail_candidate)
                        runner_stop = _apply_profit_floor_stop(
                            side=position_side,
                            entry_price=entry_price,
                            runner_stop=runner_stop,
                            favorable_extreme=long_peak,
                            pip_factor=pip_factor,
                            trigger1_pips=strategy_config.profit_floor1_trigger_pips,
                            lock1_pips=strategy_config.profit_floor1_lock_pips,
                            trigger2_pips=strategy_config.profit_floor2_trigger_pips,
                            lock2_pips=strategy_config.profit_floor2_lock_pips,
                        )
                        if (
                            strategy_config.use_stoch_exit
                            and st_meta.get("stKxDn")
                            and st_meta.get("k", 0) > strategy_config.st_ob
                        ):
                            runner_stop = max(
                                runner_stop, mag_close - strategy_config.st_tight_pips * pip_factor
                            )
                        runner_hit = (bid_low <= runner_stop) if use_bid_ask else (mag_low <= runner_stop)
                        if runner_hit and tp1_hit and _allow_runner_same_candle(tp1_hit, magnify_policy):
                            num_same_bar_tp1_and_runner += 1
                        if runner_hit and (not tp1_hit or _allow_runner_same_candle(tp1_hit, magnify_policy)):
                            exit_price = runner_stop if not use_bid_ask else _exit_price(runner_stop, position_side, spread_price)
                            balance, trades = _record_exit(
                                trades,
                                equity_curve,
                                balance,
                                entry_ts,
                                mag_candle["time"],
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
                            num_runner_exits += 1
                            total_trades += 1
                            total_hold_bars += i - (entry_index or i)
                            position_side, entry_price, entry_ts, entry_index = None, None, None, None
                            position_units_opened = 0.0
                            position_units_remaining = 0.0
                            tp1_reached = False
                            long_peak = None
                            short_trough = None
                            mae_pips = 0.0
                            break
                    else:
                        short_trough = min(short_trough or mag_low, mag_low)
                        trail_candidate = short_trough * (1 + strategy_config.trail_drawdown_pct / 100)
                        be_stop = entry_price - strategy_config.be_lock_pips * pip_factor
                        runner_stop = min(be_stop, trail_candidate)
                        runner_stop = _apply_profit_floor_stop(
                            side=position_side,
                            entry_price=entry_price,
                            runner_stop=runner_stop,
                            favorable_extreme=short_trough,
                            pip_factor=pip_factor,
                            trigger1_pips=strategy_config.profit_floor1_trigger_pips,
                            lock1_pips=strategy_config.profit_floor1_lock_pips,
                            trigger2_pips=strategy_config.profit_floor2_trigger_pips,
                            lock2_pips=strategy_config.profit_floor2_lock_pips,
                        )
                        if (
                            strategy_config.use_stoch_exit
                            and st_meta.get("stKxUp")
                            and st_meta.get("k", 0) < strategy_config.st_os
                        ):
                            runner_stop = min(
                                runner_stop, mag_close + strategy_config.st_tight_pips * pip_factor
                            )
                        runner_hit = (ask_high >= runner_stop) if use_bid_ask else (mag_high >= runner_stop)
                        if runner_hit and tp1_hit and _allow_runner_same_candle(tp1_hit, magnify_policy):
                            num_same_bar_tp1_and_runner += 1
                        if runner_hit and (not tp1_hit or _allow_runner_same_candle(tp1_hit, magnify_policy)):
                            exit_price = runner_stop if not use_bid_ask else _exit_price(runner_stop, position_side, spread_price)
                            balance, trades = _record_exit(
                                trades,
                                equity_curve,
                                balance,
                                entry_ts,
                                mag_candle["time"],
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
                            num_runner_exits += 1
                            total_trades += 1
                            total_hold_bars += i - (entry_index or i)
                            position_side, entry_price, entry_ts, entry_index = None, None, None, None
                            position_units_opened = 0.0
                            position_units_remaining = 0.0
                            tp1_reached = False
                            long_peak = None
                            short_trough = None
                            mae_pips = 0.0
                            break

        if exit_inspect_map is not None:
            exit_inspect_candles_for_bar = exit_inspect_map[i] if i < len(exit_inspect_map) else []
            for inspect_candle in exit_inspect_candles_for_bar:
                exit_inspect_series.append(
                    Candle(
                        ts=inspect_candle["time"],
                        o=float(inspect_candle["o"]),
                        h=float(inspect_candle["h"]),
                        l=float(inspect_candle["l"]),
                        c=float(inspect_candle["c"]),
                        volume=int(inspect_candle["volume"]),
                    )
                )
                if not position_side or entry_price is None:
                    continue
                inspect_decision = strategy.evaluate(
                    exit_inspect_series,
                    StrategyContext(
                        symbol=symbol,
                        timeframe=exit_inspect_tf or timeframe,
                        position=PositionState(
                            side=position_side,
                            units=position_units_remaining,
                            avg_price=entry_price,
                            entry_ts=entry_ts,
                        ),
                        config=strategy_config,
                        state=strategy_state,
                        bar_index=len(exit_inspect_series) - 1,
                        spread_pips=None,
                        spread_available=False,
                        is_realtime=False,
                        exit_only=True,
                    ),
                )
                if inspect_decision.next_state is not None:
                    strategy_state = inspect_decision.next_state
                if inspect_decision.action != "EXIT":
                    continue
                if inspect_decision.reason not in inspect_exit_reasons:
                    continue
                inspect_close = float(inspect_candle["c"])
                exit_base_price = (
                    inspect_decision.price if inspect_decision.price is not None else inspect_close
                )
                exit_price = (
                    _exit_price(exit_base_price, position_side, spread_price)
                    if use_bid_ask
                    else exit_base_price
                )
                balance, trades = _record_exit(
                    trades,
                    equity_curve,
                    balance,
                    entry_ts,
                    inspect_candle["time"],
                    position_side,
                    position_units_opened,
                    position_units_remaining,
                    entry_price,
                    exit_price,
                    "EXIT",
                    f"EXIT_INSPECT_{inspect_decision.reason.upper().replace(' ', '_')}",
                    pip_factor,
                    mae_pips,
                    i,
                    entry_index,
                )
                if inspect_decision.reason in {"drawdown time stop", "early loss cut", "momentum fail stop"}:
                    num_stopouts += 1
                total_trades += 1
                total_hold_bars += i - (entry_index or i)
                position_side, entry_price, entry_ts, entry_index = None, None, None, None
                position_units_opened = 0.0
                position_units_remaining = 0.0
                tp1_reached = False
                long_peak = None
                short_trough = None
                mae_pips = 0.0
                break

        if position_side and entry_price is not None:
            mae_pips = _update_mae(candle, entry_price, position_side, pip_factor, mae_pips)

            tp1_price, sl_price = _tp1_sl_prices(
                position_side,
                entry_price,
                strategy_config.tp1_pips,
                strategy_config.sl_pips,
                pip_factor,
            )
            if use_bid_ask:
                bid_high = candle_high - spread_price / 2
                bid_low = candle_low - spread_price / 2
                ask_high = candle_high + spread_price / 2
                ask_low = candle_low + spread_price / 2
                if position_side == "LONG":
                    tp1_hit = bid_high >= tp1_price
                    sl_hit = bid_low <= sl_price
                else:
                    tp1_hit = ask_low <= tp1_price
                    sl_hit = ask_high >= sl_price
            else:
                tp1_hit = _tp1_hit(position_side, candle_high, candle_low, tp1_price)
                sl_hit = _sl_hit(position_side, candle_high, candle_low, sl_price)

            if tp1_hit and sl_hit and not tp1_reached:
                num_ambiguous_bars += 1
                if bar_fill_policy == "conservative":
                    sl_hit = True
                    tp1_hit = False
                else:
                    tp1_hit = True
                    sl_hit = False

            if sl_hit and not tp1_reached:
                exit_price = _exit_price(sl_price, position_side, spread_price) if use_bid_ask else sl_price
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
                num_stopouts += 1
                total_trades += 1
                total_hold_bars += i - (entry_index or i)
                position_side, entry_price, entry_ts, entry_index = None, None, None, None
                position_units_opened = 0.0
                position_units_remaining = 0.0
                tp1_reached = False
                long_peak = None
                short_trough = None
                mae_pips = 0.0

            if position_side and tp1_hit and position_units_remaining > 0 and not tp1_reached:
                tp1_reached = True
                tp1_hit_trades += 1
                tp1_exec_price = _exit_price(tp1_price, position_side, spread_price) if use_bid_ask else tp1_price
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
                    tp1_exec_price,
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
                        tp1_exec_price,
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

            if position_side and tp1_reached and position_units_remaining > 0 and use_runner:
                if position_side == "LONG":
                    long_peak = max(long_peak or candle_high, candle_high)
                    trail_candidate = long_peak * (1 - strategy_config.trail_drawdown_pct / 100)
                    be_stop = entry_price + strategy_config.be_lock_pips * pip_factor
                    runner_stop = max(be_stop, trail_candidate)
                    runner_stop = _apply_profit_floor_stop(
                        side=position_side,
                        entry_price=entry_price,
                        runner_stop=runner_stop,
                        favorable_extreme=long_peak,
                        pip_factor=pip_factor,
                        trigger1_pips=strategy_config.profit_floor1_trigger_pips,
                        lock1_pips=strategy_config.profit_floor1_lock_pips,
                        trigger2_pips=strategy_config.profit_floor2_trigger_pips,
                        lock2_pips=strategy_config.profit_floor2_lock_pips,
                    )
                    if (
                        strategy_config.use_stoch_exit
                        and st_meta.get("stKxDn")
                        and st_meta.get("k", 0) > strategy_config.st_ob
                    ):
                        runner_stop = max(
                            runner_stop, candle_close - strategy_config.st_tight_pips * pip_factor
                        )
                    if use_bid_ask:
                        runner_hit = (candle_low - spread_price / 2) <= runner_stop
                    else:
                        runner_hit = candle_low <= runner_stop
                    if runner_hit and tp1_hit and bar_fill_policy == "optimistic":
                        num_same_bar_tp1_and_runner += 1
                    if runner_hit and (not tp1_hit or bar_fill_policy == "optimistic"):
                        exit_price = _exit_price(runner_stop, position_side, spread_price) if use_bid_ask else runner_stop
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
                        num_runner_exits += 1
                        total_trades += 1
                        total_hold_bars += i - (entry_index or i)
                        position_side, entry_price, entry_ts, entry_index = None, None, None, None
                        position_units_opened = 0.0
                        position_units_remaining = 0.0
                        tp1_reached = False
                        long_peak = None
                        short_trough = None
                        mae_pips = 0.0
                else:
                    short_trough = min(short_trough or candle_low, candle_low)
                    trail_candidate = short_trough * (1 + strategy_config.trail_drawdown_pct / 100)
                    be_stop = entry_price - strategy_config.be_lock_pips * pip_factor
                    runner_stop = min(be_stop, trail_candidate)
                    runner_stop = _apply_profit_floor_stop(
                        side=position_side,
                        entry_price=entry_price,
                        runner_stop=runner_stop,
                        favorable_extreme=short_trough,
                        pip_factor=pip_factor,
                        trigger1_pips=strategy_config.profit_floor1_trigger_pips,
                        lock1_pips=strategy_config.profit_floor1_lock_pips,
                        trigger2_pips=strategy_config.profit_floor2_trigger_pips,
                        lock2_pips=strategy_config.profit_floor2_lock_pips,
                    )
                    if (
                        strategy_config.use_stoch_exit
                        and st_meta.get("stKxUp")
                        and st_meta.get("k", 0) < strategy_config.st_os
                    ):
                        runner_stop = min(
                            runner_stop, candle_close + strategy_config.st_tight_pips * pip_factor
                        )
                    if use_bid_ask:
                        runner_hit = (candle_high + spread_price / 2) >= runner_stop
                    else:
                        runner_hit = candle_high >= runner_stop
                    if runner_hit and tp1_hit and bar_fill_policy == "optimistic":
                        num_same_bar_tp1_and_runner += 1
                    if runner_hit and (not tp1_hit or bar_fill_policy == "optimistic"):
                        exit_price = _exit_price(runner_stop, position_side, spread_price) if use_bid_ask else runner_stop
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
                        num_runner_exits += 1
                        total_trades += 1
                        total_hold_bars += i - (entry_index or i)
                        position_side, entry_price, entry_ts, entry_index = None, None, None, None
                        position_units_opened = 0.0
                        position_units_remaining = 0.0
                        tp1_reached = False
                        long_peak = None
                        short_trough = None
                        mae_pips = 0.0

        if magnifier_map is not None:
            if signal and position_side is None and i + 1 <= max_index:
                base_price, base_ts = _fill_price(candles, i, fill)
                entry_units = _entry_units_for_decision(decision, units)
                if use_bid_ask:
                    entry_price = _entry_price(base_price, signal, spread_price)
                else:
                    entry_price = base_price
                pending_entry = {
                    "activate_index": i + 1,
                    "entry_price": entry_price,
                    "entry_ts": base_ts,
                    "side": signal,
                    "units": entry_units,
                    "reason": "ENTRY",
                    "entry_components": _entry_components_for_signal(decision, signal),
                }
            if signal and position_side and signal != position_side and i + 1 <= max_index:
                base_price, base_ts = _fill_price(candles, i, fill)
                entry_units = _entry_units_for_decision(decision, units)
                pending_flip = {
                    "activate_index": i + 1,
                    "base_price": base_price,
                    "entry_ts": base_ts,
                    "side": signal,
                    "units": entry_units,
                    "entry_components": _entry_components_for_signal(decision, signal),
                }
        elif signal and position_side is None:
            if tv_panel and last_entry_bar_ts == candle["time"]:
                continue
            entry_price, entry_ts = _entry_fill(candles, i, fill, signal, spread_price, use_bid_ask)
            entry_index = i
            position_side = signal
            entry_units = _entry_units_for_decision(decision, units)
            position_units_opened = entry_units
            position_units_remaining = entry_units
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
                    pnl_pips_weighted=0.0,
                    pnl_usd=0.0,
                    mae_pips=0.0,
                    equity=balance,
                    hold_bars=0,
                    entry_components=_entry_components_for_signal(decision, signal),
                )
            )
            if position_side == "LONG":
                num_entries_long += 1
            else:
                num_entries_short += 1
            last_entry_bar_ts = candle["time"]
            continue

        if magnifier_map is None and signal and position_side and signal != position_side:
            if tv_panel and last_entry_bar_ts == candle["time"]:
                continue
            exit_price, exit_ts = _flip_exit_fill(candles, i, fill, position_side, spread_price, use_bid_ask)
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
            entry_price, entry_ts = _entry_fill(candles, i, fill, signal, spread_price, use_bid_ask)
            entry_index = i
            position_side = signal
            entry_units = _entry_units_for_decision(decision, units)
            position_units_opened = entry_units
            position_units_remaining = entry_units
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
                    pnl_pips_weighted=0.0,
                    pnl_usd=0.0,
                    mae_pips=0.0,
                    equity=balance,
                    hold_bars=0,
                    entry_components=_entry_components_for_signal(decision, signal),
                )
            )
            if position_side == "LONG":
                num_entries_long += 1
            else:
                num_entries_short += 1
            last_entry_bar_ts = candle["time"]

    avg_hold = (total_hold_bars / total_trades) if total_trades else 0.0
    tp1_pct = (tp1_hit_trades / total_trades * 100) if total_trades else 0.0
    metrics_extra = {
        "avg_hold_bars": avg_hold,
        "tp1_hit_pct": tp1_pct,
        "runner_pnl_usd": runner_pnl,
        "num_ambiguous_bars": float(num_ambiguous_bars),
        "num_same_bar_tp1_and_runner": float(num_same_bar_tp1_and_runner),
        "num_stopouts": float(num_stopouts),
        "entries_long": float(num_entries_long),
        "entries_short": float(num_entries_short),
        "runner_exits": float(num_runner_exits),
        "tp1_hits": float(tp1_hit_trades),
    }
    return trades, equity_curve, metrics_extra


def _decision_signal(decision: StrategyDecision) -> Optional[str]:
    if decision.action in {"ENTER_LONG", "FLIP_LONG"}:
        return "LONG"
    if decision.action in {"ENTER_SHORT", "FLIP_SHORT"}:
        return "SHORT"
    return None


def _entry_units_for_decision(decision: StrategyDecision, default_units: int) -> float:
    if decision.units is not None:
        explicit_units = float(abs(decision.units))
        if explicit_units > 0:
            return explicit_units
    metadata = decision.metadata if isinstance(decision.metadata, dict) else {}
    multiplier_raw = metadata.get("entry_units_multiplier")
    if multiplier_raw is None:
        return float(default_units)
    try:
        multiplier = float(multiplier_raw)
    except (TypeError, ValueError):
        return float(default_units)
    if multiplier <= 0:
        return float(default_units)
    return float(max(1.0, round(float(default_units) * multiplier)))


def _entry_components_for_signal(decision: StrategyDecision, signal: str) -> str:
    if not isinstance(decision.metadata, dict):
        return ""
    entry_diag = decision.metadata.get("entry_diag")
    if not isinstance(entry_diag, dict):
        return ""
    components = entry_diag.get("components")
    if not isinstance(components, dict):
        return ""
    side_key = "long" if signal == "LONG" else "short"
    side_components = components.get(side_key)
    if not isinstance(side_components, dict):
        return ""
    ordered = []
    for key in ("cross", "pullback", "rejoin", "continuation"):
        value = bool(side_components.get(key, False))
        ordered.append(f"{key}={'1' if value else '0'}")
    return ";".join(ordered)


def _propagate_entry_components(trades: list[TradeResult]) -> None:
    by_position: dict[tuple[str, str], str] = {}
    for trade in trades:
        if trade.leg == "ENTRY" and trade.entry_components:
            by_position[(trade.entry_ts, trade.side)] = trade.entry_components
    for trade in trades:
        if trade.entry_components:
            continue
        trade.entry_components = by_position.get((trade.entry_ts, trade.side), "")


def _entry_fill(
    candles: list[dict[str, Any]],
    index: int,
    fill: str,
    side: str,
    spread_price: float,
    use_bid_ask: bool,
) -> tuple[float, str]:
    base_price, ts = _fill_price(candles, index, fill)
    if not use_bid_ask:
        return base_price, ts
    if side == "LONG":
        return base_price + spread_price / 2, ts
    return base_price - spread_price / 2, ts


def _flip_exit_fill(
    candles: list[dict[str, Any]],
    index: int,
    fill: str,
    side: str,
    spread_price: float,
    use_bid_ask: bool,
) -> tuple[float, str]:
    base_price, ts = _fill_price(candles, index, fill)
    if not use_bid_ask:
        return base_price, ts
    return _exit_price(base_price, side, spread_price), ts


def _fill_price(candles: list[dict[str, Any]], index: int, fill: str) -> tuple[float, str]:
    if fill == "close":
        candle = candles[index]
        return float(candle["c"]), candle["time"]
    candle = candles[index + 1]
    return float(candle["o"]), candle["time"]


def _entry_price(price: float, side: str, spread_price: float) -> float:
    if side == "LONG":
        return price + spread_price / 2
    return price - spread_price / 2


def _exit_price(price: float, side: str, spread_price: float) -> float:
    if side == "LONG":
        return price - spread_price / 2
    return price + spread_price / 2


def _tp1_sl_prices(side: str, avg_price: float, tp1_pips: int, sl_pips: int, pip_factor: float) -> tuple[float, float]:
    if side == "LONG":
        return avg_price + tp1_pips * pip_factor, avg_price - sl_pips * pip_factor
    return avg_price - tp1_pips * pip_factor, avg_price + sl_pips * pip_factor


def _apply_profit_floor_stop(
    *,
    side: str,
    entry_price: float,
    runner_stop: float,
    favorable_extreme: float,
    pip_factor: float,
    trigger1_pips: int,
    lock1_pips: int,
    trigger2_pips: int,
    lock2_pips: int,
) -> float:
    # Two-step floor: once price moves favorably by trigger pips, lock minimum profit.
    if side == "LONG":
        mfe_pips = (favorable_extreme - entry_price) / pip_factor
        if trigger2_pips > 0 and mfe_pips >= trigger2_pips:
            runner_stop = max(runner_stop, entry_price + lock2_pips * pip_factor)
        elif trigger1_pips > 0 and mfe_pips >= trigger1_pips:
            runner_stop = max(runner_stop, entry_price + lock1_pips * pip_factor)
        return runner_stop

    mfe_pips = (entry_price - favorable_extreme) / pip_factor
    if trigger2_pips > 0 and mfe_pips >= trigger2_pips:
        runner_stop = min(runner_stop, entry_price - lock2_pips * pip_factor)
    elif trigger1_pips > 0 and mfe_pips >= trigger1_pips:
        runner_stop = min(runner_stop, entry_price - lock1_pips * pip_factor)
    return runner_stop


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
    pnl_pips, pnl_pips_weighted, pnl_usd = _calc_pnl(
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
            pnl_pips_weighted=pnl_pips_weighted,
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
) -> tuple[float, float, float]:
    direction = 1 if side == "LONG" else -1
    raw_pips = (exit_price - entry_price) / pip_factor * direction
    pnl_pips = raw_pips
    pnl_pips_weighted = raw_pips * (units / total_units) if total_units else 0.0
    raw_usd = (exit_price - entry_price) * units * direction
    return pnl_pips, pnl_pips_weighted, raw_usd


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


def _compute_compounded_metrics(
    *,
    trades: list[TradeResult],
    start_balance: float,
    participation: float,
    model: str,
    leverage: float,
    liquidation_floor: float,
) -> tuple[dict[str, float], list[EquityPoint], list[CompoundedAuditRow]]:
    if start_balance <= 0:
        return (
            {
                "compounded_participation": participation,
                "compounded_model_margin": 1.0 if model == "margin" else 0.0,
                "compounded_leverage": leverage if model == "margin" else 1.0,
                "compounded_start_balance_usd": 0.0,
                "compounded_total_pnl_usd": 0.0,
                "compounded_ending_balance_usd": 0.0,
                "compounded_max_drawdown_usd": 0.0,
                "compounded_first_entry_units": 0.0,
                "compounded_liquidated": 0.0,
            },
            [],
            [],
        )

    model = model.lower()
    if model not in {"notional", "margin"}:
        model = "notional"
    participation = min(max(participation, 0.0), 1.0)
    leverage = max(leverage, 0.0)
    liquidation_floor = max(liquidation_floor, 0.0)
    running_balance = start_balance
    compounded_equity: list[EquityPoint] = []
    compounded_audit: list[CompoundedAuditRow] = []
    first_entry_units: float = 0.0
    liquidated = False

    active_entry_ts: str | None = None
    active_side: str | None = None
    active_units_opened: float = 0.0
    active_entry_price: float = 0.0
    active_scale: float = 0.0
    active_dynamic_units_opened: float = 0.0

    for trade in trades:
        if trade.leg == "ENTRY":
            if liquidated:
                active_entry_ts = None
                active_side = None
                active_units_opened = 0.0
                active_entry_price = 0.0
                active_scale = 0.0
                continue
            active_entry_ts = trade.entry_ts
            active_side = trade.side
            active_units_opened = trade.units_opened
            active_entry_price = trade.entry_price
            if active_units_opened > 0 and active_entry_price > 0:
                effective_leverage = leverage if model == "margin" else 1.0
                dynamic_notional = running_balance * participation * effective_leverage
                dynamic_units = dynamic_notional / active_entry_price
                active_scale = dynamic_units / active_units_opened
                active_dynamic_units_opened = dynamic_units
                if first_entry_units == 0.0:
                    first_entry_units = dynamic_units
            else:
                active_scale = 0.0
                active_dynamic_units_opened = 0.0
            continue

        matches_active = (
            active_entry_ts is not None
            and trade.entry_ts == active_entry_ts
            and active_side is not None
            and trade.side == active_side
        )
        if not matches_active:
            if trade.units_opened > 0 and trade.entry_price > 0:
                effective_leverage = leverage if model == "margin" else 1.0
                dynamic_notional = running_balance * participation * effective_leverage
                dynamic_units = dynamic_notional / trade.entry_price
                scale = dynamic_units / trade.units_opened
                dynamic_units_opened = dynamic_units
            else:
                scale = 0.0
                dynamic_units_opened = 0.0
        else:
            scale = active_scale
            dynamic_units_opened = active_dynamic_units_opened

        if liquidated:
            continue

        balance_before = running_balance
        compounded_pnl = trade.pnl_usd * scale
        running_balance += trade.pnl_usd * scale
        if model == "margin" and running_balance <= liquidation_floor:
            running_balance = liquidation_floor
            liquidated = True
        balance_after = running_balance
        compounded_equity.append(EquityPoint(ts=trade.exit_ts, equity=running_balance))
        compounded_audit.append(
            CompoundedAuditRow(
                entry_ts=trade.entry_ts,
                exit_ts=trade.exit_ts,
                side=trade.side,
                leg=trade.leg,
                reason=trade.reason,
                base_units_opened=trade.units_opened,
                dynamic_units_opened=dynamic_units_opened,
                scale=scale,
                base_pnl_usd=trade.pnl_usd,
                compounded_pnl_usd=compounded_pnl,
                balance_before=balance_before,
                balance_after=balance_after,
                liquidated=liquidated,
            )
        )

    total_pnl = running_balance - start_balance
    max_dd = _max_drawdown([p.equity for p in compounded_equity])
    return (
        {
            "compounded_participation": participation,
            "compounded_model_margin": 1.0 if model == "margin" else 0.0,
            "compounded_leverage": leverage if model == "margin" else 1.0,
            "compounded_start_balance_usd": start_balance,
            "compounded_total_pnl_usd": total_pnl,
            "compounded_ending_balance_usd": running_balance,
            "compounded_max_drawdown_usd": max_dd,
            "compounded_first_entry_units": first_entry_units,
            "compounded_liquidated": 1.0 if liquidated else 0.0,
        },
        compounded_equity,
        compounded_audit,
    )


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
    total_pnl_pips = sum(t.pnl_pips_weighted for t in closed_trades)
    total_pnl_pips_raw = sum(t.pnl_pips for t in closed_trades)
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
    print(f"Total PnL (pips, weighted): {total_pnl_pips:.2f}")
    print(f"Total PnL (pips, raw): {total_pnl_pips_raw:.2f}")
    print(f"Max drawdown: {max_dd:.4f} USD")
    print(f"Sharpe (per trade): {sharpe:.4f}")
    print(f"Avg hold bars: {extra.get('avg_hold_bars', 0):.2f}")
    print(f"TP1 hit rate: {extra.get('tp1_hit_pct', 0):.2f}%")
    print(f"Runner PnL: {extra.get('runner_pnl_usd', 0):.4f} USD")
    print(f"Ambiguous bars: {extra.get('num_ambiguous_bars', 0):.0f}")
    print(f"Same-bar TP1+runner: {extra.get('num_same_bar_tp1_and_runner', 0):.0f}")
    print(f"Stopouts: {extra.get('num_stopouts', 0):.0f}")
    if extra.get("compounded_start_balance_usd", 0.0) > 0:
        comp_pct = extra.get("compounded_participation", 0.95) * 100
        model_margin = extra.get("compounded_model_margin", 0.0) >= 0.5
        model_label = "margin" if model_margin else "notional"
        leverage = extra.get("compounded_leverage", 1.0)
        sizing_label = f"{comp_pct:.0f}% {model_label}"
        if model_margin:
            sizing_label += f" @ {leverage:.2f}x"
        print(
            f"Compounded PnL ({sizing_label} sizing): "
            f"{extra.get('compounded_total_pnl_usd', 0.0):.4f} USD"
        )
        print(
            "Compounded ending balance: "
            f"{extra.get('compounded_ending_balance_usd', 0.0):.4f} USD"
        )
        print(
            "Compounded max drawdown: "
            f"{extra.get('compounded_max_drawdown_usd', 0.0):.4f} USD"
        )
        first_units = extra.get("compounded_first_entry_units", 0.0)
        if first_units > 0:
            print(f"Compounded first-entry units: {first_units:.2f}")
        if extra.get("compounded_liquidated", 0.0) >= 0.5:
            print("Compounded status: LIQUIDATED (equity reached floor)")

    return {
        "total_trades": float(total),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_pnl_pips_weighted": total_pnl_pips,
        "total_pnl_pips_raw": total_pnl_pips_raw,
        "total_pnl_usd": total_pnl,
        "max_drawdown_pips": max_dd_pips,
        "sharpe": sharpe,
        "avg_hold_bars": extra.get("avg_hold_bars", 0.0),
        "tp1_hit_pct": extra.get("tp1_hit_pct", 0.0),
        "runner_pnl_usd": extra.get("runner_pnl_usd", 0.0),
        "num_ambiguous_bars": extra.get("num_ambiguous_bars", 0.0),
        "num_same_bar_tp1_and_runner": extra.get("num_same_bar_tp1_and_runner", 0.0),
        "num_stopouts": extra.get("num_stopouts", 0.0),
        "entries_long": extra.get("entries_long", 0.0),
        "entries_short": extra.get("entries_short", 0.0),
        "runner_exits": extra.get("runner_exits", 0.0),
        "tp1_hits": extra.get("tp1_hits", 0.0),
        "compounded_participation": extra.get("compounded_participation", 0.0),
        "compounded_model_margin": extra.get("compounded_model_margin", 0.0),
        "compounded_leverage": extra.get("compounded_leverage", 1.0),
        "compounded_start_balance_usd": extra.get("compounded_start_balance_usd", 0.0),
        "compounded_total_pnl_usd": extra.get("compounded_total_pnl_usd", 0.0),
        "compounded_ending_balance_usd": extra.get("compounded_ending_balance_usd", 0.0),
        "compounded_max_drawdown_usd": extra.get("compounded_max_drawdown_usd", 0.0),
        "compounded_first_entry_units": extra.get("compounded_first_entry_units", 0.0),
        "compounded_liquidated": extra.get("compounded_liquidated", 0.0),
    }


def _write_reports(
    symbol: str,
    timeframe: str,
    trades: list[TradeResult],
    equity: list[EquityPoint],
    compounded_equity: list[EquityPoint],
    compounded_audit: list[CompoundedAuditRow],
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
                "pnl_pips_weighted",
                "pnl_usd",
                "mae_pips",
                "equity",
                "hold_bars",
                "entry_components",
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
                    f"{trade.pnl_pips_weighted:.2f}",
                    f"{trade.pnl_usd:.4f}",
                    f"{trade.mae_pips:.2f}",
                    f"{trade.equity:.4f}",
                    trade.hold_bars,
                    trade.entry_components,
                ]
            )

    equity_path = reports_dir / f"equity_{symbol}_{timeframe}_{date_tag}.csv"
    with equity_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "equity"])
        for point in equity:
            writer.writerow([point.ts, f"{point.equity:.4f}"])

    compounded_equity_path = reports_dir / f"compounded_equity_{symbol}_{timeframe}_{date_tag}.csv"
    with compounded_equity_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "equity"])
        for point in compounded_equity:
            writer.writerow([point.ts, f"{point.equity:.4f}"])

    compounded_audit_path = reports_dir / f"compounded_audit_{symbol}_{timeframe}_{date_tag}.csv"
    with compounded_audit_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "entry_ts",
                "exit_ts",
                "side",
                "leg",
                "reason",
                "base_units_opened",
                "dynamic_units_opened",
                "scale",
                "base_pnl_usd",
                "compounded_pnl_usd",
                "balance_before",
                "balance_after",
                "liquidated",
            ]
        )
        for row in compounded_audit:
            writer.writerow(
                [
                    row.entry_ts,
                    row.exit_ts,
                    row.side,
                    row.leg,
                    row.reason,
                    f"{row.base_units_opened:.4f}",
                    f"{row.dynamic_units_opened:.4f}",
                    f"{row.scale:.8f}",
                    f"{row.base_pnl_usd:.6f}",
                    f"{row.compounded_pnl_usd:.6f}",
                    f"{row.balance_before:.6f}",
                    f"{row.balance_after:.6f}",
                    "1" if row.liquidated else "0",
                ]
            )

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
        running += trade.pnl_pips_weighted
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


def _timeframe_to_seconds(timeframe: str) -> int:
    mapping = {
        "S5": 5,
        "S10": 10,
        "S15": 15,
        "S30": 30,
        "M1": 60,
        "M2": 120,
        "M4": 240,
        "M5": 300,
        "M10": 600,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H2": 7200,
        "H3": 10800,
        "H4": 14400,
        "H6": 21600,
        "H8": 28800,
        "H12": 43200,
        "D": 86400,
    }
    return mapping.get(timeframe.upper(), 900)


def _resolve_date_range(days: int, from_date: Optional[str], to_date: Optional[str]) -> tuple[datetime, datetime]:
    now_utc = datetime.now(timezone.utc)
    safe_now = now_utc - timedelta(minutes=1)
    if from_date and to_date:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
        if to_dt > safe_now:
            to_dt = safe_now
        if from_dt >= to_dt:
            raise SystemExit("--from must be earlier than --to")
        return from_dt, to_dt
    to_dt = safe_now
    from_dt = to_dt - timedelta(days=days)
    return from_dt, to_dt


def _to_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _parse_candle_time(candle: dict[str, Any]) -> datetime:
    token = candle["time"].replace("Z", "+00:00")
    # Python 3.10 only supports up to 6 fractional-second digits.
    token = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r".\1", token)
    return datetime.fromisoformat(token).astimezone(timezone.utc)


def _print_config_table(
    *,
    exec_profile: str,
    entry_timing: str,
    entries_close_only: bool,
    magnifier: str,
    magnify_policy: str,
    use_bid_ask: bool,
    spread_effective: float,
    bar_fill_policy: str,
    parity_debug: bool,
) -> None:
    rows = [
        ("Execution profile", exec_profile),
        ("Entry timing", entry_timing),
        ("Entries close-only", str(entries_close_only)),
        ("Intrabar exits via M1", str(magnifier == "M1")),
        ("Magnifier", magnifier),
        ("Magnifier policy", magnify_policy),
        ("Bid/ask used", str(use_bid_ask)),
        ("Spread effective", f"{spread_effective}"),
        ("Ambiguous policy", bar_fill_policy),
        ("Parity debug", str(parity_debug)),
    ]
    width = max(len(label) for label, _ in rows)
    print("Backtest Config")
    for label, value in rows:
        print(f"{label.ljust(width)} : {value}")
    print("")


def _write_parity_debug(
    *,
    symbol: str,
    timeframe: str,
    trades: list[TradeResult],
    from_dt: datetime,
    to_dt: datetime,
) -> None:
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = reports_dir / f"parity_debug_{symbol}_{timeframe}_{date_tag}.csv"

    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for trade in trades:
        if trade.leg == "ENTRY":
            if current:
                rows.append(current)
            current = {
                "entry_time": trade.entry_ts,
                "entry_price": trade.entry_price,
                "side": trade.side,
                "tp1_fill_time": "",
                "tp1_fill_price": "",
                "runner_exit_time": "",
                "runner_exit_price": "",
                "sl_time": "",
                "sl_price": "",
                "exit_reason": "",
                "pnl_pips": 0.0,
                "pnl_usd": 0.0,
            }
            continue
        if not current:
            continue
        if trade.leg == "TP1":
            current["tp1_fill_time"] = trade.exit_ts
            current["tp1_fill_price"] = trade.exit_price
            current["pnl_pips"] += trade.pnl_pips
            current["pnl_usd"] += trade.pnl_usd
        elif trade.leg == "RUNNER":
            current["runner_exit_time"] = trade.exit_ts
            current["runner_exit_price"] = trade.exit_price
            current["exit_reason"] = trade.reason
            current["pnl_pips"] += trade.pnl_pips
            current["pnl_usd"] += trade.pnl_usd
            rows.append(current)
            current = None
        elif trade.reason == "SL":
            current["sl_time"] = trade.exit_ts
            current["sl_price"] = trade.exit_price
            current["exit_reason"] = trade.reason
            current["pnl_pips"] += trade.pnl_pips
            current["pnl_usd"] += trade.pnl_usd
            rows.append(current)
            current = None
        else:
            current["exit_reason"] = trade.reason
            current["pnl_pips"] += trade.pnl_pips
            current["pnl_usd"] += trade.pnl_usd
            rows.append(current)
            current = None

    if current:
        rows.append(current)

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "entry_time",
                "entry_price",
                "side",
                "tp1_fill_time",
                "tp1_fill_price",
                "runner_exit_time",
                "runner_exit_price",
                "sl_time",
                "sl_price",
                "exit_reason",
                "pnl_pips",
                "pnl_usd",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()

# Example magnifier runs:
# python -m app.backtest.run --symbol NZD_USD --timeframe M15 --days 10 --spread_pips 2.6 --units 1000 --magnify_tf M1 --magnify_policy conservative
# python -m app.backtest.run --symbol NZD_USD --timeframe M15 --days 10 --spread_pips 2.6 --units 1000 --magnify_tf M1 --magnify_policy optimistic
# Example TV parity runs:
# python -m app.backtest.run --symbol NZD_USD --timeframe M15 --days 10 --units 1000 --spread_pips 2.6 --tv_parity true --magnify_policy conservative
# python -m app.backtest.run --symbol NZD_USD --timeframe M15 --days 10 --units 1000 --spread_pips 2.6 --tv_parity true --magnify_policy optimistic
# Example TV panel profile run:
# python -m app.backtest.run --symbol NZD_USD --timeframe M15 --days 10 --units 1000 --spread_pips 2.6 --exec_profile tv_panel
# Example live reality run with magnifier + bid/ask:
# python -m app.backtest.run --symbol AUD_USD --timeframe M15 --days 30 --units 7000 --spread_pips 1.6 --exec_profile live_reality --magnifier m1 --use_bid_ask true
