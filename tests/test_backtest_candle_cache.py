import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.backtest import run as backtest_run
from app.backtest.candle_cache import CandleCache


class _NoNetworkClient:
    def get_candles_range(self, **kwargs):
        raise AssertionError("Network fetch should not be called when cache fully covers the range")


def _candle(ts: datetime, price: float = 1.0) -> dict:
    return {
        "time": ts.isoformat().replace("+00:00", "Z"),
        "o": price,
        "h": price + 0.0001,
        "l": price - 0.0001,
        "c": price,
        "volume": 100,
    }


class CandleCacheTests(unittest.TestCase):
    def test_fetch_uses_cache_without_network_when_window_fully_covered(self) -> None:
        start = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        candles = [_candle(start + timedelta(minutes=15 * i), 1.0 + i * 0.0001) for i in range(4)]

        with tempfile.TemporaryDirectory() as td:
            cache = CandleCache(Path(td) / "candles_cache.db")
            cache.upsert(symbol="AUD_USD", timeframe="M15", candles=candles)

            loaded = backtest_run._fetch_candles(
                client=_NoNetworkClient(),
                symbol="AUD_USD",
                timeframe="M15",
                from_dt=start,
                to_dt=end,
                cache=cache,
                refresh_cache=False,
            )

        self.assertEqual(len(loaded), 4)
        self.assertEqual(loaded[0]["time"], candles[0]["time"])
        self.assertEqual(loaded[-1]["time"], candles[-1]["time"])


if __name__ == "__main__":
    unittest.main()
