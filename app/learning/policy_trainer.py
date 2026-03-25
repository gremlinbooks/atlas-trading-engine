from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


@dataclass(frozen=True)
class TrainerConfig:
    min_component_trades: int = 8
    min_hour_trades: int = 6
    disable_profit_factor: float = 0.95
    disable_avg_pnl_usd: float = 0.0
    block_hour_profit_factor: float = 0.9


@dataclass(frozen=True)
class TradeSample:
    entry_ts: str
    side: str
    pnl_usd: float
    hold_bars: int
    mae_pips: float
    entry_components: dict[str, bool]
    source: str


@dataclass(frozen=True)
class DecisionSample:
    candle_ts: str
    signal: str
    reason: str
    blocked_reasons: tuple[str, ...]
    hour_utc: int
    long_intent: bool
    short_intent: bool
    long_components: dict[str, bool]
    short_components: dict[str, bool]
    source: str


def train_policy(
    *,
    trade_paths: list[Path],
    config: TrainerConfig,
    decision_paths: list[Path] | None = None,
) -> dict[str, Any]:
    samples = load_trade_samples(trade_paths)
    if not samples:
        raise ValueError("No closed trades found in the supplied backtest trade reports")

    overall = _summarize(samples)
    component_stats = {
        component: _summarize([s for s in samples if s.entry_components.get(component, False)])
        for component in ("cross", "pullback", "rejoin", "continuation")
    }
    side_stats = {
        side: _summarize([s for s in samples if s.side == side])
        for side in ("LONG", "SHORT")
    }
    hour_stats = {
        str(hour): _summarize([s for s in samples if _parse_ts(s.entry_ts).hour == hour])
        for hour in range(24)
    }
    component_mix_stats = _component_mix_stats(samples)

    recommendations = _build_recommendations(
        component_stats=component_stats,
        hour_stats=hour_stats,
        config=config,
    )
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [str(path) for path in trade_paths],
        "trainer_config": asdict(config),
        "sample_count": len(samples),
        "overall": overall,
        "by_component": component_stats,
        "by_side": side_stats,
        "by_entry_hour_utc": hour_stats,
        "by_component_mix": component_mix_stats,
        "recommendations": recommendations,
    }
    if decision_paths:
        decisions = load_decision_samples(decision_paths)
        report["decision_sources"] = [str(path) for path in decision_paths]
        report["decision_sample_count"] = len(decisions)
        report["blocked_opportunities"] = summarize_blocked_opportunities(decisions)
    return report


def load_trade_samples(trade_paths: list[Path]) -> list[TradeSample]:
    samples: list[TradeSample] = []
    for path in trade_paths:
        samples.extend(_load_samples_from_trade_report(path))
    return sorted(samples, key=lambda sample: sample.entry_ts)


