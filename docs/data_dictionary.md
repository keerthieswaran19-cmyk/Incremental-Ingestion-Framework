# Data Dictionary & Database Schema Specification

This document details the complete SQLite schema design for the **Incremental Ingestion Framework**, supporting high-performance CDC upserts, time-travel history (SCD-4), quarantine dead-letter queue, atomic watermarks, and full observability.

---

## 1. Table Summary

| Table Name | Grain | Purpose | Key Constraints |
| :--- | :--- | :--- | :--- |
| `source_events` | 1 row per received event | Immutable landing log of raw received payloads. | PK `event_id` |
| `target_records` | 1 row per active entity | Curated current active snapshot for fast analytical consumption. | PK `entity_id`, Unique `natural_key` |
| `event_history` | 1 row per state mutation | Complete historical audit / CDC ledger of all changes & tombstones. | PK `history_id`, FK `entity_id` |
| `quarantine` | 1 row per rejected record | Dead-letter queue (DLQ) for malformed or rule-violating records. | PK `quarantine_id` |
| `pipeline_runs` | 1 row per pipeline execution | Operational metrics, row counts, durations, and health status. | PK `run_id` |
| `watermark_state` | 1 row per tracked entity | High-watermark state tracking for incremental processing. | PK `entity_name` |
| `file_registry` | 1 row per ingested file/batch | Checksums and ingestion state to ensure file-level idempotency. | PK `file_hash` |

---

## 2. Table Specifications

### 2.1 `source_events`
Stores every raw event as ingested before any transformations, ensuring 100% forensic auditability.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `event_id` | `TEXT` | `NO` | Deterministic SHA-256 hash or UUID representing the event. (PK) |
| `entity_id` | `TEXT` | `YES` | Natural key or identifier of the entity. |
| `source_file` | `TEXT` | `NO` | Path or name of the source file / stream partition. |
| `raw_payload` | `TEXT` | `NO` | Verbatim JSON string as received from source. |
| `received_at` | `TEXT` | `NO` | ISO-8601 timestamp when received by framework (`YYYY-MM-DDTHH:MM:SSZ`). |
| `run_id` | `TEXT` | `NO` | Ingestion execution identifier referencing `pipeline_runs(run_id)`. |

*Indexes:*
- `idx_source_events_run_id` on `(run_id)`
- `idx_source_events_received_at` on `(received_at)`

---

### 2.2 `target_records`
The active, current truth of all business records. Supports upserts, versioning, and soft-delete tombstones.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `entity_id` | `TEXT` | `NO` | Deterministic entity identifier: `SHA256(entity_type + ':' + natural_key)`. (PK) |
| `natural_key` | `TEXT` | `NO` | Natural business identifier (e.g., `user_id`, `order_id`, `device_id`). UNIQUE. |
| `entity_type` | `TEXT` | `NO` | Domain entity name (e.g., `CUSTOMER`, `TRANSACTION`, `IOT_METRIC`). |
| `payload` | `TEXT` | `NO` | Curated, validated JSON object representing current attributes. |
| `record_hash` | `TEXT` | `NO` | SHA-256 hash of payload attributes to detect changes and prevent redundant writes. |
| `version` | `INTEGER` | `NO` | Monotonically increasing revision counter for this entity. |
| `is_deleted` | `INTEGER` | `NO` | Boolean flag (0 = active, 1 = tombstoned/soft-deleted). Default 0. |
| `source_file` | `TEXT` | `NO` | Source batch that caused the latest update. |
| `first_seen_at` | `TEXT` | `NO` | ISO-8601 timestamp when first ingested. |
| `last_updated_at` | `TEXT` | `NO` | ISO-8601 timestamp of the latest event update. |
| `last_run_id` | `TEXT` | `NO` | Latest execution run ID referencing `pipeline_runs(run_id)`. |

*Indexes:*
- `idx_target_records_natural_key` on `(natural_key)`
- `idx_target_records_last_updated` on `(last_updated_at)`
- `idx_target_records_deleted` on `(is_deleted)`

---

### 2.3 `event_history`
Append-only changelog capturing every state transition (SCD-4 pattern). Enables point-in-time time-travel and out-of-order event preservation.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `history_id` | `TEXT` | `NO` | Unique hash or UUID for this audit entry. (PK) |
| `entity_id` | `TEXT` | `NO` | Foreign key referencing `target_records(entity_id)`. |
| `natural_key` | `TEXT` | `NO` | Natural business identifier. |
| `operation_type` | `TEXT` | `NO` | `INSERT`, `UPDATE`, `DELETE`, or `LATE_ARRIVAL`. |
| `payload` | `TEXT` | `NO` | Snapshot of the payload at the time of this mutation. |
| `record_hash` | `TEXT` | `NO` | Hash of payload attributes at this point in time. |
| `event_timestamp` | `TEXT` | `NO` | Source event timestamp (`updated_at` from the event). |
| `is_out_of_order` | `INTEGER` | `NO` | 1 if event arrived out of temporal order; 0 otherwise. |
| `source_file` | `TEXT` | `NO` | Originating batch file. |
| `run_id` | `TEXT` | `NO` | Execution run ID referencing `pipeline_runs(run_id)`. |
| `created_at` | `TEXT` | `NO` | Pipeline ingestion timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |

