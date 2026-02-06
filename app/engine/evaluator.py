from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.ledger.snapshots import insert_decision
from app.ledger.trades import get_position
from app.logging.logger import get_logger, log_event
from app.state_machine import get_state

system_logger = get_logger("system", "logs/system.jsonl")
decision_logger = get_logger("decision", "logs/decision.jsonl")


def _pip_factor(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def _calc_spread_pips(bid: float, ask: float, symbol: str) -> float:
    return (ask - bid) / _pip_factor(symbol)


class Evaluator:
    def __init__(self, error_state: dict[str, Any]) -> None:
        self.settings = get_settings()
        self.client = None if self.settings.dry_run else OandaClient()
        self.error_state = error_state

    async def run_loop(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.settings.loop_seconds)

    async def run_once(self) -> None:
        pricing_error = None
        price_map: dict[str, Any] = {}
        if not self.settings.dry_run:
            try:
                if self.client is None:
                    self.client = OandaClient()
                pricing = self.client.get_pricing(self.settings.symbols_list)
                prices = pricing.get("prices", [])
                price_map = {p["instrument"]: p for p in prices}
            except Exception as exc:  # noqa: BLE001
                pricing_error = str(exc)
                failures = self.error_state.get("evaluator_failures", 0) + 1
                self.error_state["evaluator_failures"] = failures
                log_event(system_logger, "evaluator_failure", error=pricing_error, failures=failures)
        else:
            self.error_state["evaluator_failures"] = 0

        for symbol in self.settings.symbols_list:
            bid = None
            ask = None
            spread_pips = 0.0

            price = price_map.get(symbol)
            if price:
                bids = price.get("bids", [])
                asks = price.get("asks", [])
                if bids and asks:
                    bid = float(bids[0]["price"])
                    ask = float(asks[0]["price"])
                    spread_pips = _calc_spread_pips(bid, ask, symbol)

            position = get_position(symbol)
            position_side = position["side"] if position and position["side"] else None
            state = get_state(
                symbol=symbol,
                spread_pips=spread_pips,
                position_side=position_side,
                error_halt=self.error_state.get("halted", False),
            )

            metadata: dict[str, Any] = {"bid": bid, "ask": ask}
            if pricing_error:
                metadata["pricing_error"] = pricing_error
            if self.settings.dry_run:
                metadata["pricing"] = "dry_run"

            insert_decision(
                symbol=symbol,
                state=state,
                spread_pips=spread_pips,
                candle_ts=datetime.now(timezone.utc).isoformat(),
                signal="HOLD",
                reason="strategy not enabled (phase 1)",
                metadata=metadata,
            )

            log_event(
                decision_logger,
                "decision",
                symbol=symbol,
                state=state,
                spread_pips=spread_pips,
                signal="HOLD",
                reason="strategy not enabled (phase 1)",
            )

        self.error_state["last_evaluator_run"] = datetime.now(timezone.utc).isoformat()
