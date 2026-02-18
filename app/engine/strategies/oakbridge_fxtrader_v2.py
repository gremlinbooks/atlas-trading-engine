from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timezone
import re
from typing import Any

from app.engine.indicators import (
    atr,
    bars_since,
    crossover,
    crossunder,
    ema,
    highest,
    lowest,
    stoch_rsi,
)
from app.engine.strategy_base import Candle, Strategy, StrategyContext, StrategyDecision, StrategyState


class OakBridgeFxTraderV2(Strategy):
    def evaluate(self, candles: list[Candle], ctx: StrategyContext) -> StrategyDecision:
        if not ctx.config.enabled:
            return StrategyDecision("HOLD", "strategy disabled", {}, next_state=ctx.state)

        if len(candles) < max(ctx.config.slow_len, ctx.config.fast_len, 20):
            return StrategyDecision("HOLD", "insufficient candles", {}, next_state=ctx.state)

        closes = [c.c for c in candles]
        highs = [c.h for c in candles]
        lows = [c.l for c in candles]
        last = candles[-1]
        prev = candles[-2]

        fast_series = ema(closes, ctx.config.fast_len)
        slow_series = ema(closes, ctx.config.slow_len)
        fast_ema = fast_series[-1]
        slow_ema = slow_series[-1]
        prev_fast = fast_series[-2]
        prev_slow = slow_series[-2]

        spread_ema = fast_ema - slow_ema
        prev_spread = prev_fast - prev_slow
        cross_up = crossover(prev_spread, 0, spread_ema, 0) or (spread_ema > 0 and prev_spread <= 0)
        cross_dn = crossunder(prev_spread, 0, spread_ema, 0) or (spread_ema < 0 and prev_spread >= 0)

        long_signal_raw = cross_up
        short_signal_raw = cross_dn

        if ctx.config.invert_eurcad and _is_eurcad(ctx.symbol):
            long_signal_raw, short_signal_raw = short_signal_raw, long_signal_raw

        long_signal_base = (
            long_signal_raw and (last.c > slow_ema if ctx.config.use_bias else True)
        )
        short_signal_base = (
            short_signal_raw and (last.c < slow_ema if ctx.config.use_bias else True)
        )

        pb_low = lowest(lows, ctx.config.pb_lookback_bars)[-1]
        pb_high = highest(highs, ctx.config.pb_lookback_bars)[-1]
        pulled_to_slow_l = pb_low is not None and pb_low <= slow_ema
        pulled_to_slow_s = pb_high is not None and pb_high >= slow_ema
        recross_fast_l = crossover(prev.c, prev_fast, last.c, fast_ema)
        recross_fast_s = crossunder(prev.c, prev_fast, last.c, fast_ema)

        pullback_long = (
            ctx.config.pb_enabled
            and (last.c > slow_ema)
            and pulled_to_slow_l
            and recross_fast_l
        )
        pullback_short = (
            ctx.config.pb_enabled
            and (last.c < slow_ema)
            and pulled_to_slow_s
            and recross_fast_s
        )

        atr14 = (atr(candles, 14)[-1]) or 0.0
        # Continuation base must exclude the current bar; otherwise breakout checks are unreachable.
        base_hi = (
            highest(highs[:-1], ctx.config.base_max_bars)[-1]
            if len(highs) > ctx.config.base_max_bars
            else None
        )
        base_lo = (
            lowest(lows[:-1], ctx.config.base_max_bars)[-1]
            if len(lows) > ctx.config.base_max_bars
            else None
        )
        base_ok = (
            base_hi is not None
            and base_lo is not None
            and atr14 > 0
            and (base_hi - base_lo) <= ctx.config.base_max_range_atr * atr14
        )
        cont_long = (
            ctx.config.cont_enabled
            and base_ok
            and base_hi is not None
            and (last.c > base_hi)
            and (last.c > slow_ema)
        )
        cont_short = (
            ctx.config.cont_enabled
            and base_ok
            and base_lo is not None
            and (last.c < base_lo)
            and (last.c < slow_ema)
        )

        long_intent = long_signal_base or pullback_long or cont_long
        short_intent = short_signal_base or pullback_short or cont_short

        st_k, st_d, st_meta = _stoch_filters(closes, ctx)
        long_signal_ok, short_signal_ok = _apply_stoch_filter(
            long_intent, short_intent, st_k, st_d, st_meta, ctx
        )

        ok_time = _ok_time(last.ts, ctx.config)
        ok_spread, using_gate = _ok_spread(ctx, ctx.config)

        state = _update_pending_signals(
            ctx.state,
            using_gate=using_gate,
            ok_spread=ok_spread,
            long_signal_ok=long_signal_ok,
            short_signal_ok=short_signal_ok,
            long_intent=long_intent,
            short_intent=short_intent,
            hold_signal_bars=ctx.config.hold_signal_bars,
        )

        in_long = ctx.position.side == "LONG"
        in_short = ctx.position.side == "SHORT"
        flat = ctx.position.side is None

        if flat:
            state = replace(
                state,
                long_tp1_reached=False,
                short_tp1_reached=False,
                long_tp1_done=False,
                short_tp1_done=False,
                long_peak=None,
                short_trough=None,
            )

        state = _update_flat_state(state, flat, ctx.bar_index)

        decision = _handle_exits(candles, ctx, st_k, st_meta, state)
        if decision.action != "HOLD":
            return decision

        can_long = ok_time and ok_spread and long_signal_ok
        can_short = ok_time and ok_spread and short_signal_ok
        pending_long = (
            using_gate
            and ok_time
            and ok_spread
            and state.pend_long
            and state.pend_long_age <= ctx.config.hold_signal_bars
            and long_intent
        )
        pending_short = (
            using_gate
            and ok_time
            and ok_spread
            and state.pend_short
            and state.pend_short_age <= ctx.config.hold_signal_bars
            and short_intent
        )
        can_long = can_long or pending_long
        can_short = can_short or pending_short
        both = can_long and can_short
        take_long = can_long and (not both or spread_ema > 0)
        take_short = can_short and (not both or spread_ema < 0)

        bars_since_entry = _bars_since_entry(candles, ctx.position.entry_ts)
        min_hold_ok = (
            ctx.position.side is None
            or bars_since_entry is None
            or bars_since_entry >= ctx.config.min_hold_bars
        )

        second_chance_ok = (
            ctx.config.allow_second_chance
            and flat
            and (ctx.bar_index - (state.last_flat_index or ctx.bar_index)) <= ctx.config.reenter_within_bars
        )

        want_long = take_long and (flat or second_chance_ok or (in_short and min_hold_ok))
        want_short = take_short and (flat or second_chance_ok or (in_long and min_hold_ok))

        already_traded = state.last_trade_index == ctx.bar_index
        if not already_traded:
            if want_long:
                action = "ENTER_LONG" if flat else "FLIP_LONG"
                return StrategyDecision(
                    action,
                    "long entry",
                    _metadata(last, fast_ema, slow_ema, spread_ema, st_k, st_d, st_meta, ok_time, ok_spread),
                    next_state=replace(
                        state,
                        last_trade_candle_ts=last.ts,
                        last_trade_index=ctx.bar_index,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                        pend_long=False,
                        pend_short=False,
                        pend_long_age=0,
                        pend_short_age=0,
                    ),
                )
            if want_short:
                action = "ENTER_SHORT" if flat else "FLIP_SHORT"
                return StrategyDecision(
                    action,
                    "short entry",
                    _metadata(last, fast_ema, slow_ema, spread_ema, st_k, st_d, st_meta, ok_time, ok_spread),
                    next_state=replace(
                        state,
                        last_trade_candle_ts=last.ts,
                        last_trade_index=ctx.bar_index,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                        pend_long=False,
                        pend_short=False,
                        pend_long_age=0,
                        pend_short_age=0,
                    ),
                )

        return StrategyDecision(
            "HOLD",
            "no entry",
            _metadata(last, fast_ema, slow_ema, spread_ema, st_k, st_d, st_meta, ok_time, ok_spread),
            next_state=state,
        )


