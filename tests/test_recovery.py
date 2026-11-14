"""Tests for transaction rollback, partial failure recovery, and stalled run cleanup."""

import sqlite3
import pytest
from unittest.mock import patch

from src.loader import Loader
from src.recovery import RecoveryManager


def test_atomic_rollback_on_failure(runner, create_test_file, temp_db_path):
    """
    Verifies that if an unexpected exception occurs during loading:
    - Entire batch rolls back.
    - Zero rows remain in target_records.
    - Watermark remains at default epoch.
    - Run is recorded as FAILED.
    """
    records = [
        {"natural_key": "CUST_FAIL_1", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 100.0},
        {"natural_key": "CUST_FAIL_2", "updated_at": "2026-11-01T10:05:00Z", "operation_type": "INSERT", "amount": 200.0},
    ]
    file_path = create_test_file(records, "fail_batch.json")

    # Simulate crash inside process_records_batch
    with patch.object(Loader, "process_records_batch", side_effect=RuntimeError("Simulated Database Disk I/O Crash")):
        with pytest.raises(RuntimeError, match="Simulated Database Disk I/O Crash"):
            runner.run(file_path)

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Target records must be 0 (clean rollback)
    cursor.execute("SELECT COUNT(*) as cnt FROM target_records")
    assert cursor.fetchone()["cnt"] == 0

    # Watermark must not have advanced
    cursor.execute("SELECT high_watermark FROM watermark_state WHERE entity_name = 'CUSTOMER'")
    assert cursor.fetchone() is None

    # Pipeline run must be marked FAILED
    cursor.execute("SELECT status, error_message FROM pipeline_runs")
    run = cursor.fetchone()
    assert run["status"] == "FAILED"
    assert "Simulated Database Disk I/O Crash" in run["error_message"]

    conn.close()

    # Safe retry: Rerun without the error
    retry_result = runner.run(file_path, force_reprocess=True)
    assert retry_result["status"] == "SUCCESS"
    assert retry_result["inserted_count"] == 2


def test_stalled_run_recovery(temp_db_path):
    """Verifies that abandoned 'RUNNING' runs from ungraceful crashes are marked FAILED_ABORTED on startup."""
    conn = sqlite3.connect(str(temp_db_path))
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pipeline_runs (run_id, start_time, status, source_file)
        VALUES ('RUN_ORPHAN_999', '2026-11-01T00:00:00Z', 'RUNNING', 'orphan.json');
        """
    )
    conn.commit()

    recovered_cnt = RecoveryManager.recover_stalled_runs(conn)
    assert recovered_cnt == 1

    cursor.execute("SELECT status, error_message FROM pipeline_runs WHERE run_id = 'RUN_ORPHAN_999'")
    row = cursor.fetchone()
    assert row[0] == "FAILED_ABORTED"
    assert "Recovered on restart" in row[1]
    conn.close()
