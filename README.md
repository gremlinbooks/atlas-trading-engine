# Atlas Trading Engine (Phase 1)

Deterministic, restart-safe core for a trading engine with a SQLite ledger, OANDA reconciliation, explicit state machine, structured logging, and manual execution endpoint.

## Linux Setup

1. Create and activate a venv

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Configure environment

```bash
cp .env.example .env
```

Update `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, and `OANDA_ENV`.

Phase 2 candle settings (defaults shown in `.env.example`):
- `TIMEFRAME` (default `M15`)
- `CANDLE_COUNT` (default `200`)
- `CANDLE_POLL_SECONDS` (default `30`)
- `DEFAULT_UNITS` (default `1000`)
- `STRATEGY_NAME` (default `oakbridge_fxtrader_v2`)
- `STRATEGY_ENABLED` (default `false`)
- `STRATEGY_MIN_HOLD_BARS` (default `3`)
- `STRATEGY_TREND_EMA_PERIOD` (default `50`)

Strategy parameters (OakBridge v2 defaults shown in `.env.example`):
- `STRATEGY_FAST_LEN`
- `STRATEGY_SLOW_LEN`
- `STRATEGY_USE_BIAS`
- `STRATEGY_INVERT_EURCAD`
- `STRATEGY_FORCE_FLIP`
- `STRATEGY_TP1_PIPS`
- `STRATEGY_SL_PIPS`
- `STRATEGY_TP1_CLOSE_PCT`
- `STRATEGY_TRAIL_DRAWDOWN_PCT`
- `STRATEGY_BE_LOCK_PIPS`
- `STRATEGY_STOCH_ENTRY_MODE` (`Off`, `ExtremesOnly`, `StrictFilter`)
- `STRATEGY_USE_STOCH_EXIT`
- `STRATEGY_ST_RSI_LEN`
- `STRATEGY_ST_STOCH_LEN`
- `STRATEGY_ST_K_LEN`
- `STRATEGY_ST_D_LEN`
- `STRATEGY_ST_OB`
- `STRATEGY_ST_OS`
- `STRATEGY_ST_RECENT`
- `STRATEGY_ST_TIGHT_PIPS`
- `STRATEGY_BLOCK_TRADES`
- `STRATEGY_BLOCK_SESSION` (UTC `HHMM-HHMM`)
- `STRATEGY_QUICK_RELAX`
- `STRATEGY_USE_DAY_MASK`
- `STRATEGY_BLOCK_MON` through `STRATEGY_BLOCK_SUN`
- `STRATEGY_USE_SPREAD_GATE`
- `STRATEGY_MAX_SPREAD_PIPS`
- `STRATEGY_AGGR_SPREAD_FACTOR`
- `STRATEGY_HOLD_SIGNAL_BARS`
- `STRATEGY_APPLY_ON_HISTORY`
- `STRATEGY_PB_ENABLED`
- `STRATEGY_PB_LOOKBACK_BARS`
- `STRATEGY_CONT_ENABLED`
- `STRATEGY_BASE_MAX_BARS`
- `STRATEGY_BASE_MAX_RANGE_ATR`
- `STRATEGY_ALLOW_SECOND_CHANCE`
- `STRATEGY_REENTER_WITHIN_BARS`

## Running Locally

```bash
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The engine initializes the SQLite ledger at `data/trades.db` and starts background loops for evaluation and reconciliation.

## Testing Endpoints

Health:

```bash
curl http://localhost:8000/api/v1/health
```

Manual execution:

```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AUD_USD","side":"LONG","units":1000,"idempotency_key":"demo-1"}'
```

## DRY_RUN Usage

- `DRY_RUN=true`: no order execution; decisions and snapshots still write to the DB and logs
- `DRY_RUN=false`: executes via OANDA and enforces idempotency

## Windows Notes

- Use `python -m venv .venv` and activate with `.venv\\Scripts\\activate`.
- Run uvicorn the same way after activating the venv.

## How To Run Backtests

Backtests use OANDA candles and the current strategy logic. Example:

```bash
python -m app.backtest.run --symbol AUD_USD --timeframe M15 --days 90 --units 1000 --spread_pips 1.2
```

If `STRATEGY_ENABLED=false`, the strategy will hold. Set `STRATEGY_ENABLED=true` for active signals.

Optional fill model:
- `--fill=next_open` (default)
- `--fill=close`

Notes:
- Backtests assume mid-price candles with a configurable spread cost via `--spread_pips`.
- TP/SL fills are simulated at the level using candle high/low.

## How To Run TradingView-Parity Backtests

Use the TradingView parity flags to match TP1 + runner management:

```bash
python -m app.backtest.run \
  --symbol AUD_USD \
  --timeframe M15 \
  --days 10 \
  --units 1000 \
  --spread_pips 2.2 \
  --tp1_pips 20 \
  --sl_pips 28 \
  --tp1_close_pct 30 \
  --trail_drawdown_pct 2.0 \
  --be_lock_pips 20 \
  --bar_fill_policy conservative \
  --use_runner true
```

Notes:
- `--bar_fill_policy=conservative` assumes SL hits before TP1 if both are touched in the same candle.
- Spread is modeled as half-spread on entry and exit.
- TP1 closes `tp1_close_pct` of units and the runner uses trailing + BE after TP1.

## Porting TradingView Strategies

Strategy interfaces live in `app/engine/strategy_base.py`. The default placeholder is:
- `app/engine/strategies/tv_port_v1.py`

Paste translated Pine logic into `OakBridgeFxTraderV2.evaluate()`. The strategy receives:
- full candle history (oldest to newest)
- current `PositionState`
- `StrategyConfig` (trend filter and min-hold)

The primary TradingView port lives in:
- `app/engine/strategies/oakbridge_fxtrader_v2.py`

Run backtests using the same strategy selection as live:

```bash
python -m app.backtest.run --symbol AUD_USD --timeframe M15 --days 90 --units 1000 --spread_pips 1.2
```