def _handle_exits(
    candles: list[Candle],
    ctx: StrategyContext,
    st_k: float | None,
    st_meta: dict[str, Any],
    state: StrategyState,
) -> StrategyDecision:
    if ctx.position.side is None:
        return StrategyDecision("HOLD", "flat", {}, next_state=state)

    last = candles[-1]
    pip = 0.01 if "JPY" in ctx.symbol.upper() else 0.0001
    avg = ctx.position.avg_price

    long_tp1 = avg + ctx.config.tp1_pips * pip
    long_sl = avg - ctx.config.sl_pips * pip
    short_tp1 = avg - ctx.config.tp1_pips * pip
    short_sl = avg + ctx.config.sl_pips * pip

    long_tp1_reached = state.long_tp1_reached
    short_tp1_reached = state.short_tp1_reached
    long_tp1_done = state.long_tp1_done
    short_tp1_done = state.short_tp1_done
    long_peak = state.long_peak
    short_trough = state.short_trough

    if ctx.position.side == "LONG":
        short_tp1_reached = False
        short_tp1_done = False
        short_trough = None
        if last.l <= long_sl and not long_tp1_done:
            return StrategyDecision(
                "EXIT",
                "stop loss",
                {"stop": long_sl},
                price=long_sl,
                next_state=replace(
                    state,
                    long_tp1_done=True,
                    long_tp1_reached=long_tp1_reached,
                    short_tp1_reached=short_tp1_reached,
                    short_tp1_done=short_tp1_done,
                    short_trough=short_trough,
                ),
            )
        if last.h >= long_tp1 and not long_tp1_done:
            long_tp1_reached = True
            long_tp1_done = True
            long_peak = max(long_peak or last.h, last.h)
            return StrategyDecision(
                "PARTIAL_TP1",
                "tp1 reached",
                {"tp1": long_tp1},
                price=long_tp1,
                next_state=replace(
                    state,
                    long_tp1_reached=long_tp1_reached,
                    long_tp1_done=long_tp1_done,
                    long_peak=long_peak,
                    short_tp1_reached=short_tp1_reached,
                    short_tp1_done=short_tp1_done,
                    short_trough=short_trough,
                ),
            )
        if last.h >= long_tp1:
            long_tp1_reached = True
        if long_tp1_reached:
            long_peak = max(long_peak or last.h, last.h)
            trail_frac = ctx.config.trail_drawdown_pct / 100
            long_trail_candidate = long_peak * (1 - trail_frac)
            long_be = avg + ctx.config.be_lock_pips * pip
            long_runner_stop = max(long_be, long_trail_candidate)
            long_runner_stop = _apply_profit_floor_stop(
                side=ctx.position.side,
                entry_price=avg,
                runner_stop=long_runner_stop,
                favorable_extreme=long_peak,
                pip_factor=pip,
                trigger1_pips=ctx.config.profit_floor1_trigger_pips,
                lock1_pips=ctx.config.profit_floor1_lock_pips,
                trigger2_pips=ctx.config.profit_floor2_trigger_pips,
                lock2_pips=ctx.config.profit_floor2_lock_pips,
            )
            if ctx.config.use_stoch_exit and st_meta.get("stKxDn") and (st_k or 0) > ctx.config.st_ob:
                tight = last.c - ctx.config.st_tight_pips * pip
                long_runner_stop = max(long_runner_stop, tight)
            if last.l <= long_runner_stop:
                return StrategyDecision(
                    "EXIT",
                    "runner stop",
                    {"runner_stop": long_runner_stop},
                    price=long_runner_stop,
                    next_state=replace(
                        state,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                    ),
                )
    else:
        long_tp1_reached = False
        long_tp1_done = False
        long_peak = None
        if last.h >= short_sl and not short_tp1_done:
            return StrategyDecision(
                "EXIT",
                "stop loss",
                {"stop": short_sl},
                price=short_sl,
                next_state=replace(
                    state,
                    short_tp1_done=True,
                    short_tp1_reached=short_tp1_reached,
                    long_tp1_reached=long_tp1_reached,
                    long_tp1_done=long_tp1_done,
                    long_peak=long_peak,
                ),
            )
        if last.l <= short_tp1 and not short_tp1_done:
            short_tp1_reached = True
            short_tp1_done = True
            short_trough = min(short_trough or last.l, last.l)
            return StrategyDecision(
                "PARTIAL_TP1",
                "tp1 reached",
                {"tp1": short_tp1},
                price=short_tp1,
                next_state=replace(
                    state,
                    short_tp1_reached=short_tp1_reached,
                    short_tp1_done=short_tp1_done,
                    short_trough=short_trough,
                    long_tp1_reached=long_tp1_reached,
                    long_tp1_done=long_tp1_done,
                    long_peak=long_peak,
                ),
            )
        if last.l <= short_tp1:
            short_tp1_reached = True
        if short_tp1_reached:
            short_trough = min(short_trough or last.l, last.l)
            trail_frac = ctx.config.trail_drawdown_pct / 100
            short_trail_candidate = short_trough * (1 + trail_frac)
            short_be = avg - ctx.config.be_lock_pips * pip
            short_runner_stop = min(short_be, short_trail_candidate)
            short_runner_stop = _apply_profit_floor_stop(
                side=ctx.position.side,
                entry_price=avg,
                runner_stop=short_runner_stop,
                favorable_extreme=short_trough,
                pip_factor=pip,
                trigger1_pips=ctx.config.profit_floor1_trigger_pips,
                lock1_pips=ctx.config.profit_floor1_lock_pips,
                trigger2_pips=ctx.config.profit_floor2_trigger_pips,
                lock2_pips=ctx.config.profit_floor2_lock_pips,
            )
            if ctx.config.use_stoch_exit and st_meta.get("stKxUp") and (st_k or 0) < ctx.config.st_os:
                tight = last.c + ctx.config.st_tight_pips * pip
                short_runner_stop = min(short_runner_stop, tight)
            if last.h >= short_runner_stop:
                return StrategyDecision(
                    "EXIT",
                    "runner stop",
                    {"runner_stop": short_runner_stop},
                    price=short_runner_stop,
                    next_state=replace(
                        state,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                    ),
                )

    return StrategyDecision(
        "HOLD",
        "no exit",
        {},
        next_state=replace(
            state,
            long_tp1_reached=long_tp1_reached,
            short_tp1_reached=short_tp1_reached,
            long_tp1_done=long_tp1_done,
            short_tp1_done=short_tp1_done,
            long_peak=long_peak,
            short_trough=short_trough,
        ),
    )