def write_training_report(*, report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _load_samples_from_trade_report(path: Path) -> list[TradeSample]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entry_ts = row.get("entry_ts", "")
            side = row.get("side", "")
            if not entry_ts or not side:
                continue
            key = (entry_ts, side)
            sample = grouped.setdefault(
                key,
                {
                    "entry_ts": entry_ts,
                    "side": side,
                    "pnl_usd": 0.0,
                    "hold_bars": 0,
                    "mae_pips": 0.0,
                    "entry_components": _parse_entry_components(row.get("entry_components", "")),
                    "source": str(path),
                    "closed_legs": 0,
                },
            )
            if row.get("leg") == "ENTRY":
                if row.get("entry_components"):
                    sample["entry_components"] = _parse_entry_components(row["entry_components"])
                continue
            sample["pnl_usd"] += float(row.get("pnl_usd", 0) or 0.0)
            sample["hold_bars"] = max(sample["hold_bars"], int(float(row.get("hold_bars", 0) or 0)))
            sample["mae_pips"] = min(sample["mae_pips"], float(row.get("mae_pips", 0) or 0.0))
            sample["closed_legs"] += 1

    samples: list[TradeSample] = []
    for sample in grouped.values():
        if sample["closed_legs"] <= 0:
            continue
        samples.append(
            TradeSample(
                entry_ts=sample["entry_ts"],
                side=sample["side"],
                pnl_usd=round(sample["pnl_usd"], 6),
                hold_bars=sample["hold_bars"],
                mae_pips=sample["mae_pips"],
                entry_components=sample["entry_components"],
                source=sample["source"],
            )
        )
    return samples


def load_decision_samples(decision_paths: list[Path]) -> list[DecisionSample]:
    samples: list[DecisionSample] = []
    for path in decision_paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                fields = payload.get("fields", {})
                metadata = fields.get("metadata", {}) or {}
                entry_diag = metadata.get("entry_diag") or {}
                blocked_reasons = entry_diag.get("blocked_reasons") or []
                components = entry_diag.get("components") or {}
                long_components = components.get("long") or {}
                short_components = components.get("short") or {}
                intent = entry_diag.get("intent") or {}
                candle_ts = fields.get("candle_ts")
                if not candle_ts:
                    continue
                dt = _parse_ts(candle_ts)
                samples.append(
                    DecisionSample(
                        candle_ts=candle_ts,
                        signal=str(fields.get("signal", "")),
                        reason=str(fields.get("reason", "")),
                        blocked_reasons=tuple(str(item) for item in blocked_reasons),
                        hour_utc=dt.hour,
                        long_intent=bool(intent.get("long", False)),
                        short_intent=bool(intent.get("short", False)),
                        long_components={name: bool(value) for name, value in long_components.items()},
                        short_components={name: bool(value) for name, value in short_components.items()},
                        source=str(path),
                    )
                )
    return sorted(samples, key=lambda sample: sample.candle_ts)


def summarize_blocked_opportunities(decisions: list[DecisionSample]) -> dict[str, Any]:
    if not decisions:
        return {"count": 0}

    blocked = [sample for sample in decisions if sample.blocked_reasons]
    stoch_blocked = [sample for sample in blocked if "stoch_filter" in sample.blocked_reasons]
    no_intent = [sample for sample in blocked if "no_intent" in sample.blocked_reasons]
    by_reason: dict[str, int] = defaultdict(int)
    by_hour: dict[str, int] = defaultdict(int)
    component_mix: dict[str, int] = defaultdict(int)

    for sample in blocked:
        for reason in sample.blocked_reasons:
            by_reason[reason] += 1
        by_hour[str(sample.hour_utc)] += 1
        component_mix[_decision_component_mix(sample)] += 1

    return {
        "count": len(blocked),
        "stoch_filter_count": len(stoch_blocked),
        "no_intent_count": len(no_intent),
        "by_reason": dict(sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))),
        "by_hour_utc": dict(sorted(by_hour.items(), key=lambda item: (-item[1], int(item[0])))),
        "by_component_mix": dict(sorted(component_mix.items(), key=lambda item: (-item[1], item[0]))),
        "review_suggestions": _blocked_review_suggestions(blocked, stoch_blocked, no_intent),
    }


def _parse_entry_components(value: str) -> dict[str, bool]:
    out = {"cross": False, "pullback": False, "rejoin": False, "continuation": False}
    for token in value.split(";"):
        if "=" not in token:
            continue
        key, raw = token.split("=", 1)
        key = key.strip()
        if key not in out:
            continue
        out[key] = raw.strip() in {"1", "true", "True"}
    return out


