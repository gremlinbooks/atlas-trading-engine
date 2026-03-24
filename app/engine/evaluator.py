from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Optional

import requests

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.data.cursor import get_last_candle_ts, set_last_candle_ts
from app.engine.strategy_base import (
    Candle,
    PositionState,
    StrategyConfig,
    StrategyContext,
    StrategyDecision,
    StrategyState,
    get_strategy,
)
from app.ledger.snapshots import insert_decision
from app.ledger.trades import (
    get_position,
    get_trade_intent_by_idempotency,
    insert_trade_intent,
    upsert_position,
    update_trade_intent,
)
from app.logging.logger import get_logger, log_event
from app.state_machine import get_state

system_logger = get_logger("system", "logs/system.jsonl")
decision_logger = get_logger("decision", "logs/decision.jsonl")
execution_logger = get_logger("execution", "logs/execution.jsonl")


def _pip_factor(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def _calc_spread_pips(bid: float, ask: float, symbol: str) -> float:
    return (ask - bid) / _pip_factor(symbol)


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evaluator:
    def __init__(self, error_state: dict[str, Any]) -> None:
        self.settings = get_settings()
        self.client: Optional[OandaClient] = None
        self.error_state = error_state
        self._last_no_change_log: dict[str, str] = {}
        self._no_change_interval_seconds = _timeframe_to_seconds(self.settings.timeframe)
        self._last_exit_inspect_ts: dict[str, str] = {}
        self._exit_inspect_tf = self.settings.strategy_exit_inspect_tf.strip().upper()
        self._exit_inspect_enabled = bool(
            self._exit_inspect_tf and self._exit_inspect_tf != self.settings.timeframe.upper()
        )
        self._exit_inspect_candle_count = max(30, int(self.settings.strategy_exit_inspect_candle_count))
        self.strategy = get_strategy(self.settings.strategy_name)
        self.strategy_config = StrategyConfig(
            timeframe=self.settings.timeframe,
            min_hold_bars=self.settings.strategy_min_hold_bars,
            trend_ema_period=self.settings.strategy_trend_ema_period,
            enabled=self.settings.strategy_enabled,
            fast_len=self.settings.strategy_fast_len,
            slow_len=self.settings.strategy_slow_len,
            use_bias=self.settings.strategy_use_bias,
            invert_eurcad=self.settings.strategy_invert_eurcad,
            force_flip=self.settings.strategy_force_flip,
            tp1_pips=self.settings.strategy_tp1_pips,
            sl_pips=self.settings.strategy_sl_pips,
            max_hold_bars=self.settings.strategy_max_hold_bars,
            drawdown_stop_pips=self.settings.strategy_drawdown_stop_pips,
            drawdown_stop_bars=self.settings.strategy_drawdown_stop_bars,
            tp1_close_pct=self.settings.strategy_tp1_close_pct,
            trail_drawdown_pct=self.settings.strategy_trail_drawdown_pct,
            be_lock_pips=self.settings.strategy_be_lock_pips,
            profit_floor1_trigger_pips=self.settings.strategy_profit_floor1_trigger_pips,
            profit_floor1_lock_pips=self.settings.strategy_profit_floor1_lock_pips,
            profit_floor2_trigger_pips=self.settings.strategy_profit_floor2_trigger_pips,
            profit_floor2_lock_pips=self.settings.strategy_profit_floor2_lock_pips,
            stoch_entry_mode=self.settings.strategy_stoch_entry_mode,
            use_stoch_exit=self.settings.strategy_use_stoch_exit,
            st_rsi_len=self.settings.strategy_st_rsi_len,
            st_stoch_len=self.settings.strategy_st_stoch_len,
            st_k_len=self.settings.strategy_st_k_len,
            st_d_len=self.settings.strategy_st_d_len,
            st_ob=self.settings.strategy_st_ob,
            st_os=self.settings.strategy_st_os,
            st_recent=self.settings.strategy_st_recent,
            st_tight_pips=self.settings.strategy_st_tight_pips,
            block_trades=self.settings.strategy_block_trades,
            block_session=self.settings.strategy_block_session,
            block_entry_hours_utc=self.settings.strategy_block_entry_hours_utc,
            no_intent_override_enabled=self.settings.strategy_no_intent_override_enabled,
            no_intent_override_hours_utc=self.settings.strategy_no_intent_override_hours_utc,
            no_intent_override_atr_mult=self.settings.strategy_no_intent_override_atr_mult,
            no_intent_override_body_ratio_min=self.settings.strategy_no_intent_override_body_ratio_min,
            no_intent_override_close_extreme_frac=self.settings.strategy_no_intent_override_close_extreme_frac,
            no_intent_override_volume_lookback=self.settings.strategy_no_intent_override_volume_lookback,
            no_intent_override_volume_percentile=self.settings.strategy_no_intent_override_volume_percentile,
            no_intent_override_risk_scale=self.settings.strategy_no_intent_override_risk_scale,
            hour_strict_mode_enabled=self.settings.strategy_hour_strict_mode_enabled,
            hour_strict_hours_utc=self.settings.strategy_hour_strict_hours_utc,
            hour_strict_require_cross_or_continuation=self.settings.strategy_hour_strict_require_cross_or_continuation,
            hour_strict_risk_scale=self.settings.strategy_hour_strict_risk_scale,
            quick_relax=self.settings.strategy_quick_relax,
            use_day_mask=self.settings.strategy_use_day_mask,
            block_mon=self.settings.strategy_block_mon,
            block_tue=self.settings.strategy_block_tue,
            block_wed=self.settings.strategy_block_wed,
            block_thu=self.settings.strategy_block_thu,
            block_fri=self.settings.strategy_block_fri,
            block_sat=self.settings.strategy_block_sat,
            block_sun=self.settings.strategy_block_sun,
            use_spread_gate=self.settings.strategy_use_spread_gate,
            max_spread_pips=self.settings.strategy_max_spread_pips,
            aggr_spread_factor=self.settings.strategy_aggr_spread_factor,
            hold_signal_bars=self.settings.strategy_hold_signal_bars,
            apply_on_history=self.settings.strategy_apply_on_history,
            pb_enabled=self.settings.strategy_pb_enabled,
            pb_enabled_long=self.settings.strategy_pb_enabled_long,
            pb_enabled_short=self.settings.strategy_pb_enabled_short,
            pb_lookback_bars=self.settings.strategy_pb_lookback_bars,
            cont_enabled=self.settings.strategy_cont_enabled,
            base_max_bars=self.settings.strategy_base_max_bars,
            base_max_range_atr=self.settings.strategy_base_max_range_atr,
            rejoin_enabled=self.settings.strategy_rejoin_enabled,
            rejoin_enabled_long=self.settings.strategy_rejoin_enabled_long,
            rejoin_enabled_short=self.settings.strategy_rejoin_enabled_short,
            allow_second_chance=self.settings.strategy_allow_second_chance,
            reenter_within_bars=self.settings.strategy_reenter_within_bars,
            early_loss_cut_pips=self.settings.strategy_early_loss_cut_pips,
            momentum_fail_exit_pips=self.settings.strategy_momentum_fail_exit_pips,
        )
        self.strategy_state: dict[str, StrategyState] = {}

    async def run_loop(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.settings.candle_poll_seconds)

    async def run_once(self) -> None:
        pricing_available = False
        price_map: dict[str, Any] = {}

        if not self.settings.dry_run:
            try:
                client = self._get_client()
                pricing = client.get_pricing(self.settings.symbols_list)
                prices = pricing.get("prices", [])
                price_map = {p["instrument"]: p for p in prices}
                pricing_available = True
                self.error_state["evaluator_failures"] = 0
            except Exception as exc:  # noqa: BLE001
                failures = self.error_state.get("evaluator_failures", 0) + 1
                self.error_state["evaluator_failures"] = failures
                log_event(system_logger, "evaluator_pricing_failure", error=str(exc), failures=failures)

        for symbol in self.settings.symbols_list:
            try:
                await self._process_symbol(symbol, price_map, pricing_available)
            except Exception as exc:  # noqa: BLE001
                log_event(system_logger, "evaluator_symbol_failure", symbol=symbol, error=str(exc))

        self.error_state["last_evaluator_run"] = _now_ts()

    async def _process_symbol(
        self,
        symbol: str,
        price_map: dict[str, Any],
        pricing_available: bool,
    ) -> None:
        try:
            candles = self._get_candles(symbol)
        except Exception as exc:  # noqa: BLE001
            log_event(system_logger, "candle_fetch_failed", symbol=symbol, error=str(exc))
            return
        if len(candles) < 2:
            log_event(system_logger, "candle_insufficient", symbol=symbol, count=len(candles))
            return

        latest_candle = candles[-1]
        latest_ts = latest_candle.get("time")
        if not latest_ts:
            log_event(system_logger, "candle_missing_time", symbol=symbol)
            return

        position = get_position(symbol)
        position_side = position["side"] if position and position["side"] else None
        position_units = position["units"] if position and position["units"] else 0
        position_trade_id = position["oanda_trade_id"] if position else None
        state = self.strategy_state.get(symbol, StrategyState())
        position_entry_ts = state.last_trade_candle_ts or (position["updated_at"] if position else None)
        position_avg_price = position["avg_price"] if position else 0.0

        spread_pips: float | None = None
        spread_available = False
        bid: float | None = None
        ask: float | None = None
        if pricing_available:
            price = price_map.get(symbol)
            if price:
                bids = price.get("bids", [])
                asks = price.get("asks", [])
                if bids and asks:
                    bid = float(bids[0]["price"])
                    ask = float(asks[0]["price"])
                    spread_pips = _calc_spread_pips(bid, ask, symbol)
                    spread_available = True

        if (
            position_side in {"LONG", "SHORT"}
            and self.settings.strategy_intrabar_loss_exit_enabled
            and spread_available
            and bid is not None
            and ask is not None
        ):
            guard_exit = self._process_intrabar_loss_guard(
                symbol=symbol,
                position_side=position_side,
                position_units=position_units,
                position_avg_price=position_avg_price,
                position_trade_id=position_trade_id,
                spread_pips=spread_pips,
                bid=bid,
                ask=ask,
            )
            if guard_exit:
                return

        last_processed = get_last_candle_ts(symbol)
        if last_processed and latest_ts <= last_processed:
            if self._exit_inspect_enabled and position_side in {"LONG", "SHORT"}:
                exit_decision = self._process_exit_inspection(
                    symbol=symbol,
                    position_side=position_side,
                    position_units=position_units,
                    position_avg_price=position_avg_price,
                    position_entry_ts=position_entry_ts,
                    position_trade_id=position_trade_id,
                    spread_pips=spread_pips,
                    spread_available=spread_available,
                    bid=bid,
                    ask=ask,
                )
                if exit_decision:
                    return
            self._maybe_log_no_change(symbol, latest_ts)
            return

        candle_objs = [
            Candle(
                ts=c["time"],
                o=float(c["o"]),
                h=float(c["h"]),
                l=float(c["l"]),
                c=float(c["c"]),
                volume=int(c["volume"]),
            )
            for c in candles
        ]
        if self._exit_inspect_enabled and position_side in {"LONG", "SHORT"}:
            exit_decision = self._process_exit_inspection(
                symbol=symbol,
                position_side=position_side,
                position_units=position_units,
                position_avg_price=position_avg_price,
                position_entry_ts=position_entry_ts,
                position_trade_id=position_trade_id,
                spread_pips=spread_pips,
                spread_available=spread_available,
                bid=bid,
                ask=ask,
            )
            if exit_decision:
                return
            state = self.strategy_state.get(symbol, state)

        decision = self.strategy.evaluate(
            candle_objs,
            StrategyContext(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                position=PositionState(
                    side=position_side,
                    units=position_units,
                    avg_price=position_avg_price,
                    entry_ts=position_entry_ts,
                ),
                config=self.strategy_config,
                state=state,
                bar_index=len(candle_objs) - 1,
                spread_pips=spread_pips,
                spread_available=spread_available,
                is_realtime=True,
            ),
        )
        signal, reason, metadata = self._map_strategy_signal(decision)
        if spread_available:
            metadata["bid"] = bid
            metadata["ask"] = ask
        if spread_pips is None and not self.settings.dry_run:
            metadata["spread_unavailable"] = True

        computed_state = get_state(
            symbol=symbol,
            spread_pips=spread_pips if spread_pips is not None else 0.0,
            position_side=position_side,
            error_halt=self.error_state.get("halted", False),
        )

        action = self._map_execution_action(decision.action)
        if self.error_state.get("halted", False) and action in {"ENTER", "FLIP"}:
            action = "HALTED"

        action = self._maybe_execute(
            symbol=symbol,
            candle_ts=latest_ts,
            timeframe=self.settings.timeframe,
            signal=signal,
            action=action,
            position_trade_id=position_trade_id,
            decision=decision,
        )

        candle_metadata = {
            "time": latest_candle.get("time"),
            "o": latest_candle.get("o"),
            "h": latest_candle.get("h"),
            "l": latest_candle.get("l"),
            "c": latest_candle.get("c"),
            "volume": latest_candle.get("volume"),
        }
        metadata.update({"latest_candle": candle_metadata})

        insert_decision(
            symbol=symbol,
            state=computed_state,
            spread_pips=spread_pips,
            candle_ts=latest_ts,
            signal=signal,
            reason=reason,
            metadata={
                **metadata,
                "timeframe": self.settings.timeframe,
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )

        log_event(
            decision_logger,
            "decision",
            symbol=symbol,
            candle_ts=latest_ts,
            timeframe=self.settings.timeframe,
            signal=signal,
            reason=reason,
            state=computed_state,
            action=action,
            current_position_side=position_side,
            current_units=position_units,
            metadata={
                **metadata,
                "timeframe": self.settings.timeframe,
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )

        set_last_candle_ts(symbol, latest_ts)
        if decision.next_state is not None:
            self.strategy_state[symbol] = decision.next_state

    def _process_intrabar_loss_guard(
        self,
        *,
        symbol: str,
        position_side: str,
        position_units: float,
        position_avg_price: float,
        position_trade_id: str | None,
        spread_pips: float | None,
        bid: float,
        ask: float,
    ) -> bool:
        threshold = abs(float(self.settings.strategy_intrabar_loss_exit_pips))
        if threshold <= 0:
            return False
        pip = _pip_factor(symbol)
        if position_side == "LONG":
            mark_price = bid
            pnl_pips = (mark_price - position_avg_price) / pip
        else:
            mark_price = ask
            pnl_pips = (position_avg_price - mark_price) / pip
        if pnl_pips > -threshold:
            return False

        decision = StrategyDecision(
            action="EXIT",
            reason="intrabar loss guard",
            metadata={
                "intrabar_loss_guard": True,
                "threshold_pips": threshold,
                "pnl_pips": pnl_pips,
                "mark_price": mark_price,
                "position_avg_price": position_avg_price,
            },
            price=mark_price,
        )
        signal, reason, metadata = self._map_strategy_signal(decision)
        metadata["execution_timeframe"] = "INTRABAR_GUARD"
        metadata["bid"] = bid
        metadata["ask"] = ask
        action = self._map_execution_action(decision.action)
        action = self._maybe_execute(
            symbol=symbol,
            candle_ts=_now_ts(),
            timeframe="INTRABAR_GUARD",
            signal=signal,
            action=action,
            position_trade_id=position_trade_id,
            decision=decision,
        )

        computed_state = get_state(
            symbol=symbol,
            spread_pips=spread_pips if spread_pips is not None else 0.0,
            position_side=position_side,
            error_halt=self.error_state.get("halted", False),
        )
        ts = _now_ts()
        insert_decision(
            symbol=symbol,
            state=computed_state,
            spread_pips=spread_pips,
            candle_ts=ts,
            signal=signal,
            reason=reason,
            metadata={
                **metadata,
                "timeframe": "INTRABAR_GUARD",
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )
        log_event(
            decision_logger,
            "decision",
            symbol=symbol,
            candle_ts=ts,
            timeframe="INTRABAR_GUARD",
            signal=signal,
            reason=reason,
            state=computed_state,
            action=action,
            current_position_side=position_side,
            current_units=position_units,
            metadata={
                **metadata,
                "timeframe": "INTRABAR_GUARD",
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )
        return True

    def _process_exit_inspection(
        self,
        *,
        symbol: str,
        position_side: str,
        position_units: float,
        position_avg_price: float,
        position_entry_ts: str | None,
        position_trade_id: str | None,
        spread_pips: float | None,
        spread_available: bool,
        bid: float | None,
        ask: float | None,
    ) -> bool:
        if not self._exit_inspect_enabled:
            return False

        try:
            candles = self._get_candles_for_tf(
                symbol=symbol,
                timeframe=self._exit_inspect_tf,
                count=self._exit_inspect_candle_count,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                system_logger,
                "exit_inspect_candle_fetch_failed",
                symbol=symbol,
                timeframe=self._exit_inspect_tf,
                error=str(exc),
            )
            return False

        if len(candles) < 2:
            return False
        latest_candle = candles[-1]
        latest_ts = latest_candle.get("time")
        if not latest_ts:
            return False
        if self._last_exit_inspect_ts.get(symbol) == latest_ts:
            return False

        candle_objs = [
            Candle(
                ts=c["time"],
                o=float(c["o"]),
                h=float(c["h"]),
                l=float(c["l"]),
                c=float(c["c"]),
                volume=int(c["volume"]),
            )
            for c in candles
        ]
        state = self.strategy_state.get(symbol, StrategyState())
        decision = self.strategy.evaluate(
            candle_objs,
            StrategyContext(
                symbol=symbol,
                timeframe=self._exit_inspect_tf,
                position=PositionState(
                    side=position_side,
                    units=position_units,
                    avg_price=position_avg_price,
                    entry_ts=position_entry_ts,
                ),
                config=self.strategy_config,
                state=state,
                bar_index=len(candle_objs) - 1,
                spread_pips=spread_pips,
                spread_available=spread_available,
                is_realtime=True,
                exit_only=True,
            ),
        )
        self._last_exit_inspect_ts[symbol] = latest_ts
        if decision.next_state is not None:
            self.strategy_state[symbol] = decision.next_state

        if decision.action not in {"EXIT", "PARTIAL_TP1"}:
            return False

        signal, reason, metadata = self._map_strategy_signal(decision)
        metadata["exit_inspection"] = True
        metadata["signal_timeframe"] = self.settings.timeframe
        metadata["execution_timeframe"] = self._exit_inspect_tf
        if spread_available:
            metadata["bid"] = bid
            metadata["ask"] = ask
        if spread_pips is None and not self.settings.dry_run:
            metadata["spread_unavailable"] = True
        action = self._map_execution_action(decision.action)
        action = self._maybe_execute(
            symbol=symbol,
            candle_ts=latest_ts,
            timeframe=self._exit_inspect_tf,
            signal=signal,
            action=action,
            position_trade_id=position_trade_id,
            decision=decision,
        )
        computed_state = get_state(
            symbol=symbol,
            spread_pips=spread_pips if spread_pips is not None else 0.0,
            position_side=position_side,
            error_halt=self.error_state.get("halted", False),
        )
        insert_decision(
            symbol=symbol,
            state=computed_state,
            spread_pips=spread_pips,
            candle_ts=latest_ts,
            signal=signal,
            reason=reason,
            metadata={
                **metadata,
                "timeframe": self._exit_inspect_tf,
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )
        log_event(
            decision_logger,
            "decision",
            symbol=symbol,
            candle_ts=latest_ts,
            timeframe=self._exit_inspect_tf,
            signal=signal,
            reason=reason,
            state=computed_state,
            action=action,
            current_position_side=position_side,
            current_units=position_units,
            metadata={
                **metadata,
                "timeframe": self._exit_inspect_tf,
                "action": action,
                "current_position_side": position_side,
                "current_units": position_units,
            },
        )
        return True

    def _map_strategy_signal(self, decision: StrategyDecision) -> tuple[str, str, dict[str, Any]]:
        action = decision.action
        reason = decision.reason
        metadata = dict(decision.metadata)

        if action in {"ENTER_LONG", "FLIP_LONG"}:
            return "LONG", reason, metadata
        if action in {"ENTER_SHORT", "FLIP_SHORT"}:
            return "SHORT", reason, metadata
        if action == "EXIT":
            return "EXIT", reason, metadata
        if action == "PARTIAL_TP1":
            return "TP1", reason, metadata
        return "HOLD", reason, metadata

    def _map_execution_action(self, action: str) -> str:
        if action == "HOLD":
            return "HOLD"
        if action == "PARTIAL_TP1":
            return "WOULD_PARTIAL_TP1" if self.settings.dry_run else "PARTIAL_TP1"
        if action == "EXIT":
            return "WOULD_EXIT" if self.settings.dry_run else "EXIT"
        if action in {"ENTER_LONG", "ENTER_SHORT"}:
            return "WOULD_ENTER" if self.settings.dry_run else "ENTER"
        if action in {"FLIP_LONG", "FLIP_SHORT"}:
            return "WOULD_FLIP" if self.settings.dry_run else "FLIP"
        return "HOLD"

    def _maybe_execute(
        self,
        *,
        symbol: str,
        candle_ts: str,
        timeframe: str,
        signal: str,
        action: str,
        position_trade_id: Optional[str],
        decision: StrategyDecision,
    ) -> str:
        if self.settings.dry_run:
            return action
        if action == "PARTIAL_TP1":
            log_event(
                execution_logger,
                "partial_tp1_unsupported",
                symbol=symbol,
                candle_ts=candle_ts,
                price=decision.price,
            )
            return "PARTIAL_UNSUPPORTED"
        if action not in {"ENTER", "FLIP", "EXIT"}:
            return action

        idempotency_key = f"{symbol}:{timeframe}:{candle_ts}"
        existing = get_trade_intent_by_idempotency(idempotency_key)
        if existing:
            return "ALREADY_EXECUTED"

        intent_id = f"{symbol}-{timeframe}-{candle_ts}"
        order_units_abs = self._resolve_order_units(symbol=symbol, signal=signal) if signal in {"LONG", "SHORT"} else 0
        if signal in {"LONG", "SHORT"}:
            order_units_abs = self._apply_decision_sizing(decision=decision, fallback_units=order_units_abs)
        insert_trade_intent(
            intent_id=intent_id,
            symbol=symbol,
            side=signal if signal in {"LONG", "SHORT"} else "EXIT",
            units=float(order_units_abs),
            status="PENDING",
            idempotency_key=idempotency_key,
            reason="strategy signal",
            requested={"symbol": symbol, "signal": signal, "candle_ts": candle_ts},
        )

        client = self._get_client()
        try:
            if action == "FLIP":
                resolved_trade_id = self._resolve_position_trade_id(
                    client=client,
                    symbol=symbol,
                    candle_ts=candle_ts,
                    requested_trade_id=position_trade_id,
                    action=action,
                )
                if resolved_trade_id:
                    self._close_trade_with_retry(
                        client=client,
                        symbol=symbol,
                        candle_ts=candle_ts,
                        trade_id=resolved_trade_id,
                        action=action,
                    )
            if action == "EXIT":
                resolved_trade_id = self._resolve_position_trade_id(
                    client=client,
                    symbol=symbol,
                    candle_ts=candle_ts,
                    requested_trade_id=position_trade_id,
                    action=action,
                )
                if not resolved_trade_id:
                    return "ALREADY_CLOSED"
                response = self._close_trade_with_retry(
                    client=client,
                    symbol=symbol,
                    candle_ts=candle_ts,
                    trade_id=resolved_trade_id,
                    action=action,
                )
            else:
                units = order_units_abs if signal == "LONG" else -abs(order_units_abs)
                response = client.place_market_order(symbol, units)
        except Exception as exc:  # noqa: BLE001
            update_trade_intent(
                intent_id=intent_id,
                status="FAILED",
                response={"error": str(exc)},
            )
            log_event(execution_logger, "execution_failed", symbol=symbol, error=str(exc))
            return "EXECUTION_FAILED"

        order_id = response.get("orderCreateTransaction", {}).get("id")
        trade_id = response.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID")
        update_trade_intent(
            intent_id=intent_id,
            status="SUBMITTED",
            response=response,
            oanda_order_id=order_id,
            oanda_trade_id=trade_id,
        )
        log_event(
            execution_logger,
            "execution_submitted",
            symbol=symbol,
            action=action,
            order_id=order_id,
            trade_id=trade_id,
        )
        return action

    def _resolve_position_trade_id(
        self,
        *,
        client: OandaClient,
        symbol: str,
        candle_ts: str,
        requested_trade_id: str | None,
        action: str,
    ) -> str | None:
        if requested_trade_id:
            return requested_trade_id

        refreshed_trade_id = self._refresh_position_from_broker(client=client, symbol=symbol)
        if refreshed_trade_id:
            log_event(
                execution_logger,
                "position_trade_id_refreshed",
                symbol=symbol,
                candle_ts=candle_ts,
                action=action,
                refreshed_trade_id=refreshed_trade_id,
            )
            return refreshed_trade_id

        log_event(
            execution_logger,
            "position_trade_id_missing",
            symbol=symbol,
            candle_ts=candle_ts,
            action=action,
        )
        return None

    def _close_trade_with_retry(
        self,
        *,
        client: OandaClient,
        symbol: str,
        candle_ts: str,
        trade_id: str,
        action: str,
    ) -> dict[str, Any]:
        try:
            return client.close_trade(trade_id)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 404:
                raise

            refreshed_trade_id = self._refresh_position_from_broker(client=client, symbol=symbol)
            if refreshed_trade_id and refreshed_trade_id != trade_id:
                log_event(
                    execution_logger,
                    "retry_close_with_refreshed_trade_id",
                    symbol=symbol,
                    candle_ts=candle_ts,
                    action=action,
                    stale_trade_id=trade_id,
                    refreshed_trade_id=refreshed_trade_id,
                )
                return client.close_trade(refreshed_trade_id)

            log_event(
                execution_logger,
                "close_trade_already_closed",
                symbol=symbol,
                candle_ts=candle_ts,
                action=action,
                stale_trade_id=trade_id,
            )
            return {}

    def _refresh_position_from_broker(self, *, client: OandaClient, symbol: str) -> str | None:
        open_trades = client.list_open_trades().get("trades", [])
        matching_trade: dict[str, Any] | None = None
        for trade in open_trades:
            if trade.get("instrument") != symbol:
                continue
            matching_trade = trade
            break

        if not matching_trade:
            upsert_position(
                symbol=symbol,
                side=None,
                units=0,
                avg_price=0,
                oanda_trade_id=None,
            )
            return None

        units = float(matching_trade.get("currentUnits", 0))
        upsert_position(
            symbol=symbol,
            side="LONG" if units > 0 else "SHORT" if units < 0 else None,
            units=abs(units),
            avg_price=float(matching_trade.get("price", 0)),
            oanda_trade_id=matching_trade.get("id"),
        )
        return matching_trade.get("id")

    def _resolve_order_units(self, *, symbol: str, signal: str) -> int:
        default_units = abs(int(self.settings.default_units))
        margin_pct = float(self.settings.margin_usage_pct)
        if margin_pct <= 0:
            return default_units

    def _apply_decision_sizing(self, *, decision: StrategyDecision, fallback_units: int) -> int:
        if decision.units is not None:
            explicit_units = int(abs(decision.units))
            if explicit_units > 0:
                return explicit_units
        metadata = decision.metadata if isinstance(decision.metadata, dict) else {}
        multiplier_raw = metadata.get("entry_units_multiplier")
        if multiplier_raw is None:
            return fallback_units
        try:
            multiplier = float(multiplier_raw)
        except (TypeError, ValueError):
            return fallback_units
        if multiplier <= 0:
            return fallback_units
        return max(1, int(round(fallback_units * multiplier)))

        client = self._get_client()
        try:
            summary = client.get_account_summary().get("account", {})
            margin_available = float(summary.get("marginAvailable", 0.0))
            if margin_available <= 0:
                raise ValueError("marginAvailable is not positive")

            pricing = client.get_pricing([symbol])
            prices = pricing.get("prices", [])
            if not prices:
                raise ValueError(f"no pricing for {symbol}")
            price_payload = prices[0]
            bids = price_payload.get("bids", [])
            asks = price_payload.get("asks", [])
            if not bids or not asks:
                raise ValueError(f"missing bid/ask for {symbol}")
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            side_price = ask if signal == "LONG" else bid
            if side_price <= 0:
                raise ValueError("invalid side price")

            account_instruments = client.get_account_instruments([symbol])
            instruments = account_instruments.get("instruments", [])
            if not instruments:
                raise ValueError(f"no instrument metadata for {symbol}")
            margin_rate = float(instruments[0].get("marginRate", 0.0))
            if margin_rate <= 0:
                raise ValueError("invalid marginRate")

            target_margin = margin_available * (margin_pct / 100.0)
            computed_units = int(target_margin / (side_price * margin_rate))
            if computed_units <= 0:
                raise ValueError("computed units <= 0")

            return computed_units
        except Exception as exc:  # noqa: BLE001
            log_event(
                execution_logger,
                "margin_sizing_fallback",
                symbol=symbol,
                margin_usage_pct=margin_pct,
                default_units=default_units,
                error=str(exc),
            )
            return default_units

    def _get_client(self) -> OandaClient:
        if self.client is None:
            self.client = OandaClient()
        return self.client

    def _get_candles(self, symbol: str) -> list[dict[str, Any]]:
        return self._get_candles_for_tf(
            symbol=symbol,
            timeframe=self.settings.timeframe,
            count=self.settings.candle_count,
        )

    def _get_candles_for_tf(self, *, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        client = self._get_client()
        return client.get_candles(symbol, timeframe, count)

    def _maybe_log_no_change(self, symbol: str, last_seen_candle_ts: str) -> None:
        last_log_ts = self._last_no_change_log.get(symbol)
        if last_log_ts:
            elapsed = _iso_to_epoch_seconds(_now_ts()) - _iso_to_epoch_seconds(last_log_ts)
            if elapsed < self._no_change_interval_seconds:
                return
        self._last_no_change_log[symbol] = _now_ts()
        log_event(
            system_logger,
            "candle_no_change",
            event="candle_no_change",
            symbol=symbol,
            timeframe=self.settings.timeframe,
            last_seen_candle_ts=last_seen_candle_ts,
        )

    def update_strategy_config(self, **updates: Any) -> None:
        for key, value in updates.items():
            if value is None:
                continue
            if hasattr(self.strategy_config, key):
                setattr(self.strategy_config, key, value)
        if "strategy_name" in updates and updates["strategy_name"] is not None:
            self.strategy = get_strategy(updates["strategy_name"])


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
        "W": 604800,
        "M": 2592000,
    }
    return mapping.get(timeframe.upper(), 900)


def _iso_to_epoch_seconds(ts: str) -> float:
    token = ts.replace("Z", "+00:00")
    token = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r".\1", token)
    return datetime.fromisoformat(token).timestamp()
