"""Tests for validation rules, corrupt records, and quarantine routing."""

import sqlite3


def test_missing_natural_key_quarantine(runner, create_test_file, temp_db_path):
    """Verifies records without a natural key are routed to quarantine."""
    records = [
        {"natural_key": None, "name": "No Key", "updated_at": "2026-11-01T10:00:00Z"},
        {"name": "Missing Field", "updated_at": "2026-11-01T10:00:00Z"},
    ]
    res = runner.run(create_test_file(records, "bad_keys.json"))

    assert res["quarantined_count"] == 2
    assert res["inserted_count"] == 0

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT error_code FROM quarantine")
    errors = [r["error_code"] for r in cursor.fetchall()]
    assert all(e == "MISSING_REQUIRED_FIELD" for e in errors)
    conn.close()


def test_malformed_json_quarantine(runner, create_test_file, temp_db_path):
    """Verifies unparseable malformed JSON is safely caught and quarantined."""
    raw_content = "{ invalid json string syntax: 123"
    file_path = create_test_file(raw_content, "corrupt.json")
    res = runner.run(file_path)

    assert res["quarantined_count"] == 1
    assert res["inserted_count"] == 0

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT error_code FROM quarantine")
    row = cursor.fetchone()
    assert row["error_code"] == "MALFORMED_JSON"
    conn.close()


def test_domain_rule_violations(runner, create_test_file, temp_db_path):
    """Verifies negative amount and invalid foreign reference key violations."""
    records = [
        {
            "natural_key": "CUST_NEG",
            "name": "Negative User",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "amount": -50.0,
        },
        {
            "natural_key": "CUST_BAD_REF",
            "name": "Bad Ref User",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "account_id": "WRONG_PREFIX_999",  # Must start with ACC-
            "amount": 100.0,
        },
    ]
    res = runner.run(create_test_file(records, "domain_rules.json"))

    assert res["quarantined_count"] == 2
    assert res["inserted_count"] == 0

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT error_code FROM quarantine ORDER BY error_code")
    codes = [r["error_code"] for r in cursor.fetchall()]
    assert "DOMAIN_RULE_VIOLATION" in codes
    assert "INVALID_REFERENCE_KEY" in codes
    conn.close()


def test_dlq_idempotency_across_runs(runner, create_test_file, temp_db_path):
    """
    Verifies that rerunning a file containing invalid records across multiple runs
    does not create duplicate rows in quarantine, but increments occurrence_count.
    """
    records = [
        {"natural_key": None, "name": "Bad Key", "updated_at": "2026-11-01T10:00:00Z"},
    ]
    file_path = create_test_file(records, "dlq_idem.json")

    run1 = runner.run(file_path)
    assert run1["quarantined_count"] == 1

    run2 = runner.run(file_path, force_reprocess=True)
    assert run2["quarantined_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt, occurrence_count, run_id FROM quarantine")
    row = cursor.fetchone()
    # Still exactly 1 unique quarantine row!
    assert row["cnt"] == 1
    # Occurrence count incremented to 2!
    assert row["occurrence_count"] == 2
    assert row["run_id"] == run2["run_id"]
    conn.close()


def test_schema_drift_detection(runner, create_test_file, temp_db_path, caplog):
    """Verifies that unexpected fields or mutated datatypes trigger SCHEMA_DRIFT_DETECTED without halting ingestion."""
    import logging
    records = [
        {
            "natural_key": "CUST_DRIFT_1",
            "name": "Drifted User",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "novel_feature_field": "SURPRISE_METRIC_VALUE",  # Unexpected new field
            "custom_metadata_tag": 12345,                    # Another drifted attribute
        }
    ]
    file_path = create_test_file(records, "drift_batch.json")
    with caplog.at_level(logging.WARNING):
        res = runner.run(file_path)

    # Ingestion should succeed and preserve the record
    assert res["status"] == "SUCCESS"
    assert res["inserted_count"] == 1
    assert res["quarantined_count"] == 0

    # Log should contain SCHEMA_DRIFT_DETECTED warnings
    assert any("SCHEMA_DRIFT_DETECTED" in rec.message for rec in caplog.records)


def test_foreign_key_parent_table_lookup(create_test_file, temp_db_path):
    """Verifies that optional parent table lookup routes invalid foreign keys to quarantine."""
    from src.validator import Validator
    from src.pipeline import PipelineRunner

    # Mock foreign key provider: only ACC-1001 exists in parent table
    valid_accounts = {"ACC-1001", "ACC-1002"}
    def mock_fk_provider(table_name, key_val):
        return key_val in valid_accounts

    validator = Validator(
        run_id="TEST_RUN",
        source_file="fk_test.json",
        fk_lookup_provider=mock_fk_provider,
    )

    # 1. Valid foreign key that exists
    valid_raw = '{"natural_key": "CUST_1", "updated_at": "2026-11-01T10:00:00Z", "account_id": "ACC-1001"}'
    v_rec, q_rec = validator.validate_record(valid_raw, {"natural_key": "CUST_1", "updated_at": "2026-11-01T10:00:00Z", "account_id": "ACC-1001"})
    assert v_rec is not None
    assert q_rec is None

    # 2. Well-formatted ACC-* key that DOES NOT exist in parent table
    missing_raw = '{"natural_key": "CUST_2", "updated_at": "2026-11-01T10:00:00Z", "account_id": "ACC-9999"}'
    v_rec2, q_rec2 = validator.validate_record(missing_raw, {"natural_key": "CUST_2", "updated_at": "2026-11-01T10:00:00Z", "account_id": "ACC-9999"})
    assert v_rec2 is None
    assert q_rec2 is not None
    assert q_rec2.error_code == "FOREIGN_KEY_NOT_FOUND"

