-- strategies lifecycle
CREATE TABLE IF NOT EXISTS strategies (
  id TEXT PRIMARY KEY,
  underlying TEXT NOT NULL,
  strategy_type TEXT NOT NULL,  -- 'IC','CCS','PCS','IFly','Strangle'
  status TEXT NOT NULL,         -- 'OPEN','CLOSED','ADJUSTED'
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  notes TEXT
);

-- legs
CREATE TABLE IF NOT EXISTS legs (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id),
  side TEXT NOT NULL,           -- 'SHORT' | 'LONG'
  call_put TEXT NOT NULL,       -- 'CALL' | 'PUT'
  strike REAL NOT NULL,
  expiration TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_price REAL NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT
);

-- fills
CREATE TABLE IF NOT EXISTS fills (
  id TEXT PRIMARY KEY,
  leg_id TEXT NOT NULL REFERENCES legs(id),
  ts TEXT NOT NULL,
  action TEXT NOT NULL,         -- 'BUY','SELL'
  price REAL NOT NULL,
  qty INTEGER NOT NULL,
  fees REAL DEFAULT 0
);

-- journal mem (text only for v0.1)
CREATE TABLE IF NOT EXISTS mem_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT,
  tag TEXT,
  content TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS mem_chunks_strategy_idx ON mem_chunks(strategy_id);
