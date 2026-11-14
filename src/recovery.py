"""Recovery and file registry module for safe retries, crash recovery, and idempotent reruns."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple


class RecoveryManager:
    """Handles file-level idempotency checks and crash recovery."""

    @staticmethod
    def is_file_processed(conn: sqlite3.Connection, file_hash: str) -> bool:
        """Checks if a file with the identical SHA-256 hash has already been processed."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM file_registry WHERE file_hash = ?",
            (file_hash,),
        )
        row = cursor.fetchone()
        cursor.close()
        return bool(row and row["status"] == "PROCESSED")

    @staticmethod
    def register_file(
        cursor: sqlite3.Cursor,
        file_hash: str,
        file_path: str,
        file_size_bytes: int,
        record_count: int,
        status: str,
        run_id: str,
    ) -> None:
        """Records file status into file_registry."""
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO file_registry (
                file_hash, file_path, file_size_bytes, record_count, status, processed_at, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                status = excluded.status,
                processed_at = excluded.processed_at,
                run_id = excluded.run_id,
                record_count = excluded.record_count;
            """,
            (file_hash, file_path, file_size_bytes, record_count, status, now_iso, run_id),
        )

    @staticmethod
    def recover_stalled_runs(conn: sqlite3.Connection) -> int:
        """
        Marks any abandoned or crashed runs ('RUNNING') as 'FAILED_ABORTED'.
        Returns count of recovered runs.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE pipeline_runs
            SET status = 'FAILED_ABORTED',
                end_time = ?,
                error_message = 'Process terminated abnormally before completion. Recovered on restart.'
            WHERE status = 'RUNNING';
            """,
            (now_iso,),
        )
        count = cursor.rowcount
        cursor.close()
        return count
