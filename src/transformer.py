"""Transformer module for deterministic hashing and data normalization."""

import hashlib
import json
from typing import Any, Dict
from src.validator import ValidatedRecord


class TransformedRecord:
    """Represents a fully enriched and hashed record ready for loading."""

    def __init__(
        self,
        event_id: str,
        entity_id: str,
        natural_key: str,
        entity_type: str,
        operation_type: str,
        payload: Dict[str, Any],
        record_hash: str,
        updated_at: str,
    ):
        self.event_id = event_id
        self.entity_id = entity_id
        self.natural_key = natural_key
        self.entity_type = entity_type
        self.operation_type = operation_type
        self.payload = payload
        self.record_hash = record_hash
        self.updated_at = updated_at

    def payload_json(self) -> str:
        """Returns canonical JSON representation of the payload."""
        return json.dumps(self.payload, sort_keys=True, ensure_ascii=False)


class Transformer:
    """Transforms validated records into content-addressable enriched models."""

    @staticmethod
    def generate_entity_id(entity_type: str, natural_key: str) -> str:
        """Computes deterministic SHA-256 entity identifier."""
        raw = f"{entity_type.upper()}:{natural_key.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_record_hash(payload: Dict[str, Any]) -> str:
        """Computes deterministic SHA-256 hash of core payload content."""
        # Exclude metadata fields that change per event from the state hash
        ignore_fields = {"updated_at", "operation_type", "_raw_payload", "run_id", "source_file"}
        filtered = {k: v for k, v in payload.items() if k not in ignore_fields}
        canonical_str = json.dumps(filtered, sort_keys=True, default=str)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_event_id(run_id: str, source_file: str, natural_key: str, updated_at: str, op_type: str) -> str:
        """Computes unique deterministic event ID."""
        raw = f"{run_id}:{source_file}:{natural_key}:{updated_at}:{op_type}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def transform(cls, record: ValidatedRecord, run_id: str, source_file: str) -> TransformedRecord:
        """Enriches and transforms a validated record."""
        entity_id = cls.generate_entity_id(record.entity_type, record.natural_key)
        record_hash = cls.compute_record_hash(record.payload)
        event_id = cls.generate_event_id(run_id, source_file, record.natural_key, record.updated_at, record.operation_type)

        return TransformedRecord(
            event_id=event_id,
            entity_id=entity_id,
            natural_key=record.natural_key,
            entity_type=record.entity_type,
            operation_type=record.operation_type,
            payload=record.payload,
            record_hash=record_hash,
            updated_at=record.updated_at,
        )
