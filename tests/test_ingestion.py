"""Tests for happy path and baseline incremental ingestion."""

import sqlite3
from pathlib import Path


def test_happy_path_ingestion(runner, create_test_file, temp_db_path):
    """Verifies standard clean records are ingested into target and history."""
    records = [
        {
            "natural_key": "CUST_001",
            "entity_type": "CUSTOMER",
            "name": "Alice Smith",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "account_id": "ACC-1001",
            "amount": 150.0,
        },
        {
            "natural_key": "CUST_002",
            "entity_type": "CUSTOMER",
            "name": "Bob Jones",
            "updated_at": "2026-11-01T10:05:00Z",
            "operation_type": "INSERT",
            "account_id": "ACC-1002",
            "amount": 200.0,
        },
    ]
    file_path = create_test_file(records, "batch1.json")
    result = runner.run(file_path)

    assert result["status"] == "SUCCESS"
    assert result["input_count"] == 2
    assert result["inserted_count"] == 2
    assert result["quarantined_count"] == 0
    assert result["watermark_end"] == "2026-11-01T10:05:00+00:00"

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM target_records WHERE is_deleted = 0")
    assert cursor.fetchone()["cnt"] == 2

    cursor.execute("SELECT COUNT(*) as cnt FROM event_history")
    assert cursor.fetchone()["cnt"] == 2

    cursor.execute("SELECT high_watermark FROM watermark_state WHERE entity_name = 'CUSTOMER'")
    assert cursor.fetchone()["high_watermark"] == "2026-11-01T10:05:00+00:00"
    conn.close()


def test_incremental_progression(runner, create_test_file, temp_db_path):
    """Verifies second batch advances watermark and updates existing target record."""
    batch1 = [
        {
            "natural_key": "CUST_001",
            "name": "Alice Smith",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "amount": 100.0,
        }
    ]
    runner.run(create_test_file(batch1, "inc_b1.json"))

    batch2 = [
        {
            "natural_key": "CUST_001",
            "name": "Alice Smith Updated",
            "updated_at": "2026-11-01T12:00:00Z",
            "operation_type": "UPDATE",
            "amount": 500.0,
        },
        {
            "natural_key": "CUST_003",
            "name": "Charlie Brown",
            "updated_at": "2026-11-01T12:30:00Z",
            "operation_type": "INSERT",
            "amount": 350.0,
        },
    ]
    result2 = runner.run(create_test_file(batch2, "inc_b2.json"))

    assert result2["status"] == "SUCCESS"
    assert result2["inserted_count"] == 1
    assert result2["updated_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT version, payload FROM target_records WHERE natural_key = 'CUST_001'")
    cust1 = cursor.fetchone()
    assert cust1["version"] == 2
    assert "Alice Smith Updated" in cust1["payload"]

    cursor.execute("SELECT high_watermark FROM watermark_state WHERE entity_name = 'CUSTOMER'")
    assert cursor.fetchone()["high_watermark"] == "2026-11-01T12:30:00+00:00"
    conn.close()
