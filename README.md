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
