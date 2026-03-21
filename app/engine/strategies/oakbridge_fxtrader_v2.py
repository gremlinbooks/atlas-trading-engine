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
            and ctx.config.pb_enabled_long
            and (last.c > slow_ema)
            and pulled_to_slow_l
            and recross_fast_l
        )
        pullback_short = (
            ctx.config.pb_enabled
            and ctx.config.pb_enabled_short
            and (last.c < slow_ema)
            and pulled_to_slow_s
            and recross_fast_s
        )
        rejoin_long = (
            ctx.config.rejoin_enabled
            and ctx.config.rejoin_enabled_long
            and (last.c > slow_ema)
            and (spread_ema > 0)
            and recross_fast_l
        )
        rejoin_short = (
            ctx.config.rejoin_enabled
            and ctx.config.rejoin_enabled_short
            and (last.c < slow_ema)
            and (spread_ema < 0)
            and recross_fast_s
        )
        long_components = {
            "cross": long_signal_base,
            "pullback": pullback_long,
            "rejoin": rejoin_long,
            "continuation": False,
        }
        short_components = {
            "cross": short_signal_base,
            "pullback": pullback_short,
            "rejoin": rejoin_short,
            "continuation": False,
        }

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
        long_components["continuation"] = cont_long
        short_components["continuation"] = cont_short

        long_intent = long_signal_base or pullback_long or rejoin_long or cont_long
        short_intent = short_signal_base or pullback_short or rejoin_short or cont_short

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
                drawdown_bars=0,
            )

        state = _update_flat_state(state, flat, ctx.bar_index)

        decision = _handle_exits(candles, ctx, st_k, st_meta, state)
        if decision.action != "HOLD" or ctx.exit_only:
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
        no_intent_override = _compute_no_intent_override(
            candles=candles,
            ctx=ctx,
            last=last,
            atr14=atr14,
            slow_ema=slow_ema,
            spread_ema=spread_ema,
            ok_time=ok_time,
            ok_spread=ok_spread,
            long_intent=long_intent,
            short_intent=short_intent,
            flat=flat,
        )
        override_long = bool(no_intent_override.get("allow_long", False))
        override_short = bool(no_intent_override.get("allow_short", False))
        hour_strict = _compute_hour_strict_gate(
            ctx=ctx,
            last_ts=last.ts,
            long_components=long_components,
            short_components=short_components,
        )
        if not bool(hour_strict.get("allow_long", True)):
            can_long = False
            take_long = False
        if not bool(hour_strict.get("allow_short", True)):
            can_short = False
            take_short = False

        bars_since_entry = _bars_since_entry(candles, ctx.position.entry_ts)
        min_hold_ok = (
            ctx.position.side is None
            or bars_since_entry is None
            or bars_since_entry >= ctx.config.min_hold_bars
        )
        flip_long_ok = in_short and _can_flip_position(
            ctx=ctx,
            last_price=last.c,
            target_side="LONG",
            min_hold_ok=min_hold_ok,
        )
        flip_short_ok = in_long and _can_flip_position(
            ctx=ctx,
            last_price=last.c,
            target_side="SHORT",
            min_hold_ok=min_hold_ok,
        )

        second_chance_ok = (
            ctx.config.allow_second_chance
            and flat
            and (ctx.bar_index - (state.last_flat_index or ctx.bar_index)) <= ctx.config.reenter_within_bars
        )

        want_long = (take_long and (flat or second_chance_ok or flip_long_ok)) or override_long
        want_short = (take_short and (flat or second_chance_ok or flip_short_ok)) or override_short
        blocked_reasons: list[str] = []
        if not long_intent and not short_intent:
            if not override_long and not override_short:
                blocked_reasons.append("no_intent")
        else:
            if not ok_time:
                blocked_reasons.append("time_gate")
            if not ok_spread:
                blocked_reasons.append("spread_gate")
            if (long_intent and not long_signal_ok) or (short_intent and not short_signal_ok):
                blocked_reasons.append("stoch_filter")
            if not flat and not (flip_long_ok or flip_short_ok):
                blocked_reasons.append("position_lock")
        entry_diag = {
            "components": {
                "long": long_components,
                "short": short_components,
            },
            "intent": {"long": long_intent, "short": short_intent},
            "signal_ok": {"long": long_signal_ok, "short": short_signal_ok},
            "pending": {"long": pending_long, "short": pending_short},
            "can": {"long": can_long, "short": can_short},
            "take": {"long": take_long, "short": take_short},
            "want": {"long": want_long, "short": want_short},
            "flip_ok": {"long": flip_long_ok, "short": flip_short_ok},
            "position": {"flat": flat, "in_long": in_long, "in_short": in_short},
            "no_intent_override": no_intent_override,
            "hour_strict": hour_strict,
            "blocked_reasons": blocked_reasons,
        }

        already_traded = state.last_trade_index == ctx.bar_index
        if not already_traded:
            if want_long:
                action = "ENTER_LONG" if flat else "FLIP_LONG"
                is_override_entry = override_long and not take_long
                reason = "long override entry" if is_override_entry else "long entry"
                metadata = _metadata(
                    last,
                    fast_ema,
                    slow_ema,
                    spread_ema,
                    st_k,
                    st_d,
                    st_meta,
                    ok_time,
                    ok_spread,
                    entry_diag=entry_diag,
                )
                if is_override_entry:
                    metadata["entry_units_multiplier"] = float(
                        no_intent_override.get("risk_scale", ctx.config.no_intent_override_risk_scale)
                    )
                elif bool(hour_strict.get("active", False)):
                    metadata["entry_units_multiplier"] = float(hour_strict.get("risk_scale", 1.0))
                return StrategyDecision(
                    action,
                    reason,
                    metadata,
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
                        drawdown_bars=0,
                    ),
                )
            if want_short:
                action = "ENTER_SHORT" if flat else "FLIP_SHORT"
                is_override_entry = override_short and not take_short
                reason = "short override entry" if is_override_entry else "short entry"
                metadata = _metadata(
                    last,
                    fast_ema,
                    slow_ema,
                    spread_ema,
                    st_k,
                    st_d,
                    st_meta,
                    ok_time,
                    ok_spread,
                    entry_diag=entry_diag,
                )
                if is_override_entry:
                    metadata["entry_units_multiplier"] = float(
                        no_intent_override.get("risk_scale", ctx.config.no_intent_override_risk_scale)
                    )
                elif bool(hour_strict.get("active", False)):
                    metadata["entry_units_multiplier"] = float(hour_strict.get("risk_scale", 1.0))
                return StrategyDecision(
                    action,
                    reason,
                    metadata,
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
                        drawdown_bars=0,
                    ),
                )

        hold_reason = (
            "no entry" if not blocked_reasons else f"no entry ({','.join(blocked_reasons)})"
        )
        return StrategyDecision(
            "HOLD",
            hold_reason,
            _metadata(
                last,
                fast_ema,
                slow_ema,
                spread_ema,
                st_k,
                st_d,
                st_meta,
                ok_time,
                ok_spread,
                entry_diag=entry_diag,
            ),
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
    drawdown_bars = state.drawdown_bars
    bars_since_entry = _bars_since_entry(candles, ctx.position.entry_ts)
    closes = [c.c for c in candles]
    fast_series = ema(closes, ctx.config.fast_len)
    slow_series = ema(closes, ctx.config.slow_len)
    spread_ema = fast_series[-1] - slow_series[-1]

    if ctx.config.max_hold_bars > 0 and bars_since_entry is not None and bars_since_entry >= ctx.config.max_hold_bars:
        return StrategyDecision(
            "EXIT",
            "max hold stop",
            {"bars_since_entry": bars_since_entry, "max_hold_bars": ctx.config.max_hold_bars},
            price=last.c,
            next_state=replace(
                state,
                long_tp1_reached=False,
                short_tp1_reached=False,
                long_tp1_done=False,
                short_tp1_done=False,
                long_peak=None,
                short_trough=None,
                drawdown_bars=0,
            ),
        )

    if ctx.position.side == "LONG":
        short_tp1_reached = False
        short_tp1_done = False
        short_trough = None
        pnl_pips = (last.c - avg) / pip
        early_loss_cut_pips = abs(ctx.config.early_loss_cut_pips)
        if early_loss_cut_pips > 0 and pnl_pips <= -early_loss_cut_pips:
            return StrategyDecision(
                "EXIT",
                "early loss cut",
                {
                    "pnl_pips": pnl_pips,
                    "early_loss_cut_pips": ctx.config.early_loss_cut_pips,
                },
                price=last.c,
                next_state=replace(
                    state,
                    long_tp1_reached=False,
                    short_tp1_reached=False,
                    long_tp1_done=False,
                    short_tp1_done=False,
                    long_peak=None,
                    short_trough=None,
                    drawdown_bars=0,
                ),
            )
        momentum_fail_exit_pips = abs(ctx.config.momentum_fail_exit_pips)
        if momentum_fail_exit_pips > 0 and spread_ema < 0 and pnl_pips <= -momentum_fail_exit_pips:
            return StrategyDecision(
                "EXIT",
                "momentum fail stop",
                {
                    "pnl_pips": pnl_pips,
                    "spread_ema": spread_ema,
                    "momentum_fail_exit_pips": ctx.config.momentum_fail_exit_pips,
                },
                price=last.c,
                next_state=replace(
                    state,
                    long_tp1_reached=False,
                    short_tp1_reached=False,
                    long_tp1_done=False,
                    short_tp1_done=False,
                    long_peak=None,
                    short_trough=None,
                    drawdown_bars=0,
                ),
            )
        if ctx.config.drawdown_stop_bars > 0 and ctx.config.drawdown_stop_pips > 0:
            drawdown_bars = drawdown_bars + 1 if pnl_pips <= -abs(ctx.config.drawdown_stop_pips) else 0
            if drawdown_bars >= ctx.config.drawdown_stop_bars:
                return StrategyDecision(
                    "EXIT",
                    "drawdown time stop",
                    {
                        "pnl_pips": pnl_pips,
                        "drawdown_bars": drawdown_bars,
                        "drawdown_stop_pips": ctx.config.drawdown_stop_pips,
                        "drawdown_stop_bars": ctx.config.drawdown_stop_bars,
                    },
                    price=last.c,
                    next_state=replace(
                        state,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                        drawdown_bars=0,
                    ),
                )
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
                    drawdown_bars=0,
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
                    drawdown_bars=0,
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
                        drawdown_bars=0,
                    ),
                )
    else:
        long_tp1_reached = False
        long_tp1_done = False
        long_peak = None
        pnl_pips = (avg - last.c) / pip
        early_loss_cut_pips = abs(ctx.config.early_loss_cut_pips)
        if early_loss_cut_pips > 0 and pnl_pips <= -early_loss_cut_pips:
            return StrategyDecision(
                "EXIT",
                "early loss cut",
                {
                    "pnl_pips": pnl_pips,
                    "early_loss_cut_pips": ctx.config.early_loss_cut_pips,
                },
                price=last.c,
                next_state=replace(
                    state,
                    long_tp1_reached=False,
                    short_tp1_reached=False,
                    long_tp1_done=False,
                    short_tp1_done=False,
                    long_peak=None,
                    short_trough=None,
                    drawdown_bars=0,
                ),
            )
        momentum_fail_exit_pips = abs(ctx.config.momentum_fail_exit_pips)
        if momentum_fail_exit_pips > 0 and spread_ema > 0 and pnl_pips <= -momentum_fail_exit_pips:
            return StrategyDecision(
                "EXIT",
                "momentum fail stop",
                {
                    "pnl_pips": pnl_pips,
                    "spread_ema": spread_ema,
                    "momentum_fail_exit_pips": ctx.config.momentum_fail_exit_pips,
                },
                price=last.c,
                next_state=replace(
                    state,
                    long_tp1_reached=False,
                    short_tp1_reached=False,
                    long_tp1_done=False,
                    short_tp1_done=False,
                    long_peak=None,
                    short_trough=None,
                    drawdown_bars=0,
                ),
            )
        if ctx.config.drawdown_stop_bars > 0 and ctx.config.drawdown_stop_pips > 0:
            drawdown_bars = drawdown_bars + 1 if pnl_pips <= -abs(ctx.config.drawdown_stop_pips) else 0
            if drawdown_bars >= ctx.config.drawdown_stop_bars:
                return StrategyDecision(
                    "EXIT",
                    "drawdown time stop",
                    {
                        "pnl_pips": pnl_pips,
                        "drawdown_bars": drawdown_bars,
                        "drawdown_stop_pips": ctx.config.drawdown_stop_pips,
                        "drawdown_stop_bars": ctx.config.drawdown_stop_bars,
                    },
                    price=last.c,
                    next_state=replace(
                        state,
                        long_tp1_reached=False,
                        short_tp1_reached=False,
                        long_tp1_done=False,
                        short_tp1_done=False,
                        long_peak=None,
                        short_trough=None,
                        drawdown_bars=0,
                    ),
                )
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
                    drawdown_bars=0,
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
                    drawdown_bars=0,
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
                        drawdown_bars=0,
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
            drawdown_bars=drawdown_bars,
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
    entry_diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
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
    if entry_diag:
        out["entry_diag"] = entry_diag
    return out


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
    if _is_hour_blocked(dt.hour, config.block_entry_hours_utc):
        return False
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


