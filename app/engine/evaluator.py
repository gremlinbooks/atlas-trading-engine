from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.data.cursor import get_last_candle_ts, set_last_candle_ts
from app.engine.strategy import evaluate_candles
from app.ledger.snapshots import insert_decision
from app.ledger.trades import (
    get_position,
    get_trade_intent_by_idempotency,
    insert_trade_intent,
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

        last_processed = get_last_candle_ts(symbol)
        if last_processed and latest_ts <= last_processed:
            self._maybe_log_no_change(symbol, latest_ts)
            return

        signal, reason, metadata = evaluate_candles(candles)
        position = get_position(symbol)
        position_side = position["side"] if position and position["side"] else None
        position_units = position["units"] if position and position["units"] else 0
        position_trade_id = position["oanda_trade_id"] if position else None

        spread_pips = 0.0
        if pricing_available:
            price = price_map.get(symbol)
            if price:
                bids = price.get("bids", [])
                asks = price.get("asks", [])
                if bids and asks:
                    bid = float(bids[0]["price"])
                    ask = float(asks[0]["price"])
                    spread_pips = _calc_spread_pips(bid, ask, symbol)
                    metadata["bid"] = bid
                    metadata["ask"] = ask
        elif not self.settings.dry_run:
            spread_pips = self.settings.max_spread_pips + 1
            metadata["spread_unavailable"] = True

        state = get_state(
            symbol=symbol,
            spread_pips=spread_pips,
            position_side=position_side,
            error_halt=self.error_state.get("halted", False),
        )

        action = self._decide_action(signal, position_side)
        if self.error_state.get("halted", False) and action in {"ENTER", "FLIP"}:
            action = "HALTED"

        action = self._maybe_execute(
            symbol=symbol,
            candle_ts=latest_ts,
            signal=signal,
            action=action,
            position_trade_id=position_trade_id,
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
            state=state,
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
            state=state,
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

    def _decide_action(self, signal: str, position_side: Optional[str]) -> str:
        if signal == "HOLD":
            return "HOLD"
        if not position_side:
            return "WOULD_ENTER" if self.settings.dry_run else "ENTER"
        if position_side == signal:
            return "HOLD"
        return "WOULD_FLIP" if self.settings.dry_run else "FLIP"

    def _maybe_execute(
        self,
        *,
        symbol: str,
        candle_ts: str,
        signal: str,
        action: str,
        position_trade_id: Optional[str],
    ) -> str:
        if self.settings.dry_run:
            return action
        if action not in {"ENTER", "FLIP"}:
            return action

        idempotency_key = f"{symbol}:{candle_ts}"
        existing = get_trade_intent_by_idempotency(idempotency_key)
        if existing:
            return "ALREADY_EXECUTED"

        intent_id = f"{symbol}-{candle_ts}"
        insert_trade_intent(
            intent_id=intent_id,
            symbol=symbol,
            side=signal,
            units=float(self.settings.default_units),
            status="PENDING",
            idempotency_key=idempotency_key,
            reason="strategy signal",
            requested={"symbol": symbol, "signal": signal, "candle_ts": candle_ts},
        )

        client = self._get_client()
        try:
            if action == "FLIP":
                if not position_trade_id:
                    log_event(
                        execution_logger,
                        "flip_missing_trade_id",
                        symbol=symbol,
                        candle_ts=candle_ts,
                    )
                    return "EXECUTION_FAILED"
                client.close_trade(position_trade_id)
            units = self.settings.default_units if signal == "LONG" else -abs(self.settings.default_units)
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

    def _get_client(self) -> OandaClient:
        if self.client is None:
            self.client = OandaClient()
        return self.client

    def _get_candles(self, symbol: str) -> list[dict[str, Any]]:
        client = self._get_client()
        return client.get_candles(symbol, self.settings.timeframe, self.settings.candle_count)

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
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
