import unittest

from app.engine.indicators import atr, bars_since, crossover, crossunder, ema, rsi, stoch_rsi
from app.engine.strategy_base import Candle


class IndicatorsTests(unittest.TestCase):
    def test_ema(self) -> None:
        values = [1.0, 2.0, 3.0]
        result = ema(values, 3)
        self.assertAlmostEqual(result[-1], 2.25, places=6)

    def test_rsi_increasing(self) -> None:
        values = [1, 2, 3, 4, 5, 6]
        result = rsi(values, 2)
        self.assertEqual(result[-1], 100.0)

    def test_atr_constant(self) -> None:
        candles = [
            Candle(ts="t1", o=1, h=2, l=0, c=1, volume=1),
            Candle(ts="t2", o=1, h=2, l=0, c=1, volume=1),
            Candle(ts="t3", o=1, h=2, l=0, c=1, volume=1),
        ]
        result = atr(candles, 2)
        self.assertAlmostEqual(result[-1], 2.0, places=6)

    def test_cross(self) -> None:
        self.assertTrue(crossover(0, 1, 2, 1))
        self.assertTrue(crossunder(2, 1, 0, 1))

    def test_bars_since(self) -> None:
        series = [False, False, True, False]
        self.assertEqual(bars_since(series), 1)

    def test_stoch_rsi(self) -> None:
        values = [i for i in range(1, 30)]
        k, d = stoch_rsi(values, 14, 14, 3, 3)
        self.assertEqual(len(k), len(values))
        self.assertEqual(len(d), len(values))


if __name__ == "__main__":
    unittest.main()
