# Architecture Decision Records (ADRs) & Design Decisions

This document outlines key engineering choices, trade-offs, and rationale adopted in the **Incremental Ingestion Framework**.

---

## ADR 001: SQLite with Write-Ahead Logging (WAL) Mode
- **Status**: Accepted
- **Context**: The framework must run locally with zero external database dependencies (no Postgres/MySQL), but still achieve production-grade performance (>10,000 records/sec) with strict ACID guarantees.
- **Decision**: Use Python's built-in `sqlite3` engine configured with:
  - `PRAGMA journal_mode = WAL;` (Allows concurrent readers while writing, avoids locking bottlenecks).
  - `PRAGMA synchronous = NORMAL;` (Significantly accelerates batch inserts while guaranteeing database integrity).
  - `PRAGMA temp_store = MEMORY;`
  - `PRAGMA cache_size = -64000;` (64 MB page cache).
- **Consequences**: Enables 30,000+ writes/sec without requiring external service installation or Docker.

---

## ADR 002: Bi-Temporal State Management (SCD-4 Architecture)
- **Status**: Accepted
- **Context**: In analytical environments, consumers need fast queries on the latest record state, while compliance and auditing require complete point-in-time change data capture (CDC) history.
- **Decision**: Maintain two physical tables:
  1. `target_records`: A curated current snapshot (one row per natural entity, indexed by `natural_key` and `entity_id`).
  2. `event_history`: An append-only ledger capturing every transition (`INSERT`, `UPDATE`, `DELETE`, `LATE_ARRIVAL`).
- **Consequences**: Queries for the "current state" do not incur costly window functions or full-table scans, while full historical auditability is preserved.

---

## ADR 003: Temporal Monotonicity for Out-of-Order & Late Arrivals
- **Status**: Accepted
- **Context**: Mobile clients, IoT sensors, and distributed event buses often deliver events out of order or with significant latency. If an older update arrives *after* a newer update has already modified `target_records`, blindly overwriting will corrupt the active state.
- **Decision**: Before updating `target_records`, the loader compares incoming `event_timestamp` against `target_records.last_updated_at`:
  - If `incoming >= target`: Active snapshot is updated, and event is recorded to `event_history`.
  - If `incoming < target`: Active snapshot is **not touched**; the event is recorded in `event_history` with `is_out_of_order = 1`.
- **Consequences**: Eliminates silent state regression bugs while ensuring zero data loss.

---

## ADR 004: Two-Tier Deduplication & Content-Addressable Hashes
- **Status**: Accepted
- **Context**: Reruns of identical batches or duplicate events in streams must not create duplicate records or redundant updates.
- **Decision**: 
  - Each incoming record generates a deterministic `SHA-256` content hash of its core business attributes.
  - In-batch deduplication: Drops identical duplicate hashes within the same micro-batch.
  - Target-state deduplication: If a record's content hash matches the existing `target_records.record_hash`, no update is performed, and `duplicate_count` is incremented.
- **Consequences**: Complete idempotent rerun safety.

---

## ADR 005: Atomic Watermark Advancement Inside Transaction
- **Status**: Accepted
- **Context**: Advancing watermarks outside target database transactions risks watermark desynchronization if target writes succeed but the watermark store fails, or vice-versa.
- **Decision**: Watermark state updates to `watermark_state` are executed within the **exact same SQLite transaction** (`BEGIN IMMEDIATE ... COMMIT`) as target upserts and history logging.
- **Consequences**: Eliminates zombie watermarks and guarantees consistency across crash-recovery scenarios.
