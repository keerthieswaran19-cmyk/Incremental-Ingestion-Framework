"""Extractor module for streaming JSON and JSONL records with file hashing."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple


class Extractor:
    """Streams records from source files with minimal memory footprint."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Source file not found: {self.file_path}")

    def compute_file_hash(self) -> str:
        """Calculates SHA-256 hash of the entire file for registry idempotency."""
        hasher = hashlib.sha256()
        with open(self.file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def get_file_metadata(self) -> Dict[str, Any]:
        """Returns file size, path, and SHA-256 hash."""
        return {
            "file_path": str(self.file_path.resolve()),
            "file_name": self.file_path.name,
            "file_size_bytes": self.file_path.stat().st_size,
            "file_hash": self.compute_file_hash(),
        }

    def stream_records(self) -> Generator[Tuple[int, str, Any], None, None]:
        """
        Yields (record_index, raw_string, parsed_data) for each record.
        Supports both JSON arrays ([{...}, {...}]) and newline-delimited JSONL.
        """
        file_suffix = self.file_path.suffix.lower()

        # Handle JSONL / NDJSON
        if file_suffix in [".jsonl", ".ndjson"]:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    raw_line = line.strip()
                    if not raw_line:
                        continue
                    try:
                        parsed = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        parsed = {"_parse_error": str(exc)}
                    yield idx, raw_line, parsed

        # Handle standard JSON (Array or single object)
        else:
            with open(self.file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return

                try:
                    data = json.loads(content)
                    if isinstance(data, list):
                        for idx, item in enumerate(data):
                            raw_str = json.dumps(item) if not isinstance(item, str) else item
                            yield idx, raw_str, item
                    elif isinstance(data, dict):
                        yield 0, content, data
                    else:
                        yield 0, content, {"_parse_error": f"Root JSON must be object or array, got {type(data).__name__}"}
                except json.JSONDecodeError as exc:
                    # File is malformed JSON
                    yield 0, content, {"_parse_error": f"Invalid JSON syntax: {str(exc)}"}
