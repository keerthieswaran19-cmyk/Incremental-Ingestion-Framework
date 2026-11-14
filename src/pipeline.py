"""Master pipeline orchestrator coordinating extraction, validation, transformation, and atomic loading."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import DB_PATH, DEFAULT_BATCH_SIZE, DEFAULT_LOOKBACK_HOURS
from src.database import get_connection, transaction_scope
from src.extractor import Extractor
from src.logger import setup_logger
from src.validator import QuarantineRecord, ValidatedRecord, Validator
from src.transformer import TransformedRecord, Transformer
from src.deduplicator import Deduplicator
from src.watermark import WatermarkManager
from src.loader import Loader, LoadMetrics
from src.recovery import RecoveryManager


class PipelineRunner:
    """End-to-end ingestion pipeline runner."""

    def __init__(self, db_path: Optional[Path] = None, batch_size: int = DEFAULT_BATCH_SIZE):
        self.db_path = db_path or DB_PATH
        self.batch_size = batch_size

    @staticmethod
    def generate_run_id() -> str:
        """Generates a sortable, unique run identifier."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:6].upper()
        return f"RUN_{ts}_{uid}"

    def run(
        self,
        source_file: Path,
        entity_name: str = "CUSTOMER",
        force_reprocess: bool = False,
        incremental: bool = True,
        lookback_hours: Optional[int] = DEFAULT_LOOKBACK_HOURS,
    ) -> Dict[str, Any]:
        """
        Executes complete ingestion for a given source file.
        Returns execution summary dictionary.
        """
        run_id = self.generate_run_id()
        logger = setup_logger(run_id=run_id)
        start_time = datetime.now(timezone.utc)
        start_perf = time.perf_counter()

        source_path = Path(source_file)
        logger.info(f"Starting ingestion run {run_id} for file: {source_path.name}")

        conn = get_connection(self.db_path)

        try:
            # Step 1: Recover any stalled runs from prior abnormal crashes
            RecoveryManager.recover_stalled_runs(conn)

            # Step 2: Extract file metadata & verify file registry idempotency
            extractor = Extractor(source_path)
            meta = extractor.get_file_metadata()
            file_hash = meta["file_hash"]

            if not force_reprocess and RecoveryManager.is_file_processed(conn, file_hash):
                logger.warning(f"File {source_path.name} (hash: {file_hash[:8]}...) was already processed. Skipping.")
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO pipeline_runs (
                        run_id, start_time, end_time, status, source_file,
                        input_count, inserted_count, updated_count, deleted_count,
                        quarantined_count, duplicate_count, duration_ms, records_per_sec,
                        error_message
                    ) VALUES (?, ?, ?, 'SKIPPED_ALREADY_PROCESSED', ?, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 'File already processed')
                    """,
                    (run_id, start_time.isoformat(), datetime.now(timezone.utc).isoformat(), source_path.name),
                )
                cursor.close()
                return {
                    "run_id": run_id,
                    "status": "SKIPPED_ALREADY_PROCESSED",
                    "file_hash": file_hash,
                    "message": "File was already ingested. Use force_reprocess=True to rerun.",
                }

            # Step 3: Fetch current high-watermark
            current_watermark = WatermarkManager.get_high_watermark(conn, entity_name)
            logger.info(f"Current high-watermark for {entity_name}: {current_watermark}")

            # Register initial RUNNING state in pipeline_runs
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, start_time, status, source_file, watermark_start
                ) VALUES (?, ?, 'RUNNING', ?, ?);
                """,
                (run_id, start_time.isoformat(), source_path.name, current_watermark),
            )
            cursor.close()

            # Step 4: Stream and validate all records
            validator = Validator(run_id=run_id, source_file=source_path.name)
            raw_event_rows = []
            validated_records: List[ValidatedRecord] = []
            quarantine_records: List[QuarantineRecord] = []
            total_input_count = 0
            watermark_skipped_count = 0

            # Priority 2: Compute lookback window threshold so late-arriving records reach Loader
            watermark_threshold = current_watermark
            if incremental and lookback_hours and lookback_hours > 0 and current_watermark != WatermarkManager.DEFAULT_WATERMARK:
                try:
                    wm_clean = current_watermark.replace("Z", "+00:00")
                    wm_dt = datetime.fromisoformat(wm_clean)
                    watermark_threshold = (wm_dt - timedelta(hours=lookback_hours)).isoformat()
                except Exception:
                    watermark_threshold = current_watermark

            for idx, raw_str, parsed_item in extractor.stream_records():
                total_input_count += 1
                valid_rec, quar_rec = validator.validate_record(raw_str, parsed_item)

                if quar_rec:
                    quarantine_records.append(quar_rec)
                    raw_event_rows.append((
                        quar_rec.quarantine_id,
                        None,
                        source_path.name,
                        raw_str,
                        start_time.isoformat(),
                        run_id,
                    ))
                elif valid_rec:
                    # Incremental filter check (uses lookback window to allow late arrivals through)
                    if incremental and valid_rec.updated_at <= watermark_threshold:
                        watermark_skipped_count += 1
                    else:
                        validated_records.append(valid_rec)

                    event_id = Transformer.generate_event_id(
                        run_id, source_path.name, valid_rec.natural_key, valid_rec.updated_at, valid_rec.operation_type
                    )
                    raw_event_rows.append((
                        event_id,
                        valid_rec.natural_key,
                        source_path.name,
                        raw_str,
                        start_time.isoformat(),
                        run_id,
                    ))

            logger.info(
                f"Streamed {total_input_count} records: {len(validated_records)} clean, "
                f"{len(quarantine_records)} quarantined, {watermark_skipped_count} below watermark."
            )

            # Step 5: Transform clean records
            transformed_records: List[TransformedRecord] = [
                Transformer.transform(rec, run_id=run_id, source_file=source_path.name)
                for rec in validated_records
            ]

            # Step 6: In-batch window deduplication
            deduped_records, in_batch_duplicates = Deduplicator.deduplicate_batch(transformed_records)

            # Step 7: Atomic transactional loading & watermark progression
            new_high_watermark = current_watermark
            if deduped_records:
                max_batch_ts = max(r.updated_at for r in deduped_records)
                if max_batch_ts > current_watermark:
                    new_high_watermark = max_batch_ts

            with transaction_scope(conn) as tx_cursor:
                # A. Persist raw source events
                Loader.load_source_events_batch(tx_cursor, raw_event_rows)

                # B. Persist quarantine records
                Loader.load_quarantine_batch(tx_cursor, quarantine_records)

                # C. Process upserts, tombstones, and SCD-4 event history
                load_metrics = Loader.process_records_batch(
                    tx_cursor,
                    records=deduped_records,
                    run_id=run_id,
                    source_file=source_path.name,
                )

                # Total duplicate count = in-batch duplicates + cross-batch payload duplicates + watermark skipped + late-arriving non-mutations
                total_duplicates = (
                    in_batch_duplicates
                    + load_metrics.duplicate_count
                    + watermark_skipped_count
                    + load_metrics.late_arriving_count
                )

                # D. Advance high watermark if new records were processed
                if new_high_watermark > current_watermark:
                    WatermarkManager.advance_watermark(tx_cursor, entity_name, new_high_watermark, run_id)

                # E. Update file registry
                RecoveryManager.register_file(
                    tx_cursor,
                    file_hash=file_hash,
                    file_path=str(source_path.resolve()),
                    file_size_bytes=meta["file_size_bytes"],
                    record_count=total_input_count,
                    status="PROCESSED",
                    run_id=run_id,
                )

                # F. Finalize pipeline_runs metrics
                duration_ms = (time.perf_counter() - start_perf) * 1000.0
                duration_sec = duration_ms / 1000.0
                rec_per_sec = (total_input_count / duration_sec) if duration_sec > 0 else 0.0
                end_time = datetime.now(timezone.utc)

                tx_cursor.execute(
                    """
                    UPDATE pipeline_runs
                    SET end_time = ?,
                        status = 'SUCCESS',
                        input_count = ?,
                        inserted_count = ?,
                        updated_count = ?,
                        deleted_count = ?,
                        quarantined_count = ?,
                        duplicate_count = ?,
                        duration_ms = ?,
                        records_per_sec = ?,
                        watermark_end = ?
                    WHERE run_id = ?;
                    """,
                    (
                        end_time.isoformat(),
                        total_input_count,
                        load_metrics.inserted_count,
                        load_metrics.updated_count,
                        load_metrics.deleted_count,
                        len(quarantine_records),
                        total_duplicates,
                        round(duration_ms, 2),
                        round(rec_per_sec, 2),
                        new_high_watermark,
                        run_id,
                    ),
                )

            logger.info(
                f"Ingestion run {run_id} completed successfully in {duration_ms:.2f}ms. "
                f"Inserts: {load_metrics.inserted_count}, Updates: {load_metrics.updated_count}, "
                f"Deletes: {load_metrics.deleted_count}, Quarantined: {len(quarantine_records)}, "
                f"Duplicates: {total_duplicates}, Throughput: {rec_per_sec:.1f} rec/s."
            )

            return {
                "run_id": run_id,
                "status": "SUCCESS",
                "source_file": source_path.name,
                "input_count": total_input_count,
                "inserted_count": load_metrics.inserted_count,
                "updated_count": load_metrics.updated_count,
                "deleted_count": load_metrics.deleted_count,
                "quarantined_count": len(quarantine_records),
                "duplicate_count": total_duplicates,
                "late_arriving_count": load_metrics.late_arriving_count,
                "duration_ms": round(duration_ms, 2),
                "records_per_sec": round(rec_per_sec, 2),
                "watermark_start": current_watermark,
                "watermark_end": new_high_watermark,
            }

        except Exception as exc:
            logger.error(f"Ingestion run {run_id} failed with error: {str(exc)}", exc_info=True)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET end_time = ?,
                    status = 'FAILED',
                    error_message = ?
                WHERE run_id = ?;
                """,
                (datetime.now(timezone.utc).isoformat(), str(exc), run_id),
            )
            cursor.close()
            raise exc

        finally:
            conn.close()
