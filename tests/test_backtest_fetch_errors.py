import unittest

import requests

from app.backtest import run as backtest_run


class _FailingClient:
    def get_candles_range(self, **kwargs):
        raise requests.ConnectionError("dns failed")


class FetchErrorTests(unittest.TestCase):
    def test_fetch_candles_reports_actionable_error(self) -> None:
        start = backtest_run.datetime(2026, 2, 1, 0, 0, tzinfo=backtest_run.timezone.utc)
        end = start + backtest_run.timedelta(minutes=15)

        with self.assertRaises(SystemExit) as ctx:
            backtest_run._fetch_candles(
                client=_FailingClient(),
                symbol="AUD_USD",
                timeframe="M15",
                from_dt=start,
                to_dt=end,
            )

        msg = str(ctx.exception)
        self.assertIn("Failed to fetch candles from OANDA.", msg)
        self.assertIn("symbol=AUD_USD", msg)
        self.assertIn("timeframe=M15", msg)


if __name__ == "__main__":
    unittest.main()
