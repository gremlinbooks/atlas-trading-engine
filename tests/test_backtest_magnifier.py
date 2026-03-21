import unittest

from app.backtest import run as backtest_run
from app.engine.strategy_base import StrategyConfig, StrategyContext, StrategyDecision


class _DummyStrategy:
    def evaluate(self, candles, ctx: StrategyContext) -> StrategyDecision:
        if ctx.bar_index == 1:
            return StrategyDecision(action="ENTER_LONG", reason="test", metadata={})
        if ctx.bar_index == 3:
            return StrategyDecision(action="ENTER_SHORT", reason="test", metadata={})
        return StrategyDecision(action="HOLD", reason="test", metadata={})


class _ExitInspectStrategy:
    def evaluate(self, candles, ctx: StrategyContext) -> StrategyDecision:
        if not ctx.exit_only and ctx.position.side is None and ctx.bar_index == 1:
            return StrategyDecision(action="ENTER_LONG", reason="test-entry", metadata={})
        if ctx.exit_only and ctx.position.side == "LONG" and len(candles) >= 4:
            return StrategyDecision(
                action="EXIT",
                reason="momentum fail stop",
                metadata={},
                price=candles[-1].c,
            )
        return StrategyDecision(action="HOLD", reason="hold", metadata={})


class MagnifierPolicyTests(unittest.TestCase):
    def test_tp1_sl_ordering_optimistic(self) -> None:
        tp1_hit, sl_hit = True, True
        tp1_first, sl_first = backtest_run._resolve_tp1_sl_order(tp1_hit, sl_hit, "optimistic")
        self.assertTrue(tp1_first)
        self.assertFalse(sl_first)

    def test_tp1_sl_ordering_conservative(self) -> None:
        tp1_hit, sl_hit = True, True
        tp1_first, sl_first = backtest_run._resolve_tp1_sl_order(tp1_hit, sl_hit, "conservative")
        self.assertFalse(tp1_first)
        self.assertTrue(sl_first)

    def test_tp1_then_runner_same_candle_policy(self) -> None:
        # If TP1 and runner can both be hit in same candle, optimistic allows it.
        allow_runner = backtest_run._allow_runner_same_candle(True, "optimistic")
        allow_runner_cons = backtest_run._allow_runner_same_candle(True, "conservative")
        self.assertTrue(allow_runner)
        self.assertTrue(allow_runner_cons)


class EntryTimingParityTests(unittest.TestCase):
    def test_close_only_entries_match_between_profiles(self) -> None:
        candles = []
        start = backtest_run.datetime(2026, 2, 1, 0, 0, tzinfo=backtest_run.timezone.utc)
        for i in range(6):
            ts = (start + backtest_run.timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z")
            candles.append({"time": ts, "o": 1.0, "h": 1.0002, "l": 0.9998, "c": 1.0, "volume": 100})

        magnifier = []
        for i in range(6):
            base = start + backtest_run.timedelta(minutes=15 * i)
            for j in range(15):
                ts = (base + backtest_run.timedelta(minutes=j)).isoformat().replace("+00:00", "Z")
                magnifier.append(
                    {"time": ts, "o": 1.0, "h": 1.0001, "l": 0.9999, "c": 1.0, "volume": 10}
                )

        cfg = StrategyConfig(timeframe="M15", min_hold_bars=0, trend_ema_period=50, enabled=True)

        trades_live, _, _ = backtest_run._run_backtest(
            candles=candles,
            symbol="AUD_USD",
            timeframe="M15",
            units=1000,
            spread_pips=0.0,
            fill="close",
            bar_fill_policy="conservative",
            use_runner=True,
            strategy=_DummyStrategy(),
            strategy_config=cfg,
            magnify_tf="M1",
            magnify_policy="conservative",
            magnifier_candles=magnifier,
            tv_parity=False,
            exec_profile="live_reality",
            use_bid_ask=True,
        )
        trades_tv, _, _ = backtest_run._run_backtest(
            candles=candles,
            symbol="AUD_USD",
            timeframe="M15",
            units=1000,
            spread_pips=0.0,
            fill="close",
            bar_fill_policy="conservative",
            use_runner=True,
            strategy=_DummyStrategy(),
            strategy_config=cfg,
            magnify_tf=None,
            magnify_policy="conservative",
            magnifier_candles=None,
            tv_parity=False,
            exec_profile="tv_panel",
            use_bid_ask=False,
        )

        def closed_count(trades):
            return sum(1 for t in trades if t.leg != "ENTRY")

        self.assertEqual(closed_count(trades_live), closed_count(trades_tv))
        self.assertAlmostEqual(trades_live[0].entry_price, candles[1]["c"], places=6)

    def test_exit_inspection_closes_open_position_intrabar(self) -> None:
        candles = []
        start = backtest_run.datetime(2026, 2, 1, 0, 0, tzinfo=backtest_run.timezone.utc)
        for i in range(6):
            ts = (start + backtest_run.timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z")
            candles.append({"time": ts, "o": 1.0, "h": 1.0002, "l": 0.9998, "c": 1.0, "volume": 100})

        magnifier = []
        for i in range(6):
            base = start + backtest_run.timedelta(minutes=15 * i)
            for j in range(15):
                ts = (base + backtest_run.timedelta(minutes=j)).isoformat().replace("+00:00", "Z")
                price = 1.0 + (j * 0.00001)
                magnifier.append(
                    {"time": ts, "o": price, "h": price + 0.0001, "l": price - 0.0001, "c": price, "volume": 10}
                )

        cfg = StrategyConfig(timeframe="M15", min_hold_bars=0, trend_ema_period=50, enabled=True, sl_pips=200)
        trades, _, _ = backtest_run._run_backtest(
            candles=candles,
            symbol="AUD_USD",
            timeframe="M15",
            units=1000,
            spread_pips=0.0,
            fill="close",
            bar_fill_policy="conservative",
            use_runner=False,
            strategy=_ExitInspectStrategy(),
            strategy_config=cfg,
            magnify_tf="M1",
            magnify_policy="conservative",
            magnifier_candles=magnifier,
            tv_parity=False,
            exec_profile="live_reality",
            use_bid_ask=False,
            exit_inspect_tf="M1",
            exit_inspect_candles=magnifier,
        )

        exits = [t for t in trades if t.leg == "EXIT"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].reason, "EXIT_INSPECT_MOMENTUM_FAIL_STOP")


if __name__ == "__main__":
    unittest.main()
