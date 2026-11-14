"""Recovery and file registry module for safe retries, crash recovery, and idempotent reruns."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


class RecoveryManager:
    """Handles file-level idempotency checks, crash recovery, and DLQ replay."""

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

    @staticmethod
    def replay_quarantine_record(
        conn: sqlite3.Connection,
        quarantine_id: str,
        corrected_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Safely and idempotently replays a quarantined record.
        - Validates the payload (corrected_payload if provided, otherwise raw_payload).
        - If valid, upserts into target_records within an ACID transaction.
        - Sets quarantine.replayed = 1 and logs execution to pipeline_runs.
        - Idempotent: repeated calls on replayed records return ALREADY_REPLAYED.
        """
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quarantine WHERE quarantine_id = ?", (quarantine_id,))
        quar_row = cursor.fetchone()
        cursor.close()

        if not quar_row:
            return {"status": "NOT_FOUND", "message": f"Quarantine record '{quarantine_id}' not found."}

        if quar_row["replayed"] == 1:
            return {
                "status": "ALREADY_REPLAYED",
                "message": f"Quarantine record '{quarantine_id}' was already successfully replayed.",
                "quarantine_id": quarantine_id,
            }

        # Determine payload
        raw_str = quar_row["raw_payload"]
        if corrected_payload is not None:
            payload_data = corrected_payload
            raw_str = json.dumps(corrected_payload)
        else:
            try:
                payload_data = json.loads(raw_str)
            except Exception as e:
                return {
                    "status": "VALIDATION_FAILED",
                    "error_code": "MALFORMED_JSON",
                    "error_message": f"Cannot replay without corrected_payload: {e}",
                }

        # Generate replay run ID
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        replay_run_id = f"RUN_REPLAY_{ts}_{uuid.uuid4().hex[:6].upper()}"

        # Validate
        from src.validator import Validator
        validator = Validator(run_id=replay_run_id, source_file=quar_row["source_file"])
        valid_rec, new_quar = validator.validate_record(raw_str, payload_data)

        if new_quar:
            return {
                "status": "VALIDATION_FAILED",
                "error_code": new_quar.error_code,
                "error_message": new_quar.error_message,
                "quarantine_id": quarantine_id,
            }

        # Execute safe atomic publish
        from src.database import transaction_scope
        from src.transformer import Transformer
        from src.loader import Loader
        from src.watermark import WatermarkManager

        transformed = Transformer.transform(valid_rec, run_id=replay_run_id, source_file=quar_row["source_file"])
        start_time = datetime.now(timezone.utc).isoformat()

        with transaction_scope(conn) as tx_cursor:
            # 1. Process into target and history
            load_metrics = Loader.process_records_batch(
                tx_cursor,
                records=[transformed],
                run_id=replay_run_id,
                source_file=quar_row["source_file"],
            )

            # 2. Advance watermark if newer
            curr_wm = WatermarkManager.get_high_watermark(conn, valid_rec.entity_type)
            if valid_rec.updated_at > curr_wm:
                WatermarkManager.advance_watermark(tx_cursor, valid_rec.entity_type, valid_rec.updated_at, replay_run_id)

            # 3. Mark quarantine record as replayed
            tx_cursor.execute(
                "UPDATE quarantine SET replayed = 1, run_id = ? WHERE quarantine_id = ?",
                (replay_run_id, quarantine_id),
            )

            # 4. Record execution in pipeline_runs
            end_time = datetime.now(timezone.utc).isoformat()
            tx_cursor.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, start_time, end_time, status, source_file,
                    input_count, inserted_count, updated_count, deleted_count,
                    quarantined_count, duplicate_count, duration_ms, records_per_sec
                ) VALUES (?, ?, ?, 'SUCCESS', ?, 1, ?, ?, ?, 0, ?, 1.0, 1000.0);
                """,
                (
                    replay_run_id,
                    start_time,
                    end_time,
                    quar_row["source_file"],
                    load_metrics.inserted_count,
                    load_metrics.updated_count,
                    load_metrics.deleted_count,
                    load_metrics.duplicate_count,
                ),
            )

        return {
            "status": "SUCCESS",
            "message": f"Quarantine record '{quarantine_id}' replayed successfully.",
            "quarantine_id": quarantine_id,
            "replay_run_id": replay_run_id,
            "natural_key": valid_rec.natural_key,
            "inserted_count": load_metrics.inserted_count,
            "updated_count": load_metrics.updated_count,
            "deleted_count": load_metrics.deleted_count,
        }