def _apply_profit_floor_stop(
    *,
    side: str | None,
    entry_price: float,
    runner_stop: float,
    favorable_extreme: float,
    pip_factor: float,
    trigger1_pips: int,
    lock1_pips: int,
    trigger2_pips: int,
    lock2_pips: int,
) -> float:
    if side == "LONG":
        mfe_pips = (favorable_extreme - entry_price) / pip_factor
        if trigger2_pips > 0 and mfe_pips >= trigger2_pips:
            return max(runner_stop, entry_price + lock2_pips * pip_factor)
        if trigger1_pips > 0 and mfe_pips >= trigger1_pips:
            return max(runner_stop, entry_price + lock1_pips * pip_factor)
        return runner_stop

    mfe_pips = (entry_price - favorable_extreme) / pip_factor
    if trigger2_pips > 0 and mfe_pips >= trigger2_pips:
        return min(runner_stop, entry_price - lock2_pips * pip_factor)
    if trigger1_pips > 0 and mfe_pips >= trigger1_pips:
        return min(runner_stop, entry_price - lock1_pips * pip_factor)
    return runner_stop


def _metadata(
    last: Candle,
    fast_ema: float,
    slow_ema: float,
    spread_ema: float,
    st_k: float | None,
    st_d: float | None,
    st_meta: dict[str, Any],
    ok_time: bool,
    ok_spread: bool,
) -> dict[str, Any]:
    return {
        "fast_ema": fast_ema,
        "slow_ema": slow_ema,
        "spread_ema": spread_ema,
        "ok_time": ok_time,
        "ok_spread": ok_spread,
        "stoch": {
            "k": st_k,
            "d": st_d,
            **st_meta,
        },
        "last": {
            "ts": last.ts,
            "o": last.o,
            "h": last.h,
            "l": last.l,
            "c": last.c,
            "volume": last.volume,
        },
    }


