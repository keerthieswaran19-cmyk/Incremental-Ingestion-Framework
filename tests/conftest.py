"""Pytest fixtures with isolated temporary databases for testing."""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator
import pytest

from src.database import DDL_STATEMENTS, get_connection
from src.pipeline import PipelineRunner


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Provides a fresh isolated SQLite database file for each test."""
    db_file = tmp_path / "test_ingestion.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(DDL_STATEMENTS)
    conn.close()
    return db_file


@pytest.fixture
def runner(temp_db_path: Path) -> PipelineRunner:
    """Provides a PipelineRunner wired to the isolated test database."""
    return PipelineRunner(db_path=temp_db_path)


@pytest.fixture
def create_test_file(tmp_path: Path):
    """Helper factory to create temporary JSON files."""
    def _create_file(data, filename: str = "test_data.json") -> Path:
        file_path = tmp_path / filename
        with open(file_path, "w", encoding="utf-8") as f:
            if isinstance(data, (list, dict)):
                json.dump(data, f)
            else:
                f.write(str(data))
        return file_path
    return _create_file
