import sqlite3
import sqlite_vec
from pathlib import Path

from .config import DB_PATH, VEC_TABLE_NAME, EMBEDDING_DIM


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            info_code       TEXT PRIMARY KEY,
            title           TEXT,
            org             TEXT,
            stock           TEXT,
            stock_code      TEXT,
            industry        TEXT,
            em_rating       TEXT,
            authors         TEXT,
            report_date     TEXT,
            report_url      TEXT,
            pdf_url         TEXT,
            pdf_path        TEXT,
            json_path       TEXT,
            file_hash       TEXT,
            page_count      INTEGER DEFAULT 0,
            indexed_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS text_chunks (
            chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            info_code   TEXT NOT NULL REFERENCES reports(info_code) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL,
            token_count INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_info ON text_chunks(info_code);

        CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
            title, stock, industry, org,
            content='reports',
            content_rowid='rowid',
            tokenize='unicode61'
        );
    """)

    vec_table = VEC_TABLE_NAME
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table} USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding float[{EMBEDDING_DIM}] distance_metric=cosine
        );
    """)

    _sync_fts_triggers(conn)


def _sync_fts_triggers(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS reports_ai AFTER INSERT ON reports BEGIN
            INSERT INTO reports_fts(rowid, title, stock, industry, org)
            VALUES (new.rowid, new.title, new.stock, new.industry, new.org);
        END;

        CREATE TRIGGER IF NOT EXISTS reports_ad AFTER DELETE ON reports BEGIN
            INSERT INTO reports_fts(reports_fts, rowid, title, stock, industry, org)
            VALUES ('delete', old.rowid, old.title, old.stock, old.industry, old.org);
        END;

        CREATE TRIGGER IF NOT EXISTS reports_au AFTER UPDATE ON reports BEGIN
            INSERT INTO reports_fts(reports_fts, rowid, title, stock, industry, org)
            VALUES ('delete', old.rowid, old.title, old.stock, old.industry, old.org);
            INSERT INTO reports_fts(rowid, title, stock, industry, org)
            VALUES (new.rowid, new.title, new.stock, new.industry, new.org);
        END;
    """)


def get_stats(conn: sqlite3.Connection) -> dict:
    r = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM text_chunks").fetchone()[0]
    stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM reports WHERE stock_code != ''").fetchone()[0]
    industries = conn.execute("SELECT COUNT(DISTINCT industry) FROM reports WHERE industry != ''").fetchone()[0]
    orgs = conn.execute("SELECT COUNT(DISTINCT org) FROM reports WHERE org != ''").fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(report_date), MAX(report_date) FROM reports WHERE report_date != ''"
    ).fetchone()
    return {
        "total_reports": r,
        "total_chunks": c,
        "unique_stocks": stocks,
        "unique_industries": industries,
        "unique_orgs": orgs,
        "date_range": (date_range[0], date_range[1]) if date_range else None,
    }
