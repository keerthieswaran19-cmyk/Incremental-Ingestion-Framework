"""CLI Script to initialize or reset the SQLite database schema."""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import init_database, get_connection
from src.config import DB_PATH


def main():
    parser = argparse.ArgumentParser(description="Initialize SQLite Schema for Incremental Ingestion Framework.")
    parser.add_argument("--db-path", type=str, default=str(DB_PATH), help="Path to SQLite database file.")
    parser.add_argument("--reset", action="store_true", help="Drop existing tables before recreating.")
    args = parser.parse_args()

    db_file = Path(args.db_path)
    if args.reset and db_file.exists():
        print(f"Resetting database at {db_file}...")
        conn = get_connection(db_file)
        tables = ["source_events", "target_records", "event_history", "quarantine", "pipeline_runs", "watermark_state", "file_registry"]
        with conn:
            for t in tables:
                conn.execute(f"DROP TABLE IF EXISTS {t};")
        conn.close()
        print("Existing tables dropped.")

    print(f"Initializing database at: {db_file}")
    init_database(db_file)
    print("Database schema successfully created with all indexes and constraints.")


if __name__ == "__main__":
    main()