def _summarize(samples: list[TradeSample]) -> dict[str, Any]:
    if not samples:
        return {
            "count": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "total_pnl_usd": 0.0,
            "avg_pnl_usd": 0.0,
            "avg_hold_bars": 0.0,
            "avg_mae_pips": 0.0,
        }
    wins = [sample.pnl_usd for sample in samples if sample.pnl_usd > 0]
    losses = [sample.pnl_usd for sample in samples if sample.pnl_usd < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "count": len(samples),
        "win_rate_pct": round(len(wins) / len(samples) * 100.0, 4),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else 0.0,
        "total_pnl_usd": round(sum(sample.pnl_usd for sample in samples), 6),
        "avg_pnl_usd": round(mean(sample.pnl_usd for sample in samples), 6),
        "avg_hold_bars": round(mean(sample.hold_bars for sample in samples), 4),
        "avg_mae_pips": round(mean(sample.mae_pips for sample in samples), 4),
    }


def _component_mix_stats(samples: list[TradeSample]) -> dict[str, Any]:
    buckets: dict[str, list[TradeSample]] = defaultdict(list)
    for sample in samples:
        active = [name for name, enabled in sample.entry_components.items() if enabled]
        key = "+".join(active) if active else "none"
        buckets[key].append(sample)
    return {
        key: _summarize(items)
        for key, items in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    }


def _decision_component_mix(sample: DecisionSample) -> str:
    active: list[str] = []
    for prefix, items in (("long", sample.long_components), ("short", sample.short_components)):
        for name, enabled in items.items():
            if enabled:
                active.append(f"{prefix}:{name}")
    return "+".join(active) if active else "none"


def _blocked_review_suggestions(
    blocked: list[DecisionSample],
    stoch_blocked: list[DecisionSample],
    no_intent: list[DecisionSample],
) -> list[str]:
    suggestions: list[str] = []
    if blocked and len(stoch_blocked) / len(blocked) >= 0.35:
        suggestions.append(
            "Large share of blocked opportunities are failing the stochastic filter; compare StrictFilter vs ExtremesOnly in walk-forward tests."
        )
    if blocked and len(no_intent) / len(blocked) >= 0.5:
        suggestions.append(
            "Most blocked opportunities have no base intent; focus tuning on pullback/rejoin/continuation parameters before adding rescue layers."
        )
    return suggestions


def _build_recommendations(
    *,
    component_stats: dict[str, dict[str, Any]],
    hour_stats: dict[str, dict[str, Any]],
    config: TrainerConfig,
) -> dict[str, Any]:
    env_patch: dict[str, str] = {}
    disable_components: list[str] = []
    review_components: list[str] = []

    component_to_env = {
        "pullback": "STRATEGY_PB_ENABLED",
        "rejoin": "STRATEGY_REJOIN_ENABLED",
        "continuation": "STRATEGY_CONT_ENABLED",
    }
    for component, env_name in component_to_env.items():
        stats = component_stats[component]
        if stats["count"] < config.min_component_trades:
            continue
        if (
            stats["profit_factor"] < config.disable_profit_factor
            and stats["avg_pnl_usd"] <= config.disable_avg_pnl_usd
            and stats["total_pnl_usd"] <= 0
        ):
            env_patch[env_name] = "false"
            disable_components.append(component)
        elif stats["profit_factor"] < 1.0:
            review_components.append(component)

    bad_hours: list[int] = []
    for hour, stats in hour_stats.items():
        if stats["count"] < config.min_hour_trades:
            continue
        if stats["profit_factor"] < config.block_hour_profit_factor and stats["total_pnl_usd"] < 0:
            bad_hours.append(int(hour))
    if bad_hours:
        env_patch["STRATEGY_BLOCK_ENTRY_HOURS_UTC"] = ",".join(str(hour) for hour in sorted(bad_hours))

    return {
        "env_patch": env_patch,
        "disable_components": disable_components,
        "review_components": review_components,
        "block_hours_utc": sorted(bad_hours),
    }


def _parse_ts(value: str) -> datetime:
    token = value.replace("Z", "+00:00")
    if "." in token and "+" in token:
        left, right = token.split("+", 1)
        if "." in left:
            stem, frac = left.split(".", 1)
            left = f"{stem}.{frac[:6]}"
        token = f"{left}+{right}"
    return datetime.fromisoformat(token)
