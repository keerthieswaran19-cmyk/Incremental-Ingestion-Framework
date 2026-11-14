"""Validation engine for schema compliance, data quality checks, and quarantine routing."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError


class ValidatedRecord(BaseModel):
    """Canonical model for validated source event records."""
    natural_key: str = Field(..., min_length=1)
    entity_type: str = Field(default="CUSTOMER")
    updated_at: str = Field(...)
    operation_type: str = Field(default="INSERT")
    payload: Dict[str, Any] = Field(default_factory=dict)


class QuarantineRecord(BaseModel):
    """Structure for invalid records routed to Dead-Letter Queue."""
    quarantine_id: str
    source_file: str
    raw_payload: str
    error_code: str
    error_message: str
    run_id: str
    created_at: str


class Validator:
    """Validates records and separates clean data from quarantined anomalies."""

    def __init__(self, run_id: str, source_file: str):
        self.run_id = run_id
        self.source_file = source_file

    def _generate_quarantine_id(self, raw_payload: str, error_code: str) -> str:
        """Generates a deterministic hash ID for the quarantine entry."""
        hash_input = f"{self.run_id}:{self.source_file}:{error_code}:{raw_payload}"
        return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    def _parse_iso_timestamp(self, ts_str: Any) -> Optional[datetime]:
        """Validates and parses ISO-8601 timestamp strings."""
        if not isinstance(ts_str, str):
            return None
        # Support common ISO-8601 formats including trailing Z
        cleaned = ts_str.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None

    def validate_record(self, raw_str: str, parsed_data: Any) -> Tuple[Optional[ValidatedRecord], Optional[QuarantineRecord]]:
        """
        Validates an incoming record against syntax, schema, and domain rules.
        Returns (ValidatedRecord, None) if clean, or (None, QuarantineRecord) if rejected.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Check for JSON parse failures
        if isinstance(parsed_data, dict) and "_parse_error" in parsed_data:
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "MALFORMED_JSON"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="MALFORMED_JSON",
                error_message=parsed_data["_parse_error"],
                run_id=self.run_id,
                created_at=now_iso,
            )

        if not isinstance(parsed_data, dict):
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "INVALID_RECORD_FORMAT"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="INVALID_RECORD_FORMAT",
                error_message=f"Record must be a JSON object, got {type(parsed_data).__name__}",
                run_id=self.run_id,
                created_at=now_iso,
            )

        # 2. Extract and check natural key (e.g. natural_key, customer_id, id, order_id)
        natural_key = (
            parsed_data.get("natural_key")
            or parsed_data.get("customer_id")
            or parsed_data.get("order_id")
            or parsed_data.get("id")
        )
        if natural_key is None or str(natural_key).strip() == "" or str(natural_key).lower() == "null":
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "MISSING_REQUIRED_FIELD"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="MISSING_REQUIRED_FIELD",
                error_message="Missing or null natural key (natural_key / customer_id / id)",
                run_id=self.run_id,
                created_at=now_iso,
            )

        natural_key_str = str(natural_key).strip()

        # 3. Validate timestamp field (updated_at, timestamp, event_time)
        ts_raw = parsed_data.get("updated_at") or parsed_data.get("timestamp") or parsed_data.get("event_time")
        if not ts_raw:
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "MISSING_TIMESTAMP"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="MISSING_TIMESTAMP",
                error_message="Missing required timestamp field (updated_at / timestamp)",
                run_id=self.run_id,
                created_at=now_iso,
            )

        parsed_ts = self._parse_iso_timestamp(ts_raw)
        if not parsed_ts:
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "INVALID_TIMESTAMP_FORMAT"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="INVALID_TIMESTAMP_FORMAT",
                error_message=f"Value '{ts_raw}' is not a valid ISO-8601 timestamp",
                run_id=self.run_id,
                created_at=now_iso,
            )

        # 4. Validate operation type
        op_raw = parsed_data.get("operation_type") or parsed_data.get("op") or "INSERT"
        op_type = str(op_raw).upper()
        if op_type not in ["INSERT", "UPDATE", "DELETE", "TOMBSTONE"]:
            return None, QuarantineRecord(
                quarantine_id=self._generate_quarantine_id(raw_str, "INVALID_OPERATION_TYPE"),
                source_file=self.source_file,
                raw_payload=raw_str,
                error_code="INVALID_OPERATION_TYPE",
                error_message=f"Operation type '{op_raw}' is invalid. Expected INSERT, UPDATE, or DELETE.",
                run_id=self.run_id,
                created_at=now_iso,
            )
        if op_type == "TOMBSTONE":
            op_type = "DELETE"

        # 5. Domain-specific validation checks (e.g. invalid reference key, negative balance/amount)
        # Check reference key format if present (e.g., account_id must match ACC-XXXXX pattern or start with ACC-)
        if "account_id" in parsed_data:
            acc_id = str(parsed_data["account_id"]).strip()
            if not acc_id.startswith("ACC-") and acc_id != "":
                return None, QuarantineRecord(
                    quarantine_id=self._generate_quarantine_id(raw_str, "INVALID_REFERENCE_KEY"),
                    source_file=self.source_file,
                    raw_payload=raw_str,
                    error_code="INVALID_REFERENCE_KEY",
                    error_message=f"Foreign reference 'account_id' must start with 'ACC-', got '{acc_id}'",
                    run_id=self.run_id,
                    created_at=now_iso,
                )

        # Negative amount check for financial / transaction events
        if "amount" in parsed_data and parsed_data["amount"] is not None:
            try:
                amt = float(parsed_data["amount"])
                if amt < 0 and op_type != "DELETE":
                    return None, QuarantineRecord(
                        quarantine_id=self._generate_quarantine_id(raw_str, "DOMAIN_RULE_VIOLATION"),
                        source_file=self.source_file,
                        raw_payload=raw_str,
                        error_code="DOMAIN_RULE_VIOLATION",
                        error_message=f"Attribute 'amount' cannot be negative ({amt})",
                        run_id=self.run_id,
                        created_at=now_iso,
                    )
            except (ValueError, TypeError):
                return None, QuarantineRecord(
                    quarantine_id=self._generate_quarantine_id(raw_str, "INVALID_DATA_TYPE"),
                    source_file=self.source_file,
                    raw_payload=raw_str,
                    error_code="INVALID_DATA_TYPE",
                    error_message=f"Attribute 'amount' must be numeric, got '{parsed_data['amount']}'",
                    run_id=self.run_id,
                    created_at=now_iso,
                )

        # Standardize payload dict
        entity_type = parsed_data.get("entity_type", "CUSTOMER").upper()
        # Canonical ISO-8601 formatted timestamp string with UTC timezone
        normalized_ts = parsed_ts.astimezone(timezone.utc).isoformat()

        # Build clean payload copy
        clean_payload = {k: v for k, v in parsed_data.items() if not k.startswith("_")}
        clean_payload["natural_key"] = natural_key_str
        clean_payload["updated_at"] = normalized_ts
        clean_payload["operation_type"] = op_type
        clean_payload["entity_type"] = entity_type

        return ValidatedRecord(
            natural_key=natural_key_str,
            entity_type=entity_type,
            updated_at=normalized_ts,
            operation_type=op_type,
            payload=clean_payload,
        ), None
