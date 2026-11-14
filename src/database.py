"""Database management and transaction module using built-in SQLite3 with WAL mode."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Union
import logging

from src.config import DB_PATH

logger = logging.getLogger(__name__)

DDL_STATEMENTS = """
-- 1. Source Events: Raw immutable event log
CREATE TABLE IF NOT EXISTS source_events (
    event_id TEXT PRIMARY KEY,
    entity_id TEXT,
    source_file TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    received_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_events_run_id ON source_events(run_id);
CREATE INDEX IF NOT EXISTS idx_source_events_received_at ON source_events(received_at);

-- 2. Target Records: Active current state snapshot (SCD-4 active state)
CREATE TABLE IF NOT EXISTS target_records (
    entity_id TEXT PRIMARY KEY,
    natural_key TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    source_file TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_records_natural_key ON target_records(natural_key);
CREATE INDEX IF NOT EXISTS idx_target_records_last_updated ON target_records(last_updated_at);
CREATE INDEX IF NOT EXISTS idx_target_records_deleted ON target_records(is_deleted);

-- 3. Event History: Append-only audit ledger (SCD-4 CDC log)
CREATE TABLE IF NOT EXISTS event_history (
    history_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    is_out_of_order INTEGER NOT NULL DEFAULT 0,
    source_file TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES target_records(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_event_history_entity_id ON event_history(entity_id);
CREATE INDEX IF NOT EXISTS idx_event_history_event_time ON event_history(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_event_history_run_id ON event_history(run_id);

-- 4. Quarantine: Dead-Letter Queue (DLQ) for corrupt / invalid records
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    replayed INTEGER NOT NULL DEFAULT 0,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quarantine_error_code ON quarantine(error_code);
CREATE INDEX IF NOT EXISTS idx_quarantine_run_id ON quarantine(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_replayed ON quarantine(replayed);

-- 5. Pipeline Runs: Operational run audit and performance metrics
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL,
    source_file TEXT,
    input_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    quarantined_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    records_per_sec REAL,
    watermark_start TEXT,
    watermark_end TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_start_time ON pipeline_runs(start_time);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);

-- 6. Watermark State: High-watermark tracking per entity
CREATE TABLE IF NOT EXISTS watermark_state (
    entity_name TEXT PRIMARY KEY,
    high_watermark TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL
);

-- 7. File Registry: File-level checksum and idempotency tracking
CREATE TABLE IF NOT EXISTS file_registry (
    file_hash TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_registry_path ON file_registry(file_path);
"""


def get_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Creates a high-performance SQLite connection configured with WAL mode."""
    target_path = Path(db_path or DB_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(target_path),
        timeout=30.0,
        isolation_level=None,  # Autocommit off, managed via explicit transactions
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row

    # Performance pragmas
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    cursor.execute("PRAGMA temp_store = MEMORY;")
    cursor.execute("PRAGMA cache_size = -64000;")  # 64 MB
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.close()

    return conn


def init_database(db_path: Optional[Union[str, Path]] = None) -> None:
    """Executes DDL statements to set up the database schema."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(DDL_STATEMENTS)
        logger.info("Database schema initialized successfully.")
    finally:
        conn.close()


@contextmanager
def transaction_scope(conn: sqlite3.Connection) -> Generator[sqlite3.Cursor, None, None]:
    """Context manager for ACID transaction boundaries (BEGIN IMMEDIATE / COMMIT / ROLLBACK)."""
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE;")
    try:
        yield cursor
        cursor.execute("COMMIT;")
    except Exception as exc:
        cursor.execute("ROLLBACK;")
        logger.error(f"Transaction aborted and rolled back due to error: {exc}")
        raise exc
    finally:
        cursor.close()
