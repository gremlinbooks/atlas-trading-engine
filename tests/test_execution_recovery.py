from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from app.engine.evaluator import Evaluator


class _FakeClient:
    def __init__(self) -> None:
        self.closed: list[str] = []

    def close_trade(self, trade_id: str) -> dict:
        self.closed.append(trade_id)
        if trade_id == "stale":
            response = SimpleNamespace(status_code=404)
            raise requests.HTTPError("404 trade not found", response=response)
        return {"closedTradeID": trade_id}


class ExecutionRecoveryTests(unittest.TestCase):
    def test_close_trade_retries_after_stale_trade_id_404(self) -> None:
        evaluator = Evaluator.__new__(Evaluator)
        client = _FakeClient()

        with patch.object(evaluator, "_refresh_position_from_broker", return_value="fresh"):
            response = evaluator._close_trade_with_retry(
                client=client,
                symbol="AUD_USD",
                candle_ts="2026-03-22T09:00:00Z",
                trade_id="stale",
                action="EXIT",
            )

        self.assertEqual(response, {"closedTradeID": "fresh"})
        self.assertEqual(client.closed, ["stale", "fresh"])

    def test_resolve_position_trade_id_refreshes_broker_state_when_missing(self) -> None:
        evaluator = Evaluator.__new__(Evaluator)
        client = object()

        with patch.object(evaluator, "_refresh_position_from_broker", return_value="broker-trade-1"):
            trade_id = evaluator._resolve_position_trade_id(
                client=client,
                symbol="AUD_USD",
                candle_ts="2026-03-22T09:00:00Z",
                requested_trade_id=None,
                action="FLIP",
            )

        self.assertEqual(trade_id, "broker-trade-1")


if __name__ == "__main__":
    unittest.main()
