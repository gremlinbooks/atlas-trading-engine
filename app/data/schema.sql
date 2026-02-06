CREATE TABLE IF NOT EXISTS trade_intents (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    symbol TEXT,
    side TEXT,
    units REAL,
    status TEXT,
    idempotency_key TEXT UNIQUE,
    reason TEXT,
    oanda_order_id TEXT,
    oanda_trade_id TEXT,
    requested_json TEXT,
    response_json TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    side TEXT,
    units REAL,
    avg_price REAL,
    updated_at TEXT,
    oanda_trade_id TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    balance REAL,
    nav REAL,
    margin_used REAL,
    unrealized_pl REAL,
    open_trades_json TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    symbol TEXT,
    state TEXT,
    spread_pips REAL,
    candle_ts TEXT,
    signal TEXT,
    reason TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS candle_cursor (
    symbol TEXT PRIMARY KEY,
    last_candle_ts TEXT,
    updated_at TEXT
);