def _stoch_filters(
    closes: list[float],
    ctx: StrategyContext,
) -> tuple[float | None, float | None, dict[str, Any]]:
    st_k_series, st_d_series = stoch_rsi(
        closes,
        ctx.config.st_rsi_len,
        ctx.config.st_stoch_len,
        ctx.config.st_k_len,
        ctx.config.st_d_len,
    )
    st_k = st_k_series[-1] if st_k_series else None
    st_d = st_d_series[-1] if st_d_series else None

    crosses_up: list[bool] = []
    crosses_dn: list[bool] = []
    for i in range(1, len(st_k_series)):
        if (
            st_k_series[i] is None
            or st_d_series[i] is None
            or st_k_series[i - 1] is None
            or st_d_series[i - 1] is None
        ):
            crosses_up.append(False)
            crosses_dn.append(False)
            continue
        crosses_up.append(
            crossover(st_k_series[i - 1], st_d_series[i - 1], st_k_series[i], st_d_series[i])
        )
        crosses_dn.append(
            crossunder(st_k_series[i - 1], st_d_series[i - 1], st_k_series[i], st_d_series[i])
        )

    st_kx_up = crosses_up[-1] if crosses_up else False
    st_kx_dn = crosses_dn[-1] if crosses_dn else False
    bars_up = bars_since(crosses_up) if crosses_up else None
    bars_dn = bars_since(crosses_dn) if crosses_dn else None

    return st_k, st_d, {
        "stKxUp": st_kx_up,
        "stKxDn": st_kx_dn,
        "barsSinceKxUp": bars_up,
        "barsSinceKxDn": bars_dn,
    }


