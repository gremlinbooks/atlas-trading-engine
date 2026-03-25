from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from app.learning.policy_trainer import (
    TrainerConfig,
    load_decision_samples,
    load_trade_samples,
    summarize_blocked_opportunities,
    train_policy,
)


class PolicyTrainerTests(unittest.TestCase):
    def test_load_trade_samples_aggregates_closed_legs_by_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trades.csv"
            self._write_rows(
                path,
                [
                    {
                        "entry_ts": "2026-03-01T00:00:00.000000000Z",
                        "exit_ts": "2026-03-01T00:00:00.000000000Z",
                        "side": "LONG",
                        "leg": "ENTRY",
                        "reason": "ENTRY",
                        "pnl_usd": "0",
                        "hold_bars": "0",
                        "mae_pips": "0",
                        "entry_components": "cross=1;pullback=1;rejoin=0;continuation=0",
                    },
                    {
                        "entry_ts": "2026-03-01T00:00:00.000000000Z",
                        "exit_ts": "2026-03-01T01:00:00.000000000Z",
                        "side": "LONG",
                        "leg": "TP1",
                        "reason": "TP1",
                        "pnl_usd": "10",
                        "hold_bars": "4",
                        "mae_pips": "-3",
                        "entry_components": "",
                    },
                    {
                        "entry_ts": "2026-03-01T00:00:00.000000000Z",
                        "exit_ts": "2026-03-01T01:00:00.000000000Z",
                        "side": "LONG",
                        "leg": "RUNNER",
                        "reason": "RUNNER_STOP",
                        "pnl_usd": "15",
                        "hold_bars": "4",
                        "mae_pips": "-3",
                        "entry_components": "",
                    },
                ],
            )
            samples = load_trade_samples([path])

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].pnl_usd, 25.0)
        self.assertTrue(samples[0].entry_components["cross"])
        self.assertTrue(samples[0].entry_components["pullback"])
        self.assertFalse(samples[0].entry_components["rejoin"])

    def test_train_policy_recommends_disabling_bad_components_and_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trades.csv"
            rows = []
            for idx in range(8):
                ts = f"2026-03-01T03:{idx:02d}:00.000000000Z"
                rows.extend(
                    [
                        {
                            "entry_ts": ts,
                            "exit_ts": ts,
                            "side": "LONG",
                            "leg": "ENTRY",
                            "reason": "ENTRY",
                            "pnl_usd": "0",
                            "hold_bars": "0",
                            "mae_pips": "0",
                            "entry_components": "cross=0;pullback=0;rejoin=0;continuation=1",
                        },
                        {
                            "entry_ts": ts,
                            "exit_ts": f"2026-03-01T03:{idx:02d}:30.000000000Z",
                            "side": "LONG",
                            "leg": "EXIT",
                            "reason": "SL",
                            "pnl_usd": "-20",
                            "hold_bars": "1",
                            "mae_pips": "-10",
                            "entry_components": "",
                        },
                    ]
                )
            self._write_rows(path, rows)
            report = train_policy(
                trade_paths=[path],
                config=TrainerConfig(min_component_trades=4, min_hour_trades=4),
            )

        env_patch = report["recommendations"]["env_patch"]
        self.assertEqual(env_patch["STRATEGY_CONT_ENABLED"], "false")
        self.assertEqual(env_patch["STRATEGY_BLOCK_ENTRY_HOURS_UTC"], "3")

    def test_decision_samples_summarize_blocked_opportunities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "decision.jsonl"
            rows = [
                {
                    "fields": {
                        "candle_ts": "2026-03-01T03:00:00.000000000Z",
                        "signal": "HOLD",
                        "reason": "no entry (stoch_filter)",
                        "metadata": {
                            "entry_diag": {
                                "blocked_reasons": ["stoch_filter"],
                                "intent": {"long": True, "short": False},
                                "components": {
                                    "long": {"cross": True, "pullback": True, "rejoin": False, "continuation": False},
                                    "short": {"cross": False, "pullback": False, "rejoin": False, "continuation": False},
                                },
                            }
                        },
                    }
                },
                {
                    "fields": {
                        "candle_ts": "2026-03-01T04:00:00.000000000Z",
                        "signal": "HOLD",
                        "reason": "no entry (no_intent)",
                        "metadata": {
                            "entry_diag": {
                                "blocked_reasons": ["no_intent"],
                                "intent": {"long": False, "short": False},
                                "components": {
                                    "long": {"cross": False, "pullback": False, "rejoin": False, "continuation": False},
                                    "short": {"cross": False, "pullback": False, "rejoin": False, "continuation": False},
                                },
                            }
                        },
                    }
                },
            ]
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

            decisions = load_decision_samples([path])
            summary = summarize_blocked_opportunities(decisions)

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["stoch_filter_count"], 1)
        self.assertEqual(summary["no_intent_count"], 1)
        self.assertIn("3", summary["by_hour_utc"])
        self.assertIn("4", summary["by_hour_utc"])

    def _write_rows(self, path: Path, rows: list[dict[str, str]]) -> None:
        fieldnames = [
            "entry_ts",
            "exit_ts",
            "side",
            "leg",
            "reason",
            "pnl_usd",
            "hold_bars",
            "mae_pips",
            "entry_components",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
