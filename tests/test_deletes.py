"""Tests for delete operations, tombstones, and entity reincarnation."""

import sqlite3


def test_tombstone_and_soft_delete(runner, create_test_file, temp_db_path):
    """Verifies that DELETE operation creates tombstones in target and logs in history."""
    insert_data = [
        {"natural_key": "CUST_DEL", "name": "To Be Deleted", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 100.0}
    ]
    runner.run(create_test_file(insert_data, "del_ins.json"))

    delete_data = [
        {"natural_key": "CUST_DEL", "name": "To Be Deleted", "updated_at": "2026-11-01T11:00:00Z", "operation_type": "DELETE", "amount": 0.0}
    ]
    del_res = runner.run(create_test_file(delete_data, "del_op.json"))

    assert del_res["status"] == "SUCCESS"
    assert del_res["deleted_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Active target count should be 0
    cursor.execute("SELECT COUNT(*) as cnt FROM target_records WHERE is_deleted = 0")
    assert cursor.fetchone()["cnt"] == 0

    # Tombstoned record exists with is_deleted = 1 and version = 2
    cursor.execute("SELECT is_deleted, version FROM target_records WHERE natural_key = 'CUST_DEL'")
    target = cursor.fetchone()
    assert target["is_deleted"] == 1
    assert target["version"] == 2

    # History contains both INSERT and DELETE
    cursor.execute("SELECT operation_type FROM event_history WHERE natural_key = 'CUST_DEL' ORDER BY created_at")
    ops = [r["operation_type"] for r in cursor.fetchall()]
    assert ops == ["INSERT", "DELETE"]

    conn.close()


def test_reincarnation_after_delete(runner, create_test_file, temp_db_path):
    """Verifies that an entity deleted can be subsequently reincarnated with a newer INSERT/UPDATE."""
    # 1. Insert
    runner.run(create_test_file([{"natural_key": "CUST_REBORN", "updated_at": "2026-11-01T10:00:00Z", "operation_type": "INSERT", "amount": 50.0}], "step1.json"))
    # 2. Delete
    runner.run(create_test_file([{"natural_key": "CUST_REBORN", "updated_at": "2026-11-01T11:00:00Z", "operation_type": "DELETE", "amount": 0.0}], "step2.json"))
    # 3. Reincarnate with higher timestamp
    runner.run(create_test_file([{"natural_key": "CUST_REBORN", "name": "Reborn User", "updated_at": "2026-11-01T12:00:00Z", "operation_type": "INSERT", "amount": 750.0}], "step3.json"))

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT is_deleted, version, payload FROM target_records WHERE natural_key = 'CUST_REBORN'")
    row = cursor.fetchone()
    assert row["is_deleted"] == 0
    assert row["version"] == 3
    assert "Reborn User" in row["payload"]
    conn.close()
