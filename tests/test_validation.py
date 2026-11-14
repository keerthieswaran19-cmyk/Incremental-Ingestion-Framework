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
