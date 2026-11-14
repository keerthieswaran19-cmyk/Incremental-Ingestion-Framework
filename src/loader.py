"""Loader module for atomic upserts, tombstones, SCD-4 event history, and quarantine storage."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.transformer import TransformedRecord
from src.validator import QuarantineRecord


class LoadMetrics:
    """Tracks counts for a batch load."""

    def __init__(self):
        self.inserted_count: int = 0
        self.updated_count: int = 0
        self.deleted_count: int = 0
        self.quarantined_count: int = 0
        self.duplicate_count: int = 0
        self.late_arriving_count: int = 0


class Loader:
    """Executes transaction-safe database publishing."""

    @staticmethod
    def _generate_history_id(entity_id: str, timestamp: str, op_type: str, run_id: str) -> str:
        raw = f"{entity_id}:{timestamp}:{op_type}:{run_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def load_quarantine_batch(
        cls,
        cursor: sqlite3.Cursor,
        quarantine_records: List[QuarantineRecord],
    ) -> int:
        """Persists rejected records to the quarantine table."""
        if not quarantine_records:
            return 0

        insert_sql = """
        INSERT INTO quarantine (
            quarantine_id, source_file, raw_payload, error_code, error_message, replayed, run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(quarantine_id) DO NOTHING;
        """
        params = [
            (
                r.quarantine_id,
                r.source_file,
                r.raw_payload,
                r.error_code,
                r.error_message,
                r.run_id,
                r.created_at,
            )
            for r in quarantine_records
        ]
        cursor.executemany(insert_sql, params)
        return len(quarantine_records)

    @classmethod
    def load_source_events_batch(
        cls,
        cursor: sqlite3.Cursor,
        records: List[Tuple[str, Optional[str], str, str, str, str]],
    ) -> None:
        """
        Persists raw incoming events to source_events.
        records tuple: (event_id, entity_id, source_file, raw_payload, received_at, run_id)
        """
        if not records:
            return

        insert_sql = """
        INSERT INTO source_events (
            event_id, entity_id, source_file, raw_payload, received_at, run_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO NOTHING;
        """
        cursor.executemany(insert_sql, records)

    @classmethod
    def process_records_batch(
        cls,
        cursor: sqlite3.Cursor,
        records: List[TransformedRecord],
        run_id: str,
        source_file: str,
    ) -> LoadMetrics:
        """
        Performs atomic upserts, late-arrival protections, tombstones, and SCD-4 history updates.
        """
        metrics = LoadMetrics()
        if not records:
            return metrics

        now_iso = datetime.now(timezone.utc).isoformat()

        # Fetch existing target state for natural keys in this batch in chunks to respect SQLite variable limits
        natural_keys = list(set(r.natural_key for r in records))
        existing_targets: Dict[str, sqlite3.Row] = {}
        chunk_size = 900
        for i in range(0, len(natural_keys), chunk_size):
            chunk = natural_keys[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"""
                SELECT entity_id, natural_key, payload, record_hash, version, is_deleted, last_updated_at
                FROM target_records
                WHERE natural_key IN ({placeholders})
                """,
                chunk,
            )
            for row in cursor.fetchall():
                existing_targets[row["natural_key"]] = row

        insert_rows = []
        update_rows = []
        delete_rows = []
        history_rows = []

        for rec in records:
            payload_str = rec.payload_json()
            is_delete_op = rec.operation_type in ["DELETE", "TOMBSTONE"]

            if rec.natural_key in existing_targets:
                target = existing_targets[rec.natural_key]
                target_last_updated = target["last_updated_at"]
                target_hash = target["record_hash"]

                # Check temporal monotonicity (late-arriving / out-of-order check)
                if rec.updated_at < target_last_updated:
                    # Late arrival! Do NOT overwrite target_records.
                    # Preserve in event_history only with is_out_of_order = 1.
                    metrics.late_arriving_count += 1
                    history_id = cls._generate_history_id(rec.entity_id, rec.updated_at, "LATE_ARRIVAL", run_id)
                    history_rows.append((
                        history_id,
                        rec.entity_id,
                        rec.natural_key,
                        "LATE_ARRIVAL",
                        payload_str,
                        rec.record_hash,
                        rec.updated_at,
                        1,  # is_out_of_order
                        source_file,
                        run_id,
                        now_iso,
                    ))
                    continue

                # Same or newer timestamp: Check for identical payload hash (Deduplication)
                if rec.record_hash == target_hash and not is_delete_op and target["is_deleted"] == 0:
                    metrics.duplicate_count += 1
                    continue

                # State change (Update or Delete)
                if is_delete_op:
                    metrics.deleted_count += 1
                    delete_rows.append((source_file, rec.updated_at, run_id, rec.entity_id))
                    history_id = cls._generate_history_id(rec.entity_id, rec.updated_at, "DELETE", run_id)
                    history_rows.append((
                        history_id,
                        rec.entity_id,
                        rec.natural_key,
                        "DELETE",
                        payload_str,
                        rec.record_hash,
                        rec.updated_at,
                        0,
                        source_file,
                        run_id,
                        now_iso,
                    ))
                else:
                    metrics.updated_count += 1
                    update_rows.append((payload_str, rec.record_hash, source_file, rec.updated_at, run_id, rec.entity_id))
                    history_id = cls._generate_history_id(rec.entity_id, rec.updated_at, "UPDATE", run_id)
                    history_rows.append((
                        history_id,
                        rec.entity_id,
                        rec.natural_key,
                        "UPDATE",
                        payload_str,
                        rec.record_hash,
                        rec.updated_at,
                        0,
                        source_file,
                        run_id,
                        now_iso,
                    ))

            else:
                # Brand new record (Insert or Initial Tombstone)
                if is_delete_op:
                    metrics.deleted_count += 1
                    insert_rows.append((
                        rec.entity_id,
                        rec.natural_key,
                        rec.entity_type,
                        payload_str,
                        rec.record_hash,
                        1,
                        1,
                        source_file,
                        rec.updated_at,
                        rec.updated_at,
                        run_id,
                    ))
                    op_type = "DELETE"
                else:
                    metrics.inserted_count += 1
                    insert_rows.append((
                        rec.entity_id,
                        rec.natural_key,
                        rec.entity_type,
                        payload_str,
                        rec.record_hash,
                        1,
                        0,
                        source_file,
                        rec.updated_at,
                        rec.updated_at,
                        run_id,
                    ))
                    op_type = "INSERT"

                history_id = cls._generate_history_id(rec.entity_id, rec.updated_at, op_type, run_id)
                history_rows.append((
                    history_id,
                    rec.entity_id,
                    rec.natural_key,
                    op_type,
                    payload_str,
                    rec.record_hash,
                    rec.updated_at,
                    0,
                    source_file,
                    run_id,
                    now_iso,
                ))

        if insert_rows:
            cursor.executemany(
                """
                INSERT INTO target_records (
                    entity_id, natural_key, entity_type, payload, record_hash,
                    version, is_deleted, source_file, first_seen_at, last_updated_at, last_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                insert_rows,
            )

        if update_rows:
            cursor.executemany(
                """
                UPDATE target_records
                SET payload = ?,
                    record_hash = ?,
                    version = version + 1,
                    is_deleted = 0,
                    source_file = ?,
                    last_updated_at = ?,
                    last_run_id = ?
                WHERE entity_id = ?;
                """,
                update_rows,
            )

        if delete_rows:
            cursor.executemany(
                """
                UPDATE target_records
                SET is_deleted = 1,
                    version = version + 1,
                    source_file = ?,
                    last_updated_at = ?,
                    last_run_id = ?
                WHERE entity_id = ?;
                """,
                delete_rows,
            )

        # Bulk write event_history entries
        if history_rows:
            cursor.executemany(
                """
                INSERT INTO event_history (
                    history_id, entity_id, natural_key, operation_type, payload,
                    record_hash, event_timestamp, is_out_of_order, source_file, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                history_rows,
            )

        return metrics