*Indexes:*
- `idx_event_history_entity_id` on `(entity_id)`
- `idx_event_history_event_time` on `(event_timestamp)`
- `idx_event_history_run_id` on `(run_id)`

---

### 2.4 `quarantine`
Dead-Letter Queue (DLQ) for all malformed, schema-violating, or corrupted records.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `quarantine_id` | `TEXT` | `NO` | Deterministic hash or UUID of the rejected event. (PK) |
| `source_file` | `TEXT` | `NO` | Source file or stream where the error originated. |
| `raw_payload` | `TEXT` | `NO` | Verbatim corrupt or malformed payload. |
| `error_code` | `TEXT` | `NO` | Standard error classification (e.g., `SCHEMA_VALIDATION_ERROR`, `NULL_KEY_ERROR`, `INVALID_TIMESTAMP`, `MALFORMED_JSON`). |
| `error_message` | `TEXT` | `NO` | Detailed human-readable root-cause failure description. |
| `occurrence_count` | `INTEGER` | `NO` | Number of times this identical content error has occurred. Default 1. |
| `replayed` | `INTEGER` | `NO` | 0 = pending review, 1 = successfully replayed. Default 0. |
| `run_id` | `TEXT` | `NO` | Execution run ID referencing `pipeline_runs(run_id)`. |
| `created_at` | `TEXT` | `NO` | ISO-8601 timestamp when quarantined. |

*Indexes:*
- `idx_quarantine_error_code` on `(error_code)`
- `idx_quarantine_run_id` on `(run_id)`
- `idx_quarantine_replayed` on `(replayed)`

---

### 2.5 `pipeline_runs`
Master operational audit log for every execution of the ingestion framework.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `run_id` | `TEXT` | `NO` | Unique execution identifier (e.g., `RUN_20261114_143000_A1B2`). (PK) |
| `start_time` | `TEXT` | `NO` | ISO-8601 execution start timestamp. |
| `end_time` | `TEXT` | `YES` | ISO-8601 execution completion timestamp. |
| `status` | `TEXT` | `NO` | `RUNNING`, `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED`. |
| `source_file` | `TEXT` | `YES` | Target batch file or source identifier. |
| `input_count` | `INTEGER` | `NO` | Total count of raw events processed. Default 0. |
| `inserted_count` | `INTEGER` | `NO` | Total new records created in `target_records`. Default 0. |
| `updated_count` | `INTEGER` | `NO` | Total existing records updated in `target_records`. Default 0. |
| `deleted_count` | `INTEGER` | `NO` | Total records soft-deleted / tombstoned. Default 0. |
| `quarantined_count`| `INTEGER` | `NO` | Total records rejected and routed to quarantine. Default 0. |
| `duplicate_count` | `INTEGER` | `NO` | Total identical duplicate records safely skipped. Default 0. |
| `duration_ms` | `REAL` | `YES` | Total execution duration in milliseconds. |
| `records_per_sec` | `REAL` | `YES` | Throughput achieved during this run. |
| `watermark_start` | `TEXT` | `YES` | High watermark at start of run. |
| `watermark_end` | `TEXT` | `YES` | High watermark committed at end of run. |
| `error_message` | `TEXT` | `YES` | Error message and stacktrace if run failed. |

*Indexes:*
- `idx_pipeline_runs_start_time` on `(start_time)`
- `idx_pipeline_runs_status` on `(status)`

---

### 2.6 `watermark_state`
State store for incremental high-watermarks.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `entity_name` | `TEXT` | `NO` | Entity or topic identifier (e.g., `CUSTOMER`, `ORDERS`). (PK) |
| `high_watermark` | `TEXT` | `NO` | ISO-8601 high-watermark timestamp (`updated_at` threshold). |
| `updated_at` | `TEXT` | `NO` | ISO-8601 timestamp when watermark was advanced. |
| `last_run_id` | `TEXT` | `NO` | Run ID that committed this watermark state. |

---

### 2.7 `file_registry`
File-level deduplication and batch tracking table.

| Column | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `file_hash` | `TEXT` | `NO` | SHA-256 hash of the complete source file. (PK) |
| `file_path` | `TEXT` | `NO` | Canonical relative or absolute file path. |
| `file_size_bytes`| `INTEGER` | `NO` | Size of the file in bytes. |
| `record_count` | `INTEGER` | `NO` | Number of records contained in the file. |
| `status` | `TEXT` | `NO` | `PROCESSED`, `FAILED`, or `SKIPPED`. |
| `processed_at` | `TEXT` | `NO` | ISO-8601 timestamp when processed. |
| `run_id` | `TEXT` | `NO` | Run ID referencing `pipeline_runs(run_id)`. |

*Indexes:*
- `idx_file_registry_path` on `(file_path)`