def _is_hour_blocked(hour_utc: int, hour_list: str) -> bool:
    return hour_utc in _parse_blocked_hours(hour_list)


def _parse_blocked_hours(hour_list: str) -> set[int]:
    blocked: set[int] = set()
    for token in hour_list.split(","):
        value = token.strip()
        if not value:
            continue
        if not value.isdigit():
            continue
        hour = int(value)
        if 0 <= hour <= 23:
            blocked.add(hour)
    return blocked


def _compute_no_intent_override(
    *,
    candles: list[Candle],
    ctx: StrategyContext,
    last: Candle,
    atr14: float,
    slow_ema: float,
    spread_ema: float,
    ok_time: bool,
    ok_spread: bool,
    long_intent: bool,
    short_intent: bool,
    flat: bool,
) -> dict[str, Any]:
    risk_scale = max(0.01, min(1.0, float(ctx.config.no_intent_override_risk_scale)))
    out: dict[str, Any] = {
        "enabled": bool(ctx.config.no_intent_override_enabled),
        "evaluated": False,
        "allow_long": False,
        "allow_short": False,
        "risk_scale": risk_scale,
    }
    if not out["enabled"] or long_intent or short_intent or not flat:
        return out

    dt = _parse_iso_ts(last.ts).astimezone(timezone.utc)
    hours = _parse_blocked_hours(ctx.config.no_intent_override_hours_utc)
    hour_match = dt.hour in hours
    out["evaluated"] = True
    out["hour"] = dt.hour
    out["hour_match"] = hour_match
    out["ok_time"] = ok_time
    out["ok_spread"] = ok_spread
    if not hour_match or not ok_time or not ok_spread:
        return out

    bar_range = max(0.0, last.h - last.l)
    if bar_range <= 0 or atr14 <= 0:
        out["bar_range"] = bar_range
        out["atr14"] = atr14
        return out

    atr_mult = bar_range / atr14
    body_ratio = abs(last.c - last.o) / bar_range
    close_pos = (last.c - last.l) / bar_range
    close_extreme_frac = max(0.0, min(1.0, float(ctx.config.no_intent_override_close_extreme_frac)))
    long_direction_ok = close_pos >= (1.0 - close_extreme_frac) and spread_ema > 0 and last.c > slow_ema
    short_direction_ok = close_pos <= close_extreme_frac and spread_ema < 0 and last.c < slow_ema

    lookback = max(5, int(ctx.config.no_intent_override_volume_lookback))
    prior_volumes = [float(c.volume) for c in candles[-(lookback + 1) : -1]]
    if not prior_volumes:
        prior_volumes = [float(c.volume) for c in candles[:-1]]
    vol_pct = max(0.0, min(100.0, float(ctx.config.no_intent_override_volume_percentile)))
    vol_threshold = _percentile(prior_volumes, vol_pct) if prior_volumes else 0.0
    volume_ok = float(last.volume) >= vol_threshold if vol_threshold > 0 else False

    atr_mult_ok = atr_mult >= float(ctx.config.no_intent_override_atr_mult)
    body_ratio_ok = body_ratio >= float(ctx.config.no_intent_override_body_ratio_min)
    quality_ok = atr_mult_ok and body_ratio_ok and volume_ok

    out.update(
        {
            "bar_range": bar_range,
            "atr14": atr14,
            "atr_mult": atr_mult,
            "atr_mult_ok": atr_mult_ok,
            "body_ratio": body_ratio,
            "body_ratio_ok": body_ratio_ok,
            "close_pos": close_pos,
            "volume_last": float(last.volume),
            "volume_threshold": vol_threshold,
            "volume_ok": volume_ok,
            "quality_ok": quality_ok,
            "direction_ok": {"long": long_direction_ok, "short": short_direction_ok},
        }
    )
    if not quality_ok:
        return out
    out["allow_long"] = bool(long_direction_ok)
    out["allow_short"] = bool(short_direction_ok)
    return out


