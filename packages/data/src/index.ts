import Database from 'better-sqlite3';

const DB_PATH = process.env.STRATDECK_DB || 'stratdeck.db';

let db: Database.Database | null = null;

function getDB() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
  }
  return db;
}

export type PositionRow = {
  strategy_id: string;
  underlying: string;
  strategy_type: string;
  status: string;
  expiration: string;
  credit_received: number | null;
};

export async function listPositions(): Promise<PositionRow[]> {
  const d = getDB();
  const sql = `
    SELECT
      s.id AS strategy_id,
      s.underlying,
      s.strategy_type,
      s.status,
      (SELECT MAX(expiration) FROM legs l WHERE l.strategy_id = s.id) AS expiration,
      ROUND(COALESCE((SELECT SUM(avg_price) FROM legs l WHERE l.strategy_id = s.id), 0), 2) AS credit_received
    FROM strategies s
    ORDER BY s.opened_at DESC
  `;
  const rows = d.prepare(sql).all() as PositionRow[];
  return rows;
}

export async function addJournalNote(strategyId: string, content: string, tag = 'note'): Promise<void> {
  const d = getDB();
  const ins = d.prepare(`INSERT INTO mem_chunks (strategy_id, tag, content) VALUES (?, ?, ?)`);
  ins.run(strategyId, tag, content);
}

export type JournalEntryRow = {
  id: number;
  strategy_id: string | null;
  tag: string | null;
  content: string;
  created_at: string;
};

export async function listJournalNotes(limit = 10): Promise<JournalEntryRow[]> {
  const d = getDB();
  const stmt = d.prepare(`
    SELECT id, strategy_id, tag, content, created_at
    FROM mem_chunks
    ORDER BY created_at DESC
    LIMIT ?
  `);
  return stmt.all(limit) as JournalEntryRow[];
}

export type SpreadRow = {
  strategy_id: string;
  underlying: string;
  strategy_type: string;
  status: string;
  expiration: string;
  credit_received: number;
  width: number | null;
  legs: SpreadLegRow[];
};

export type SpreadLegRow = {
  id: string;
  side: 'SHORT' | 'LONG';
  call_put: 'CALL' | 'PUT';
  strike: number;
  expiration: string;
  qty: number;
};

export async function listSpreads(typeFilter: 'PCS' | 'CCS' | 'IC'): Promise<SpreadRow[]> {
  const d = getDB();
  const sql = `
    WITH exp AS (
      SELECT strategy_id, MAX(expiration) AS expiration
      FROM legs
      GROUP BY strategy_id
    ),
    credits AS (
      SELECT strategy_id, ROUND(COALESCE(SUM(avg_price),0), 2) AS credit
      FROM legs
      GROUP BY strategy_id
    ),
    widths AS (
      SELECT
        s.id AS strategy_id,
        CASE
          WHEN s.strategy_type IN ('PCS','CCS') THEN (
            SELECT ABS(
              (SELECT strike FROM legs WHERE strategy_id = s.id AND side='SHORT' LIMIT 1) -
              (SELECT strike FROM legs WHERE strategy_id = s.id AND side='LONG'  LIMIT 1)
            )
          )
          WHEN s.strategy_type = 'IC' THEN (
            (
              (SELECT ABS(
                (SELECT strike FROM legs WHERE strategy_id = s.id AND call_put='CALL' AND side='SHORT' LIMIT 1) -
                (SELECT strike FROM legs WHERE strategy_id = s.id AND call_put='CALL' AND side='LONG'  LIMIT 1)
              ))
              +
              (SELECT ABS(
                (SELECT strike FROM legs WHERE strategy_id = s.id AND call_put='PUT' AND side='SHORT' LIMIT 1) -
                (SELECT strike FROM legs WHERE strategy_id = s.id AND call_put='PUT' AND side='LONG'  LIMIT 1)
              ))
            ) / 2.0
          )
          ELSE NULL
        END AS width
      FROM strategies s
    )
    SELECT
      s.id AS strategy_id,
      s.underlying,
      s.strategy_type,
      s.status,
      exp.expiration AS expiration,
      credits.credit AS credit_received,
      widths.width AS width
    FROM strategies s
    JOIN exp ON exp.strategy_id = s.id
    JOIN credits ON credits.strategy_id = s.id
    JOIN widths ON widths.strategy_id = s.id
    WHERE s.strategy_type = ?
    ORDER BY s.opened_at DESC
  `;
  const rows = d.prepare(sql).all(typeFilter) as Omit<SpreadRow, 'legs'>[];
  const legsStmt = d.prepare<[string]>(
    `SELECT id, side, call_put, strike, expiration, qty FROM legs WHERE strategy_id = ? ORDER BY CASE WHEN side = 'SHORT' THEN 0 ELSE 1 END, call_put`
  );
  return rows.map((row) => ({
    ...row,
    legs: legsStmt.all(row.strategy_id) as SpreadLegRow[],
  }));
}
