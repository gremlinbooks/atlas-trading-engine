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

- `DRY_RUN=true`: records trade intent only, no broker calls
- `DRY_RUN=false`: executes via OANDA and enforces idempotency

## Windows Notes

- Use `python -m venv .venv` and activate with `.venv\\Scripts\\activate`.
- Run uvicorn the same way after activating the venv.
