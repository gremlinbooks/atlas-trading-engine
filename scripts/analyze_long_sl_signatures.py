from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from app.backtest import run as backtest_run
from app.backtest.candle_cache import CandleCache
from app.config import get_settings
from app.engine.strategy_base import Candle, PositionState, StrategyConfig, StrategyContext, StrategyState


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze recurring long-SL entry signatures from a backtest trade report."
    )
    parser.add_argument("--trades_csv", help="Path to backtest_trades_*.csv (latest if omitted)")
    parser.add_argument("--top", type=int, default=12, help="Rows to print per section")
    return parser.parse_args()


def _resolve_latest_trades_csv(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if not path.exists():
            raise SystemExit(f"Trades CSV not found: {path}")
        return path
    files = sorted(
        Path("reports").glob("backtest_trades_AUD_USD_M15_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise SystemExit("No reports/backtest_trades_AUD_USD_M15_*.csv files found")
    return files[0]


def _summary_from_trades_csv(trades_csv: Path) -> Path:
    run_id = trades_csv.stem.split("_")[-1]
    summary = trades_csv.parent / f"summary_AUD_USD_M15_{run_id}.json"
    if not summary.exists():
        raise SystemExit(f"Matching summary file not found: {summary}")
    return summary


def _parse_ts(ts: str) -> datetime:
    token = ts.replace("Z", "+00:00")
    token = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r".\1", token)
    return datetime.fromisoformat(token).astimezone(timezone.utc)


def _build_strategy_config(params: dict[str, Any]) -> StrategyConfig:
    settings = get_settings()
    return StrategyConfig(
        timeframe=params["timeframe"],
        min_hold_bars=settings.strategy_min_hold_bars,
        trend_ema_period=settings.strategy_trend_ema_period,
        enabled=settings.strategy_enabled,
        fast_len=settings.strategy_fast_len,
        slow_len=settings.strategy_slow_len,
        use_bias=settings.strategy_use_bias,
        invert_eurcad=settings.strategy_invert_eurcad,
        force_flip=settings.strategy_force_flip,
        tp1_pips=int(params["tp1_pips"]),
        sl_pips=int(params["sl_pips"]),
        max_hold_bars=settings.strategy_max_hold_bars,
        drawdown_stop_pips=settings.strategy_drawdown_stop_pips,
        drawdown_stop_bars=settings.strategy_drawdown_stop_bars,
        tp1_close_pct=int(params["tp1_close_pct"]),
        trail_drawdown_pct=float(params["trail_drawdown_pct"]),
        be_lock_pips=int(params["be_lock_pips"]),
        profit_floor1_trigger_pips=settings.strategy_profit_floor1_trigger_pips,
        profit_floor1_lock_pips=settings.strategy_profit_floor1_lock_pips,
        profit_floor2_trigger_pips=settings.strategy_profit_floor2_trigger_pips,
        profit_floor2_lock_pips=settings.strategy_profit_floor2_lock_pips,
        stoch_entry_mode=settings.strategy_stoch_entry_mode,
        use_stoch_exit=bool(params["use_stoch_exit"]),
        st_rsi_len=settings.strategy_st_rsi_len,
        st_stoch_len=settings.strategy_st_stoch_len,
        st_k_len=settings.strategy_st_k_len,
        st_d_len=settings.strategy_st_d_len,
        st_ob=settings.strategy_st_ob,
        st_os=settings.strategy_st_os,
        st_recent=settings.strategy_st_recent,
        st_tight_pips=int(params["st_tight_pips"]),
        block_trades=settings.strategy_block_trades,
        block_session=settings.strategy_block_session,
        quick_relax=settings.strategy_quick_relax,
        use_day_mask=settings.strategy_use_day_mask,
        block_mon=settings.strategy_block_mon,
        block_tue=settings.strategy_block_tue,
        block_wed=settings.strategy_block_wed,
        block_thu=settings.strategy_block_thu,
        block_fri=settings.strategy_block_fri,
        block_sat=settings.strategy_block_sat,
        block_sun=settings.strategy_block_sun,
        use_spread_gate=settings.strategy_use_spread_gate,
        max_spread_pips=settings.strategy_max_spread_pips,
        aggr_spread_factor=settings.strategy_aggr_spread_factor,
        hold_signal_bars=settings.strategy_hold_signal_bars,
        apply_on_history=settings.strategy_apply_on_history,
        pb_enabled=settings.strategy_pb_enabled,
        pb_lookback_bars=settings.strategy_pb_lookback_bars,
        cont_enabled=settings.strategy_cont_enabled,
        base_max_bars=settings.strategy_base_max_bars,
        base_max_range_atr=settings.strategy_base_max_range_atr,
        rejoin_enabled=settings.strategy_rejoin_enabled,
        allow_second_chance=settings.strategy_allow_second_chance,
        reenter_within_bars=settings.strategy_reenter_within_bars,
        early_loss_cut_pips=settings.strategy_early_loss_cut_pips,
        momentum_fail_exit_pips=settings.strategy_momentum_fail_exit_pips,
    )


def _load_long_sl_positions(trades_csv: Path) -> list[dict[str, Any]]:
    by_position: dict[tuple[str, str], dict[str, Any]] = {}
    with trades_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("leg") == "ENTRY":
                continue
            if row.get("side") != "LONG":
                continue
            key = (row["entry_ts"], row["side"])
            item = by_position.get(
                key,
                {
                    "entry_ts": row["entry_ts"],
                    "side": "LONG",
                    "pnl_usd": 0.0,
                    "hold_bars": 0.0,
                    "reasons": set(),
                },
            )
            pnl = float(row.get("pnl_usd") or 0)
            item["pnl_usd"] += pnl
            item["hold_bars"] = max(item["hold_bars"], float(row.get("hold_bars") or 0))
            item["reasons"].add(row.get("reason", ""))
            by_position[key] = item
    out = list(by_position.values())
    out.sort(key=lambda x: x["pnl_usd"])
    return [x for x in out if "SL" in x["reasons"]]


def _load_candles(params: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from_dt = datetime.fromisoformat(params["from"]).replace(tzinfo=timezone.utc)
    to_dt = datetime.fromisoformat(params["to"]).replace(tzinfo=timezone.utc)
    if to_dt.hour == 0 and to_dt.minute == 0 and to_dt.second == 0:
        to_dt += timedelta(days=1)
    cache = CandleCache(Path(params.get("cache_db") or "data/candles_cache.db"))
    candles = cache.load_range(
        symbol=params["symbol"],
        timeframe=params["timeframe"],
        from_dt=from_dt,
        to_dt=to_dt,
    )
    if not candles:
        raise SystemExit("No candles loaded from cache for report range")
    index_by_ts = {c["time"]: i for i, c in enumerate(candles)}
    return candles, index_by_ts


def _infer_entry_decision(
    strategy,
    config: StrategyConfig,
    candles_raw: list[dict[str, Any]],
    bar_index: int,
) -> tuple[str, str, dict[str, Any]]:
    candles = [
        Candle(
            ts=c["time"],
            o=float(c["o"]),
            h=float(c["h"]),
            l=float(c["l"]),
            c=float(c["c"]),
            volume=int(c["volume"]),
        )
        for c in candles_raw[: bar_index + 1]
    ]
    last_price = float(candles_raw[bar_index]["c"])
    prev_ts = candles_raw[max(bar_index - 1, 0)]["time"]

    flat_decision = strategy.evaluate(
        candles,
        StrategyContext(
            symbol="AUD_USD",
            timeframe=config.timeframe,
            position=PositionState(side=None, units=0, avg_price=0.0, entry_ts=None),
            config=config,
            state=StrategyState(),
            bar_index=bar_index,
            spread_pips=None,
            spread_available=False,
            is_realtime=False,
        ),
    )
    if flat_decision.action in {"ENTER_LONG", "FLIP_LONG"}:
        return "flat", flat_decision.action, flat_decision.metadata

    flip_decision = strategy.evaluate(
        candles,
        StrategyContext(
            symbol="AUD_USD",
            timeframe=config.timeframe,
            position=PositionState(side="SHORT", units=1.0, avg_price=last_price, entry_ts=prev_ts),
            config=config,
            state=StrategyState(),
            bar_index=bar_index,
            spread_pips=None,
            spread_available=False,
            is_realtime=False,
        ),
    )
    return "flip", flip_decision.action, flip_decision.metadata


def main() -> None:
    args = _parse_args()
    trades_csv = _resolve_latest_trades_csv(args.trades_csv)
    summary_path = _summary_from_trades_csv(trades_csv)
    summary = json.loads(summary_path.read_text())
    params = summary["params"]

    config = _build_strategy_config(params)
    strategy = get_settings().strategy_name
    strategy_impl = backtest_run.get_strategy(strategy)

    candles, idx = _load_candles(params)
    long_sl_positions = _load_long_sl_positions(trades_csv)

    enriched: list[dict[str, Any]] = []
    for row in long_sl_positions:
        i = idx.get(row["entry_ts"])
        if i is None or i < 2:
            continue
        mode, action, md = _infer_entry_decision(strategy_impl, config, candles, i)
        diag = md.get("entry_diag", {}) if isinstance(md, dict) else {}
        comp = diag.get("components", {}).get("long", {})
        stoch = md.get("stoch", {}) if isinstance(md, dict) else {}
        ts = _parse_ts(row["entry_ts"])
        enriched.append(
            {
                **row,
                "hour": ts.hour,
                "weekday": ts.weekday(),
                "inferred_mode": mode,
                "inferred_action": action,
                "comp_cross": bool(comp.get("cross")),
                "comp_pullback": bool(comp.get("pullback")),
                "comp_rejoin": bool(comp.get("rejoin")),
                "comp_continuation": bool(comp.get("continuation")),
                "stoch_k": stoch.get("k"),
            }
        )

    print(f"TRADES_CSV: {trades_csv}")
    print(f"SUMMARY: {summary_path}")
    print(f"LONG_SL_POSITIONS: {len(enriched)}")
    print("")

    print("Top Long SL Trades")
    for i, row in enumerate(sorted(enriched, key=lambda x: x["pnl_usd"])[: args.top], 1):
        comps = {
            "cross": row["comp_cross"],
            "pullback": row["comp_pullback"],
            "rejoin": row["comp_rejoin"],
            "cont": row["comp_continuation"],
        }
        print(
            f"{i:02d}. {row['entry_ts']} pnl={row['pnl_usd']:.2f} hold={row['hold_bars']:.1f} "
            f"hour={row['hour']:02d} mode={row['inferred_mode']} action={row['inferred_action']} "
            f"comps={comps} stoch_k={row['stoch_k']}"
        )
    print("")

    sig = defaultdict(lambda: {"n": 0, "pnl": 0.0, "hours": defaultdict(int)})
    for row in enriched:
        key = (
            row["comp_cross"],
            row["comp_pullback"],
            row["comp_rejoin"],
            row["comp_continuation"],
        )
        sig[key]["n"] += 1
        sig[key]["pnl"] += row["pnl_usd"]
        sig[key]["hours"][row["hour"]] += 1

    print("Worst Entry Signatures (LONG SL subset)")
    sig_rows = sorted(sig.items(), key=lambda kv: kv[1]["pnl"])[: args.top]
    for key, val in sig_rows:
        hours = sorted(val["hours"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        hours_str = ",".join(f"{h:02d}({n})" for h, n in hours)
        print(
            f"cross={key[0]} pullback={key[1]} rejoin={key[2]} cont={key[3]} | "
            f"n={val['n']} pnl={val['pnl']:.2f} avg={val['pnl']/val['n']:.2f} top_hours={hours_str}"
        )

    hour_buckets = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for row in enriched:
        hour_buckets[row["hour"]]["n"] += 1
        hour_buckets[row["hour"]]["pnl"] += row["pnl_usd"]
    print("")
    print("Worst Hours For LONG SL")
    for hour, val in sorted(hour_buckets.items(), key=lambda kv: kv[1]["pnl"])[: args.top]:
        print(f"hour={hour:02d} n={val['n']} pnl={val['pnl']:.2f} avg={val['pnl']/val['n']:.2f}")


if __name__ == "__main__":
    main()