def _compute_hour_strict_gate(
    *,
    ctx: StrategyContext,
    last_ts: str,
    long_components: dict[str, bool],
    short_components: dict[str, bool],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": bool(ctx.config.hour_strict_mode_enabled),
        "active": False,
        "allow_long": True,
        "allow_short": True,
        "risk_scale": max(0.01, min(1.0, float(ctx.config.hour_strict_risk_scale))),
    }
    if not out["enabled"]:
        return out
    dt = _parse_iso_ts(last_ts).astimezone(timezone.utc)
    strict_hours = _parse_blocked_hours(ctx.config.hour_strict_hours_utc)
    out["hour"] = dt.hour
    out["active"] = dt.hour in strict_hours
    if not out["active"]:
        return out

    require_cross_or_cont = bool(ctx.config.hour_strict_require_cross_or_continuation)
    if require_cross_or_cont:
        out["allow_long"] = bool(long_components.get("cross")) or bool(long_components.get("continuation"))
        out["allow_short"] = bool(short_components.get("cross")) or bool(short_components.get("continuation"))
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (max(0.0, min(100.0, pct)) / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


def _bars_since_entry(candles: list[Candle], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    for index, candle in enumerate(candles):
        if candle.ts == entry_ts:
            return len(candles) - 1 - index
    return None


def _can_flip_position(
    *,
    ctx: StrategyContext,
    last_price: float,
    target_side: str,
    min_hold_ok: bool,
) -> bool:
    if ctx.position.side is None or ctx.position.side == target_side or not ctx.config.force_flip:
        return False
    if min_hold_ok:
        return True

    threshold_pips = abs(ctx.config.drawdown_stop_pips)
    if threshold_pips <= 0:
        return False

    pip = 0.01 if "JPY" in ctx.symbol.upper() else 0.0001
    if ctx.position.side == "LONG":
        adverse_pips = (ctx.position.avg_price - last_price) / pip
    else:
        adverse_pips = (last_price - ctx.position.avg_price) / pip
    return adverse_pips >= threshold_pips
