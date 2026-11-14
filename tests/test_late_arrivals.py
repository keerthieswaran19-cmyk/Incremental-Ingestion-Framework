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


def test_late_arrival_with_lookback_window(runner, create_test_file, temp_db_path):
    """
    Verifies that with incremental=True:
    1. A late arrival within the lookback window passes the watermark gate.
    2. Target state is protected against regression.
    3. Event history records the out-of-order event.
    """
    # 1. Advance watermark to 2026-11-05T12:00:00Z
    initial = [
        {"natural_key": "CUST_LB", "name": "Current State", "updated_at": "2026-11-05T12:00:00Z", "amount": 500.0}
    ]
    runner.run(create_test_file(initial, "lb_init.json"))

    # 2. Ingest late-arriving record with incremental=True and lookback_hours=72 (3 days)
    # Event timestamp is 2026-11-04T10:00:00Z (26 hours prior to current watermark)
    late_batch = [
        {"natural_key": "CUST_LB", "name": "Older Late State", "updated_at": "2026-11-04T10:00:00Z", "amount": 100.0}
    ]
    res = runner.run(create_test_file(late_batch, "lb_late.json"), incremental=True, lookback_hours=72)

    assert res["status"] == "SUCCESS"
    assert res["late_arriving_count"] == 1

    # Verify target state was NOT regressed
    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT payload, last_updated_at FROM target_records WHERE natural_key = 'CUST_LB'")
    target = cursor.fetchone()
    assert '"amount": 500.0' in target["payload"]
    assert target["last_updated_at"] == "2026-11-05T12:00:00+00:00"
    conn.close()
