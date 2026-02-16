from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from app.broker.oanda import OandaClient
from app.config import get_settings


@dataclass
class ClosedLeg:
    entry_ts: str
    exit_ts: str
    side: str
    leg: str
    reason: str
    pnl_usd: float
    pnl_pips_weighted: float
    mae_pips: float
    hold_bars: int
    entry_origin: str


def main() -> None:
    args = _parse_args()
    trade_path = _resolve_trade_path(args.trades_csv)
    closed_legs = _load_closed_legs(trade_path)
    if not closed_legs:
        raise SystemExit(f"No closed trade legs found in {trade_path}")

    symbol, timeframe, run_id = _parse_report_identity(trade_path)
    diagnostics: dict[str, Any] = {
        "source": {
            "trades_csv": str(trade_path),
            "symbol": symbol,
            "timeframe": timeframe,
            "run_id": run_id,
        },
        "overall": _overall_stats(closed_legs),
        "by_exit_reason": _by_group(closed_legs, lambda t: t.reason),
        "by_leg": _by_group(closed_legs, lambda t: t.leg),
        "by_entry_origin": _by_group(closed_legs, lambda t: t.entry_origin),
        "by_entry_hour_utc": _by_group(closed_legs, lambda t: str(_parse_ts(t.entry_ts).hour)),
        "by_entry_weekday_utc": _by_group(closed_legs, lambda t: str(_parse_ts(t.entry_ts).weekday())),
        "stopouts": _stopout_stats(closed_legs),
    }

    candle_context: dict[str, Any] | None = None
    if args.with_candles:
        candle_context = _build_candle_context(
            closed_legs=closed_legs,
            symbol=symbol,
            timeframe=timeframe,
        )
        if candle_context is not None:
            diagnostics["candle_context"] = candle_context

    diagnostics["recommendations"] = _recommendations(diagnostics)

    output_path = _output_path(symbol=symbol, timeframe=timeframe, run_id=run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

    _print_summary(diagnostics, output_path, candle_context_available=candle_context is not None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest diagnostics and trade forensics")
    parser.add_argument("--trades_csv", help="Path to backtest_trades_*.csv; latest is used if omitted")
    parser.add_argument("--with_candles", type=_parse_bool, default=True)
    return parser.parse_args()


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_trade_path(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if not path.exists():
            raise SystemExit(f"Trades CSV not found: {path}")
        return path
    reports = Path("reports")
    candidates = sorted(reports.glob("backtest_trades_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("No reports/backtest_trades_*.csv found")
    return candidates[0]


def _load_closed_legs(trade_path: Path) -> list[ClosedLeg]:
    entry_origin_by_ts: dict[str, str] = {}
    closed: list[ClosedLeg] = []

    with trade_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leg = row.get("leg", "")
            entry_ts = row.get("entry_ts", "")
            if leg == "ENTRY":
                entry_origin_by_ts[entry_ts] = row.get("reason", "ENTRY")
                continue
            closed.append(
                ClosedLeg(
                    entry_ts=entry_ts,
                    exit_ts=row.get("exit_ts", ""),
                    side=row.get("side", ""),
                    leg=leg,
                    reason=row.get("reason", ""),
                    pnl_usd=float(row.get("pnl_usd", 0) or 0),
                    pnl_pips_weighted=float(row.get("pnl_pips_weighted", 0) or 0),
                    mae_pips=float(row.get("mae_pips", 0) or 0),
                    hold_bars=int(float(row.get("hold_bars", 0) or 0)),
                    entry_origin=entry_origin_by_ts.get(entry_ts, "UNKNOWN"),
                )
            )
    return closed


def _overall_stats(trades: list[ClosedLeg]) -> dict[str, Any]:
    wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losses = [t.pnl_usd for t in trades if t.pnl_usd < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "count": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "total_pnl_usd": round(sum(t.pnl_usd for t in trades), 6),
        "avg_win_usd": round(mean(wins), 6) if wins else 0.0,
        "avg_loss_usd": round(mean(losses), 6) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else 0.0,
        "avg_hold_bars": round(mean(t.hold_bars for t in trades), 4) if trades else 0.0,
        "avg_mae_pips": round(mean(t.mae_pips for t in trades), 4) if trades else 0.0,
    }


def _by_group(trades: list[ClosedLeg], key_fn) -> dict[str, Any]:
    buckets: dict[str, list[ClosedLeg]] = defaultdict(list)
    for trade in trades:
        buckets[key_fn(trade)].append(trade)
    out: dict[str, Any] = {}
    for key, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        wins = sum(1 for t in items if t.pnl_usd > 0)
        losses = sum(1 for t in items if t.pnl_usd < 0)
        out[key] = {
            "count": len(items),
            "win_rate_pct": round((wins / len(items) * 100), 4),
            "pnl_usd": round(sum(t.pnl_usd for t in items), 6),
            "avg_pnl_usd": round(mean(t.pnl_usd for t in items), 6),
            "avg_hold_bars": round(mean(t.hold_bars for t in items), 4),
            "avg_mae_pips": round(mean(t.mae_pips for t in items), 4),
            "wins": wins,
            "losses": losses,
        }
    return out


def _stopout_stats(trades: list[ClosedLeg]) -> dict[str, Any]:
    stopouts = [t for t in trades if t.reason == "SL"]
    if not stopouts:
        return {"count": 0}
    hour_counts = Counter(_parse_ts(t.exit_ts).hour for t in stopouts)
    return {
        "count": len(stopouts),
        "pct_of_all_closed_legs": round(len(stopouts) / len(trades) * 100, 4),
        "total_pnl_usd": round(sum(t.pnl_usd for t in stopouts), 6),
        "avg_pnl_usd": round(mean(t.pnl_usd for t in stopouts), 6),
        "by_entry_origin": _by_group(stopouts, lambda t: t.entry_origin),
        "top_exit_hours_utc": sorted(
            [{"hour": int(h), "count": c} for h, c in hour_counts.items()],
            key=lambda x: (-x["count"], x["hour"]),
        )[:5],
    }


def _build_candle_context(
    *,
    closed_legs: list[ClosedLeg],
    symbol: str,
    timeframe: str,
) -> Optional[dict[str, Any]]:
    settings = get_settings()
    if not settings.oanda_api_key or not settings.oanda_account_id or not settings.oanda_env:
        return None

    from_dt = min(_parse_ts(t.entry_ts) for t in closed_legs) - timedelta(days=2)
    to_dt = max(_parse_ts(t.exit_ts) for t in closed_legs) + timedelta(days=1)

    try:
        client = OandaClient()
        candles = _fetch_candles(client=client, symbol=symbol, timeframe=timeframe, from_dt=from_dt, to_dt=to_dt)
    except Exception:
        return None
    if not candles:
        return None

    close_series = [float(c["c"]) for c in candles]
    ema50 = _ema(close_series, 50)
    idx_by_time = {c["time"]: i for i, c in enumerate(candles)}
    pip = 0.01 if "JPY" in symbol else 0.0001
    ranges = [(float(c["h"]) - float(c["l"])) / pip for c in candles]

    aligned: list[float] = []
    counter: list[float] = []
    vol_buckets: dict[str, list[float]] = {"low": [], "mid": [], "high": []}
    skipped = 0

    for trade in closed_legs:
        index = idx_by_time.get(trade.entry_ts)
        if index is None or index < 49:
            skipped += 1
            continue
        close = float(candles[index]["c"])
        trend_up = close > ema50[index]
        is_aligned = (trade.side == "LONG" and trend_up) or (trade.side == "SHORT" and not trend_up)
        if is_aligned:
            aligned.append(trade.pnl_usd)
        else:
            counter.append(trade.pnl_usd)

        entry_range = ranges[index]
        p33, p66 = _percentiles(ranges, 0.33, 0.66)
        if entry_range <= p33:
            vol_buckets["low"].append(trade.pnl_usd)
        elif entry_range <= p66:
            vol_buckets["mid"].append(trade.pnl_usd)
        else:
            vol_buckets["high"].append(trade.pnl_usd)

    return {
        "sample_size": len(closed_legs) - skipped,
        "skipped_missing_entry_candle": skipped,
        "ema50_alignment": {
            "aligned": _simple_bucket(aligned),
            "counter_trend": _simple_bucket(counter),
        },
        "entry_range_regime_pnl": {
            "low": _simple_bucket(vol_buckets["low"]),
            "mid": _simple_bucket(vol_buckets["mid"]),
            "high": _simple_bucket(vol_buckets["high"]),
        },
    }


def _fetch_candles(
    *,
    client: OandaClient,
    symbol: str,
    timeframe: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    timeframe_minutes = _timeframe_to_minutes(timeframe)
    chunk_minutes = 5000 * timeframe_minutes
    all_candles: dict[str, dict[str, Any]] = {}
    current = from_dt
    while current < to_dt:
        chunk_to = min(current + timedelta(minutes=chunk_minutes), to_dt)
        batch = client.get_candles_range(
            symbol=symbol,
            granularity=timeframe,
            from_ts=_to_rfc3339(current),
            to_ts=_to_rfc3339(chunk_to),
            count=None,
            include_first=True,
        )
        for candle in batch:
            all_candles[candle["time"]] = candle
        current = chunk_to
    return [all_candles[k] for k in sorted(all_candles)]


def _timeframe_to_minutes(timeframe: str) -> int:
    mapping = {
        "M1": 1,
        "M2": 2,
        "M4": 4,
        "M5": 5,
        "M10": 10,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H2": 120,
        "H3": 180,
        "H4": 240,
        "H6": 360,
        "H8": 480,
        "H12": 720,
        "D": 1440,
    }
    if timeframe.upper() not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return mapping[timeframe.upper()]


def _to_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str) -> datetime:
    token = value.strip()
    if token.endswith("Z"):
        token = token[:-1] + "+00:00"
    if "." in token:
        head, tail = token.split(".", 1)
        frac, zone = tail[:], ""
        if "+" in tail:
            frac, zone = tail.split("+", 1)
            zone = "+" + zone
        elif "-" in tail:
            frac, zone = tail.split("-", 1)
            zone = "-" + zone
        frac = frac[:6]
        token = f"{head}.{frac}{zone}"
    return datetime.fromisoformat(token)


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def _percentiles(values: list[float], p1: float, p2: float) -> tuple[float, float]:
    ordered = sorted(values)
    n = len(ordered)
    i1 = min(max(int(p1 * (n - 1)), 0), n - 1)
    i2 = min(max(int(p2 * (n - 1)), 0), n - 1)
    return ordered[i1], ordered[i2]


def _simple_bucket(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "pnl_usd": 0.0, "avg_pnl_usd": 0.0, "win_rate_pct": 0.0}
    wins = sum(1 for v in values if v > 0)
    return {
        "count": len(values),
        "pnl_usd": round(sum(values), 6),
        "avg_pnl_usd": round(mean(values), 6),
        "win_rate_pct": round(wins / len(values) * 100, 4),
    }


def _recommendations(diagnostics: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    overall = diagnostics["overall"]
    by_origin = diagnostics.get("by_entry_origin", {})
    stopouts = diagnostics.get("stopouts", {})
    by_hour = diagnostics.get("by_entry_hour_utc", {})

    flip = by_origin.get("FLIP_ENTRY")
    if flip and flip["count"] >= 20 and flip["pnl_usd"] < 0:
        recs.append(
            "Flip-origin trades are net negative. Increase STRATEGY_MIN_HOLD_BARS and keep STRATEGY_ALLOW_SECOND_CHANCE=false."
        )

    if stopouts.get("count", 0) > 0 and stopouts.get("pct_of_all_closed_legs", 0) >= 15:
        recs.append(
            "Stopouts are frequent. Consider widening SL slightly and reducing early trail aggressiveness to avoid noise exits."
        )

    if overall["avg_loss_usd"] < 0 and abs(overall["avg_loss_usd"]) > overall["avg_win_usd"] * 1.3:
        recs.append(
            "Loss magnitude dominates wins. Increase TP1_CLOSE_PCT or tighten reversal/flip entries to reduce large losers."
        )

    worst_hours = sorted(
        [
            (int(hour), stats["pnl_usd"], stats["count"])
            for hour, stats in by_hour.items()
            if stats["count"] >= 5
        ],
        key=lambda x: x[1],
    )[:3]
    if worst_hours and worst_hours[0][1] < 0:
        hours = ", ".join(f"{h:02d}:00" for h, _, _ in worst_hours)
        recs.append(
            f"Entry-hour underperformance detected around UTC {hours}. Consider blocking these sessions via STRATEGY_BLOCK_SESSION."
        )

    candle_ctx = diagnostics.get("candle_context")
    if candle_ctx:
        aligned = candle_ctx["ema50_alignment"]["aligned"]
        counter = candle_ctx["ema50_alignment"]["counter_trend"]
        if aligned["count"] >= 20 and counter["count"] >= 20 and counter["avg_pnl_usd"] < aligned["avg_pnl_usd"]:
            recs.append("Counter-trend entries underperform trend-aligned entries. Keep STRATEGY_USE_BIAS=true.")

    if not recs:
        recs.append("No dominant failure mode found. Run a parameter sweep around hold bars, SL, and TP1 sizing.")
    return recs


def _parse_report_identity(path: Path) -> tuple[str, str, str]:
    name = path.stem  # backtest_trades_AUD_USD_M15_20260215
    if not name.startswith("backtest_trades_"):
        return "UNKNOWN", "UNKNOWN", datetime.now(timezone.utc).strftime("%Y%m%d")
    rest = name.removeprefix("backtest_trades_")
    parts = rest.split("_")
    if len(parts) < 4:
        return "UNKNOWN", "UNKNOWN", datetime.now(timezone.utc).strftime("%Y%m%d")
    run_id = parts[-1]
    timeframe = parts[-2]
    symbol = "_".join(parts[:-2])
    return symbol, timeframe, run_id


def _output_path(*, symbol: str, timeframe: str, run_id: str) -> Path:
    return Path("reports") / f"diagnostics_{symbol}_{timeframe}_{run_id}.json"


def _print_summary(diagnostics: dict[str, Any], out_path: Path, *, candle_context_available: bool) -> None:
    overall = diagnostics["overall"]
    print("Diagnostics Summary")
    print(f"Closed legs analyzed: {overall['count']}")
    print(f"Win rate: {overall['win_rate_pct']:.2f}%")
    print(f"Profit factor: {overall['profit_factor']:.4f}")
    print(f"Total PnL USD: {overall['total_pnl_usd']:.4f}")
    print(f"Avg win/loss USD: {overall['avg_win_usd']:.4f} / {overall['avg_loss_usd']:.4f}")
    print(f"Candle context: {'enabled' if candle_context_available else 'unavailable'}")
    print(f"Report: {out_path}")
    print("Top recommendations:")
    for rec in diagnostics["recommendations"][:5]:
        print(f"- {rec}")


if __name__ == "__main__":
    main()
