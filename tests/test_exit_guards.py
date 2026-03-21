from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.engine.strategies.oakbridge_fxtrader_v2 import OakBridgeFxTraderV2
from app.engine.strategy_base import Candle, PositionState, StrategyConfig, StrategyContext, StrategyState


def _make_candles(count: int, base_price: float = 1.0) -> list[Candle]:
    start = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    candles: list[Candle] = []
    for i in range(count):
        ts = (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z")
        candles.append(
            Candle(
                ts=ts,
                o=base_price,
                h=base_price + 0.0002,
                l=base_price - 0.0002,
                c=base_price,
                volume=1000,
            )
        )
    return candles


class ExitGuardsTests(unittest.TestCase):
    def test_entry_hour_block_blocks_otherwise_valid_signal(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-2] = Candle(
            ts=candles[-2].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9990,
            volume=1000,
        )
        candles[-1] = Candle(
            ts=candles[-1].ts,  # 04:45 UTC from _make_candles()
            o=1.0020,
            h=1.0022,
            l=1.0018,
            c=1.0020,
            volume=1000,
        )
        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            pb_enabled=False,
            cont_enabled=False,
            rejoin_enabled=False,
            stoch_entry_mode="Off",
            block_trades=True,
            block_entry_hours_utc="4,11",
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "HOLD")
        self.assertIn("time_gate", decision.reason)

    def test_entry_hour_block_allows_signal_when_hour_not_blocked(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-2] = Candle(
            ts=candles[-2].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9990,
            volume=1000,
        )
        candles[-1] = Candle(
            ts=candles[-1].ts,  # 04:45 UTC from _make_candles()
            o=1.0020,
            h=1.0022,
            l=1.0018,
            c=1.0020,
            volume=1000,
        )
        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            pb_enabled=False,
            cont_enabled=False,
            rejoin_enabled=False,
            stoch_entry_mode="Off",
            block_trades=True,
            block_entry_hours_utc="11,17",
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "ENTER_LONG")

    def test_early_flip_allowed_when_opposite_signal_and_loss_exceeds_threshold(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-2] = Candle(
            ts=candles[-2].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9990,
            volume=1000,
        )
        candles[-1] = Candle(
            ts=candles[-1].ts,
            o=1.0020,
            h=1.0022,
            l=1.0018,
            c=1.0020,
            volume=1000,
        )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=3,
            trend_ema_period=50,
            enabled=True,
            pb_enabled=False,
            cont_enabled=False,
            use_bias=False,
            sl_pips=50,
            drawdown_stop_pips=15,
            drawdown_stop_bars=0,
            force_flip=True,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="SHORT", units=1000, avg_price=1.0, entry_ts=candles[-2].ts),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "FLIP_LONG")
        self.assertEqual(decision.reason, "long entry")

    def test_no_early_flip_before_min_hold_when_force_flip_disabled(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-2] = Candle(
            ts=candles[-2].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9990,
            volume=1000,
        )
        candles[-1] = Candle(
            ts=candles[-1].ts,
            o=1.0020,
            h=1.0022,
            l=1.0018,
            c=1.0020,
            volume=1000,
        )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=3,
            trend_ema_period=50,
            enabled=True,
            pb_enabled=False,
            cont_enabled=False,
            use_bias=False,
            sl_pips=50,
            drawdown_stop_pips=15,
            drawdown_stop_bars=0,
            force_flip=False,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="SHORT", units=1000, avg_price=1.0, entry_ts=candles[-2].ts),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "HOLD")
        self.assertTrue(decision.reason.startswith("no entry"))

    def test_max_hold_bars_forced_exit(self) -> None:
        candles = _make_candles(30, 1.0)
        entry_ts = candles[-11].ts

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=3,
            trend_ema_period=50,
            enabled=True,
            sl_pips=50,
            tp1_pips=20,
            max_hold_bars=5,
            drawdown_stop_bars=0,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="LONG", units=1000, avg_price=1.0, entry_ts=entry_ts),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "EXIT")
        self.assertEqual(decision.reason, "max hold stop")

    def test_drawdown_time_stop_forced_exit(self) -> None:
        candles = _make_candles(30, 1.0)
        # Keep SL untouched while closing deep enough in drawdown.
        candles[-1] = Candle(
            ts=candles[-1].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9984,  # -16 pips for LONG
            volume=1000,
        )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=3,
            trend_ema_period=50,
            enabled=True,
            sl_pips=50,
            tp1_pips=20,
            max_hold_bars=0,
            drawdown_stop_pips=15,
            drawdown_stop_bars=3,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="LONG", units=1000, avg_price=1.0, entry_ts=candles[-5].ts),
            config=config,
            state=StrategyState(drawdown_bars=2),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "EXIT")
        self.assertEqual(decision.reason, "drawdown time stop")

    def test_rejoin_entry_can_trigger_without_new_cross(self) -> None:
        candles = _make_candles(24, 1.0)
        custom_closes = [
            1.0001280,
            1.0001991,
            1.0003563,
            1.0004331,
            1.0005842,
            1.0007494,
            1.0008159,
            1.0009343,
            1.0010983,
            1.0012163,
            1.0014119,
            1.0015764,
            1.0016573,
            1.0017862,
            1.0018719,
            1.0019615,
            1.0021194,
            1.0022124,
            1.0023644,
            1.0025605,
            1.0027208,
            1.0028712,
            1.0023247,
            1.0041086,
        ]
        for i, close in enumerate(custom_closes):
            candles[i] = Candle(
                ts=candles[i].ts,
                o=close,
                h=close + 0.0002,
                l=close - 0.0002,
                c=close,
                volume=1000,
            )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            use_bias=True,
            pb_enabled=False,
            cont_enabled=False,
            rejoin_enabled=True,
            stoch_entry_mode="Off",
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "ENTER_LONG")
        self.assertTrue(decision.metadata["entry_diag"]["components"]["long"]["rejoin"])

    def test_rejoin_long_respects_side_toggle(self) -> None:
        candles = _make_candles(24, 1.0)
        custom_closes = [
            1.0001280,
            1.0001991,
            1.0003563,
            1.0004331,
            1.0005842,
            1.0007494,
            1.0008159,
            1.0009343,
            1.0010983,
            1.0012163,
            1.0014119,
            1.0015764,
            1.0016573,
            1.0017862,
            1.0018719,
            1.0019615,
            1.0021194,
            1.0022124,
            1.0023644,
            1.0025605,
            1.0027208,
            1.0028712,
            1.0023247,
            1.0041086,
        ]
        for i, close in enumerate(custom_closes):
            candles[i] = Candle(
                ts=candles[i].ts,
                o=close,
                h=close + 0.0002,
                l=close - 0.0002,
                c=close,
                volume=1000,
            )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            use_bias=True,
            pb_enabled=False,
            cont_enabled=False,
            rejoin_enabled=True,
            rejoin_enabled_long=False,
            stoch_entry_mode="Off",
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "HOLD")
        self.assertTrue(decision.reason.startswith("no entry"))

    def test_early_loss_cut_exits_before_full_stop(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-1] = Candle(
            ts=candles[-1].ts,
            o=0.9991,
            h=0.9992,
            l=0.9989,
            c=0.9991,
            volume=1000,
        )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            sl_pips=50,
            early_loss_cut_pips=8,
            drawdown_stop_bars=0,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="LONG", units=1000, avg_price=1.0, entry_ts=candles[-3].ts),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "EXIT")
        self.assertEqual(decision.reason, "early loss cut")

    def test_momentum_fail_exit_closes_losing_long(self) -> None:
        candles = _make_candles(24, 1.0)
        down_closes = [
            1.0030,
            1.0028,
            1.0026,
            1.0024,
            1.0022,
            1.0020,
            1.0018,
            1.0016,
            1.0014,
            1.0012,
            1.0010,
            1.0008,
            1.0006,
            1.0004,
            1.0002,
            1.0000,
            0.9998,
            0.9996,
            0.9994,
            0.9992,
            0.9990,
            0.9988,
            0.9986,
            0.9985,
        ]
        for i, close in enumerate(down_closes):
            candles[i] = Candle(
                ts=candles[i].ts,
                o=close,
                h=close + 0.0002,
                l=close - 0.0002,
                c=close,
                volume=1000,
            )

        config = StrategyConfig(
            timeframe="M15",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            sl_pips=80,
            early_loss_cut_pips=0,
            momentum_fail_exit_pips=8,
            drawdown_stop_bars=0,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M15",
            position=PositionState(side="LONG", units=1000, avg_price=1.0, entry_ts=candles[-4].ts),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
        )

        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "EXIT")
        self.assertEqual(decision.reason, "momentum fail stop")

    def test_exit_only_mode_does_not_open_new_entry(self) -> None:
        candles = _make_candles(20, 1.0)
        candles[-2] = Candle(
            ts=candles[-2].ts,
            o=0.9990,
            h=0.9992,
            l=0.9988,
            c=0.9990,
            volume=1000,
        )
        candles[-1] = Candle(
            ts=candles[-1].ts,
            o=1.0020,
            h=1.0022,
            l=1.0018,
            c=1.0020,
            volume=1000,
        )
        config = StrategyConfig(
            timeframe="M1",
            min_hold_bars=1,
            trend_ema_period=50,
            enabled=True,
            pb_enabled=False,
            cont_enabled=False,
            use_bias=False,
        )
        ctx = StrategyContext(
            symbol="AUD_USD",
            timeframe="M1",
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=len(candles) - 1,
            exit_only=True,
        )
        decision = OakBridgeFxTraderV2().evaluate(candles, ctx)
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.reason, "flat")


if __name__ == "__main__":
    unittest.main()