def _apply_stoch_filter(
    long_intent: bool,
    short_intent: bool,
    st_k: float | None,
    st_d: float | None,
    st_meta: dict[str, Any],
    ctx: StrategyContext,
) -> tuple[bool, bool]:
    mode = ctx.config.stoch_entry_mode
    if mode == "Off":
        return long_intent, short_intent

    if st_k is None or st_d is None:
        return False, False

    if mode == "ExtremesOnly":
        return long_intent and st_k < ctx.config.st_ob, short_intent and st_k > ctx.config.st_os

    if mode == "StrictFilter":
        long_ok = st_k > st_d and st_k < ctx.config.st_ob
        short_ok = st_k < st_d and st_k > ctx.config.st_os
        if ctx.config.st_recent > 0:
            bars_up = st_meta.get("barsSinceKxUp")
            bars_dn = st_meta.get("barsSinceKxDn")
            long_ok = long_ok and (bars_up is not None and bars_up <= ctx.config.st_recent)
            short_ok = short_ok and (bars_dn is not None and bars_dn <= ctx.config.st_recent)
        return long_intent and long_ok, short_intent and short_ok

    return long_intent, short_intent


def _ok_time(ts: str, config) -> bool:
    if config.quick_relax:
        return True
    if not config.block_trades:
        return True
    dt = _parse_iso_ts(ts).astimezone(timezone.utc)
    if config.use_day_mask:
        weekday = dt.weekday()
        day_blocked = {
            0: config.block_mon,
            1: config.block_tue,
            2: config.block_wed,
            3: config.block_thu,
            4: config.block_fri,
            5: config.block_sat,
            6: config.block_sun,
        }.get(weekday, False)
        if not day_blocked:
            return True

    if config.block_session:
        start_str, end_str = config.block_session.split("-")
        start = _parse_hhmm(start_str)
        end = _parse_hhmm(end_str)
        now = dt.time()
        if start <= end:
            in_block = start <= now <= end
        else:
            in_block = now >= start or now <= end
        return not in_block
    return True


def _parse_iso_ts(ts: str) -> datetime:
    token = ts.replace("Z", "+00:00")
    token = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r".\1", token)
    return datetime.fromisoformat(token)


def _ok_spread(ctx: StrategyContext, config) -> tuple[bool, bool]:
    using_gate = config.use_spread_gate and (ctx.is_realtime or config.apply_on_history)
    if not using_gate:
        return True, False
    if not ctx.spread_available or ctx.spread_pips is None:
        return True, True
    max_allowed = config.max_spread_pips * (config.aggr_spread_factor if ctx.is_realtime else 1.0)
    return ctx.spread_pips <= max_allowed, True


def _update_pending_signals(
    state: StrategyState,
    *,
    using_gate: bool,
    ok_spread: bool,
    long_signal_ok: bool,
    short_signal_ok: bool,
    long_intent: bool,
    short_intent: bool,
    hold_signal_bars: int,
) -> StrategyState:
    if not using_gate:
        return replace(state, pend_long=False, pend_short=False, pend_long_age=0, pend_short_age=0)

    pend_long = state.pend_long
    pend_short = state.pend_short
    pend_long_age = state.pend_long_age
    pend_short_age = state.pend_short_age

    if long_signal_ok and not ok_spread:
        pend_long = True
        pend_long_age = 0
    elif pend_long:
        pend_long_age += 1
        if not long_intent or pend_long_age > hold_signal_bars:
            pend_long = False
            pend_long_age = 0

    if short_signal_ok and not ok_spread:
        pend_short = True
        pend_short_age = 0
    elif pend_short:
        pend_short_age += 1
        if not short_intent or pend_short_age > hold_signal_bars:
            pend_short = False
            pend_short_age = 0

    return replace(
        state,
        pend_long=pend_long,
        pend_short=pend_short,
        pend_long_age=pend_long_age,
        pend_short_age=pend_short_age,
    )


def _update_flat_state(state: StrategyState, flat: bool, bar_index: int) -> StrategyState:
    if flat:
        if state.last_flat_index is None:
            return replace(state, last_flat_index=bar_index)
        return state
    return replace(state, last_flat_index=None)


def _is_eurcad(symbol: str) -> bool:
    symbol_upper = symbol.upper().replace("_", "")
    return "EURCAD" in symbol_upper


def _parse_hhmm(value: str) -> time:
    token = value.strip()
    if ":" in token:
        hour, minute = token.split(":")
    else:
        if len(token) != 4 or not token.isdigit():
            raise ValueError(f"Invalid HHMM value: {value}")
        hour, minute = token[:2], token[2:]
    return time(int(hour), int(minute))


def _bars_since_entry(candles: list[Candle], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    for index, candle in enumerate(candles):
        if candle.ts == entry_ts:
            return len(candles) - 1 - index
    return None
