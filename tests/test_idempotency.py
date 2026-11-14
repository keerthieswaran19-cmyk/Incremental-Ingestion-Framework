"""Tests for idempotency, same-file reruns, and duplicate handling."""

import sqlite3


def test_same_file_rerun_skipped(runner, create_test_file, temp_db_path):
    """Verifies that executing the exact same file twice is skipped via file registry."""
    records = [
        {"natural_key": "CUST_IDEM_1", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 100.0}
    ]
    file_path = create_test_file(records, "idem_batch.json")

    run1 = runner.run(file_path)
    assert run1["status"] == "SUCCESS"
    assert run1["inserted_count"] == 1

    run2 = runner.run(file_path)
    assert run2["status"] == "SKIPPED_ALREADY_PROCESSED"

    conn = sqlite3.connect(str(temp_db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM target_records")
    assert cursor.fetchone()[0] == 1
    conn.close()


def test_forced_rerun_zero_target_duplicates(runner, create_test_file, temp_db_path):
    """Verifies that forcing a rerun of the same file does not create duplicate rows."""
    records = [
        {"natural_key": "CUST_IDEM_A", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 100.0},
        {"natural_key": "CUST_IDEM_B", "updated_at": "2026-11-01T10:05:00Z", "operation_type": "INSERT", "amount": 200.0},
    ]
    file_path = create_test_file(records, "idem_forced.json")

    run1 = runner.run(file_path)
    assert run1["inserted_count"] == 2

    # Forced rerun
    run2 = runner.run(file_path, force_reprocess=True)
    assert run2["status"] == "SUCCESS"
    assert run2["inserted_count"] == 0
    assert run2["duplicate_count"] == 2

    conn = sqlite3.connect(str(temp_db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM target_records")
    assert cursor.fetchone()[0] == 2  # Still exactly 2 rows
    conn.close()


def test_in_batch_duplicates(runner, create_test_file, temp_db_path):
    """Verifies in-batch multiple events for same natural key resolve to latest without duplicate rows."""
    records = [
        {"natural_key": "CUST_MULTI", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 10.0},
        {"natural_key": "CUST_MULTI", "updated_at": "2026-11-01T10:30:00Z", "operation_type": "INSERT", "amount": 25.0},
    ]
    file_path = create_test_file(records, "in_batch_dup.json")
    res = runner.run(file_path)

    assert res["status"] == "SUCCESS"
    assert res["inserted_count"] == 1
    assert res["duplicate_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT payload FROM target_records WHERE natural_key = 'CUST_MULTI'")
    row = cursor.fetchone()
    assert '"amount": 25.0' in row["payload"]
    conn.close()
