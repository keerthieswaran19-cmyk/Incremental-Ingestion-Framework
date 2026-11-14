"""Tests for out-of-order events and late-arriving records."""

import sqlite3


def test_late_arriving_record_preserves_target_state(runner, create_test_file, temp_db_path):
    """
    Critical test:
    1. Ingest newer version of CUST_LATE (updated_at = 12:00, amount = 999.0).
    2. Ingest older late-arriving version of CUST_LATE (updated_at = 08:00, amount = 111.0).
    3. Ensure target_records active state remains at 999.0 (does not regress).
    4. Ensure event_history captures both, marking the late arrival with is_out_of_order = 1.
    """
    newer_record = [
        {
            "natural_key": "CUST_LATE",
            "name": "Latest State",
            "updated_at": "2026-11-01T12:00:00Z",
            "operation_type": "INSERT",
            "amount": 999.0,
        }
    ]
    runner.run(create_test_file(newer_record, "batch_newer.json"))

    # Ingest late-arriving older record (must run with incremental=False or backfill mode so it is evaluated)
    older_late_record = [
        {
            "natural_key": "CUST_LATE",
            "name": "Old Out-of-Order State",
            "updated_at": "2026-11-01T08:00:00Z",
            "operation_type": "UPDATE",
            "amount": 111.0,
        }
    ]
    res = runner.run(create_test_file(older_late_record, "batch_late.json"), incremental=False)

    assert res["status"] == "SUCCESS"
    assert res["late_arriving_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Target active record must NOT be regressed!
    cursor.execute("SELECT payload, last_updated_at FROM target_records WHERE natural_key = 'CUST_LATE'")
    target = cursor.fetchone()
    assert '"amount": 999.0' in target["payload"]
    assert target["last_updated_at"] == "2026-11-01T12:00:00+00:00"

    # Event history must have recorded the out-of-order arrival
    cursor.execute("SELECT operation_type, is_out_of_order, event_timestamp FROM event_history WHERE natural_key = 'CUST_LATE' ORDER BY created_at")
    history = cursor.fetchall()
    assert len(history) == 2
    assert history[0]["operation_type"] == "INSERT"
    assert history[0]["is_out_of_order"] == 0
    assert history[1]["operation_type"] == "LATE_ARRIVAL"
    assert history[1]["is_out_of_order"] == 1
    assert history[1]["event_timestamp"] == "2026-11-01T08:00:00+00:00"

    conn.close()
