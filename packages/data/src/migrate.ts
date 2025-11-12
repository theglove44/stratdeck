import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';

const DB_PATH = process.env.STRATDECK_DB || 'stratdeck.db';
const schemaPath = path.resolve(process.cwd(), 'packages/data/src/schema.sql');
const seedPath = path.resolve(process.cwd(), 'packages/data/src/seed.sql');

function runSQL(db: Database.Database, sql: string) {
  const statements = sql.split(/;\s*\n/).map(s => s.trim()).filter(Boolean);
  db.exec('BEGIN');
  try {
    for (const st of statements) db.exec(st + ';');
    db.exec('COMMIT');
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

function main() {
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  const schema = fs.readFileSync(schemaPath, 'utf8');
  runSQL(db, schema);
  const seed = fs.readFileSync(seedPath, 'utf8');
  runSQL(db, seed);
  console.log('Migration complete. DB:', DB_PATH);
}

main();
