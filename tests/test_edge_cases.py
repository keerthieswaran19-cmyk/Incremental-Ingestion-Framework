"""Tests for edge cases: empty files, unicode, boundary values."""

import sqlite3


def test_empty_file_ingestion(runner, create_test_file):
    """Verifies that an empty file is handled gracefully without crashing."""
    file_path = create_test_file("", "empty.json")
    res = runner.run(file_path)

    assert res["status"] == "SUCCESS"
    assert res["input_count"] == 0
    assert res["inserted_count"] == 0


def test_empty_json_array(runner, create_test_file):
    """Verifies empty JSON list [] is handled cleanly."""
    file_path = create_test_file([], "empty_array.json")
    res = runner.run(file_path)

    assert res["status"] == "SUCCESS"
    assert res["input_count"] == 0


def test_unicode_and_special_characters(runner, create_test_file, temp_db_path):
    """Verifies full support for multibyte Unicode and non-Latin character sets."""
    records = [
        {
            "natural_key": "CUST_UNICODE_1",
            "name": "வணக்கம்世界 Müller 🚀",
            "description": "Unicode test with emojis and Tamil characters & special symbols: <>&'\"",
            "updated_at": "2026-11-01T10:00:00Z",
            "operation_type": "INSERT",
            "amount": 888.88,
        }
    ]
    file_path = create_test_file(records, "unicode.json")
    res = runner.run(file_path)

    assert res["status"] == "SUCCESS"
    assert res["inserted_count"] == 1

    conn = sqlite3.connect(str(temp_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT payload FROM target_records WHERE natural_key = 'CUST_UNICODE_1'")
    payload = cursor.fetchone()["payload"]
    assert "வணக்கம்世界" in payload
    assert "🚀" in payload
    conn.close()
