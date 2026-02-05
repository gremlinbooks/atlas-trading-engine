from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.engine.evaluator import Evaluator
from app.ledger.snapshots import insert_snapshot
from app.ledger.trades import list_positions, upsert_position
from app.logging.logger import get_logger, log_event

system_logger = get_logger("system", "logs/system.jsonl")
snapshot_logger = get_logger("snapshot", "logs/snapshot.jsonl")


class Scheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = OandaClient()
        self.error_state: Dict[str, Any] = {
            "halted": False,
            "evaluator_failures": 0,
            "snapshot_failures": 0,
            "last_evaluator_run": None,
            "last_snapshot_run": None,
        }
        self.evaluator = Evaluator(self.error_state)

    async def start(self) -> None:
        asyncio.create_task(self.evaluator.run_loop())
        asyncio.create_task(self.snapshot_loop())

    async def snapshot_loop(self) -> None:
        while True:
            await self.snapshot_once()
            await asyncio.sleep(self.settings.snapshot_seconds)

    async def snapshot_once(self) -> None:
        try:
            summary = self.client.get_account_summary().get("account", {})
            open_trades = self.client.list_open_trades().get("trades", [])

            insert_snapshot(
                balance=float(summary.get("balance", 0)),
                nav=float(summary.get("NAV", 0)),
                margin_used=float(summary.get("marginUsed", 0)),
                unrealized_pl=float(summary.get("unrealizedPL", 0)),
                open_trades=open_trades,
            )

            self._reconcile_positions(open_trades)

            self.error_state["snapshot_failures"] = 0
            self.error_state["last_snapshot_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:  # noqa: BLE001
            failures = self.error_state.get("snapshot_failures", 0) + 1
            self.error_state["snapshot_failures"] = failures
            log_event(system_logger, "snapshot_failure", error=str(exc), failures=failures)
            if failures >= 3:
                self.error_state["halted"] = True

    def _reconcile_positions(self, open_trades: list[dict[str, Any]]) -> None:
        broker_positions: Dict[str, dict[str, Any]] = {}
        for trade in open_trades:
            symbol = trade.get("instrument")
            if not symbol:
                continue
            units = float(trade.get("currentUnits", 0))
            side = "LONG" if units > 0 else "SHORT" if units < 0 else None
            broker_positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "units": abs(units),
                "avg_price": float(trade.get("price", 0)),
                "oanda_trade_id": trade.get("id"),
            }

        stored_positions = {p["symbol"]: p for p in list_positions()}
        for symbol in self.settings.symbols_list:
            broker = broker_positions.get(symbol)
            if broker:
                stored = stored_positions.get(symbol)
                if stored and stored["units"] != broker["units"]:
                    log_event(
                        snapshot_logger,
                        "position_mismatch",
                        symbol=symbol,
                        stored_units=stored["units"],
                        broker_units=broker["units"],
                    )
                upsert_position(
                    symbol=symbol,
                    side=broker["side"],
                    units=broker["units"],
                    avg_price=broker["avg_price"],
                    oanda_trade_id=broker["oanda_trade_id"],
                )
            else:
                stored = stored_positions.get(symbol)
                if stored and stored["units"] != 0:
                    log_event(
                        snapshot_logger,
                        "position_mismatch",
                        symbol=symbol,
                        stored_units=stored["units"],
                        broker_units=0,
                    )
                upsert_position(
                    symbol=symbol,
                    side=None,
                    units=0,
                    avg_price=0,
                    oanda_trade_id=None,
                )
