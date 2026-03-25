#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT_ROOT = REPORTS / "learning"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.learning.policy_trainer import TrainerConfig, train_policy, write_training_report


def main() -> int:
    args = _parse_args()
    trade_paths = _resolve_trade_paths(args)
    decision_paths = _resolve_decision_paths(args)
    report = train_policy(
        trade_paths=trade_paths,
        config=TrainerConfig(
            min_component_trades=args.min_component_trades,
            min_hour_trades=args.min_hour_trades,
            disable_profit_factor=args.disable_profit_factor,
            disable_avg_pnl_usd=args.disable_avg_pnl_usd,
            block_hour_profit_factor=args.block_hour_profit_factor,
        ),
        decision_paths=decision_paths,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = OUT_ROOT / f"policy_training_{stamp}.json"
    write_training_report(report=report, output_path=output_path)

    print(f"Policy training report: {output_path}")
    print(f"Samples: {report['sample_count']}")
    print(f"Overall PF: {report['overall']['profit_factor']:.3f}")
    print(f"Overall PnL: {report['overall']['total_pnl_usd']:.2f} USD")
    env_patch = report["recommendations"]["env_patch"]
    if env_patch:
        print("Recommended env patch:")
        for key, value in sorted(env_patch.items()):
            print(f"  {key}={value}")
    else:
        print("Recommended env patch: none")
    blocked = report.get("blocked_opportunities")
    if blocked:
        print(f"Blocked opportunities: {blocked['count']}")
        if blocked.get("review_suggestions"):
            print("Review suggestions:")
            for item in blocked["review_suggestions"]:
                print(f"  - {item}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a simple strategy policy from completed backtest trade reports")
    parser.add_argument("--trades_csv", action="append", default=[], help="Specific backtest_trades_*.csv to include")
    parser.add_argument("--latest", type=int, default=5, help="Use the latest N trade reports when --trades_csv is omitted")
    parser.add_argument("--min_component_trades", type=int, default=8)
    parser.add_argument("--min_hour_trades", type=int, default=6)
    parser.add_argument("--disable_profit_factor", type=float, default=0.95)
    parser.add_argument("--disable_avg_pnl_usd", type=float, default=0.0)
    parser.add_argument("--block_hour_profit_factor", type=float, default=0.9)
    parser.add_argument("--decisions_jsonl", action="append", default=[], help="Decision journal(s) to analyze")
    return parser.parse_args()


def _resolve_trade_paths(args: argparse.Namespace) -> list[Path]:
    if args.trades_csv:
        paths = [Path(path) for path in args.trades_csv]
    else:
        candidates = sorted(
            REPORTS.glob("backtest_trades_*.csv"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        paths = candidates[: max(1, args.latest)]
    if not paths:
        raise SystemExit("No backtest trade reports found")
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"Missing trade report(s): {', '.join(str(path) for path in missing)}")
    return paths


def _resolve_decision_paths(args: argparse.Namespace) -> list[Path]:
    if not args.decisions_jsonl:
        return []
    paths = [Path(path) for path in args.decisions_jsonl]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"Missing decision journal(s): {', '.join(str(path) for path in missing)}")
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
