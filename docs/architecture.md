# Incremental Ingestion Framework: Architecture & System Design

## 1. High-Level Architecture Overview

The **Incremental Ingestion Framework** is an enterprise-grade, transaction-safe data engineering engine designed to reliably ingest continuous or micro-batched event streams. It guarantees:
- **Zero data loss** (via a Dead-Letter Queue / Quarantine table).
- **Exact-once semantics & idempotency** (content-addressable deterministic SHA-256 keys and audited file registry).
- **Bi-temporal consistency** (active snapshot in `target_records` + append-only audit trail in `event_history`).
- **Out-of-order and late-arriving event safety** (temporal monotonicity guards).
- **Atomic rollback and crash recovery** (all target state changes, watermark updates, and metrics committed in single ACID transactions).

---

## 2. End-to-End Pipeline Architecture Diagram

```mermaid
flowchart TD
    subgraph S1["1. Source Layer"]
        RAW["Raw Source Data<br/>(JSON / Batches / API)"]
        REG_CHECK{"File Ingestion<br/>Registry Check"}
    end

    subgraph S2["2. Extraction & Watermarking"]
        EXT["Extractor (Generator Streaming)"]
        WM_READ[("Watermark State<br/>(High Watermark)")]
        INC_FILTER{"Incremental Filter<br/>(updated_at > watermark)"}
    end

    subgraph S3["3. Validation & Quarantine (DLQ)"]
        VAL["Schema & Semantic Validator"]
        VALID_GATE{"Valid Record?"}
        QUAR_TBL[("Quarantine DLQ<br/>(Error Code, Payload, Run ID)")]
    end

    subgraph S4["4. Transformation & Deduplication"]
        TRANS["Transformer & Normalizer<br/>(SHA-256 Deterministic Hash)"]
        DEDUP{"In-Batch & State<br/>Deduplicator"}
        DUP_DISCARD["Discard Duplicate<br/>(Audit Counter +1)"]
    end

    subgraph S5["5. Incremental Processing Engine"]
        ORD_CHECK{"Temporal Order Check<br/>(Incoming vs Target Timestamp)"}
        LATE_RECORD["Late-Arriving Record<br/>(Preserve Audit History Only)"]
        OP_CHECK{"Operation Type?"}
        UPSERT_OP["Upsert Record<br/>(Insert or Update Snapshot)"]
        DELETE_OP["Tombstone Record<br/>(Soft-Delete Snapshot)"]
    end

    subgraph S6["6. Target & Storage Layer (ACID Transaction)"]
        TARGET_DB[("Target Records<br/>(Active Current Snapshot)")]
        HIST_DB[("Event History<br/>(Immutable SCD-4 CDC Log)")]
        WM_WRITE[("Watermark State<br/>(Atomic Advance)")]
        FILE_REG[("File Registry<br/>(Hash, Counts, Status)")]
    end

    subgraph S7["7. Observability, Recovery & Reporting"]
        TX_MGR{"Transaction Success?"}
        RECOVERY["Crash Recovery &<br/>Automatic Rollback"]
        RUNS_DB[("Pipeline Runs<br/>(Metrics, Audit Logs)")]
        RECON["Mathematical Reconciliation<br/>(Source = Target + Quar + Dups)"]
        REPORT["Executive Reports &<br/>Operations Cockpit UI"]
    end

    %% Flow Connections
    RAW --> REG_CHECK
    REG_CHECK -- "Unseen / Valid File" --> EXT
    REG_CHECK -- "Already Processed (Rerun)" --> EXT
    EXT --> INC_FILTER
    WM_READ -.->|"Read Last Watermark"| INC_FILTER
    INC_FILTER --> VAL
    
    VAL --> VALID_GATE
    VALID_GATE -- "Invalid (Schema / Rule)" --> QUAR_TBL
    VALID_GATE -- "Valid" --> TRANS

    TRANS --> DEDUP
    DEDUP -- "Duplicate Event" --> DUP_DISCARD
    DEDUP -- "Unique Event" --> ORD_CHECK

    ORD_CHECK -- "Older than Active State" --> LATE_RECORD
    LATE_RECORD --> HIST_DB
    ORD_CHECK -- "Newer or Equal (Monotonic)" --> OP_CHECK

    OP_CHECK -- "INSERT / UPDATE" --> UPSERT_OP
    OP_CHECK -- "DELETE / TOMBSTONE" --> DELETE_OP

    UPSERT_OP --> TARGET_DB
    UPSERT_OP --> HIST_DB
    DELETE_OP --> TARGET_DB
    DELETE_OP --> HIST_DB

    TARGET_DB & HIST_DB & QUAR_TBL --> TX_MGR
    TX_MGR -- "COMMIT" --> WM_WRITE
    TX_MGR -- "COMMIT" --> FILE_REG
    TX_MGR -- "COMMIT" --> RUNS_DB
    TX_MGR -- "ERROR / CRASH" --> RECOVERY
    RECOVERY -.->|"Rollback to Prev Watermark"| WM_READ

    RUNS_DB & TARGET_DB & QUAR_TBL --> RECON
    RECON --> REPORT
```

---

## 3. Dedicated Subsystem Paths

### 3.1 Rejected Records Path (Quarantine / Dead-Letter Queue)
```mermaid
flowchart LR
    Record[Incoming Event] --> Validator{Validation Engine}
    Validator -- "Pass" --> Pipeline[Pipeline Processing]
    Validator -- "Fail" --> DLQ[Quarantine Table]
    
    subgraph Quarantine Metadata
        DLQ --> F1[error_code: SCHEMA / NULL / FK / TYPE]
        DLQ --> F2[rejection_reason: Descriptive failure text]
        DLQ --> F3[raw_payload: Original verbatim JSON]
        DLQ --> F4[run_id: Traceable Execution ID]
    end

    DLQ --> DLQ_API["/api/quarantine/replay (Redrive Interface)"]
```

### 3.2 Watermark & Incremental Progression Path
```mermaid
sequenceDiagram
    autonumber
    participant Pipeline
    participant WatermarkState
    participant TargetDB
    participant PipelineRuns

    Pipeline->>WatermarkState: Read high_watermark for entity
    WatermarkState-->>Pipeline: last_watermark_timestamp
    Pipeline->>Pipeline: Filter: record.updated_at > last_watermark_timestamp
    Pipeline->>TargetDB: BEGIN TRANSACTION (IMMEDIATE)
    Pipeline->>TargetDB: Upsert Target Records & Append Event History
    Pipeline->>WatermarkState: Update high_watermark = MAX(batch.updated_at)
    Pipeline->>PipelineRuns: Log input, inserted, updated, deleted, quarantined
    Pipeline->>TargetDB: COMMIT TRANSACTION
    Note over Pipeline,TargetDB: Watermark only advances if commit succeeds!
```

### 3.3 Deduplication Strategy
```mermaid
flowchart TD
    In[Event Batch] --> Hash["Compute Deterministic Hash: SHA256(natural_keys)"]
    Hash --> WindowDedup{"In-Batch Deduplication<br/>(Keep latest event_timestamp)"}
    WindowDedup --> StateCheck{"Cross-Batch Deduplication<br/>(Target record content hash == incoming hash?)"}
    StateCheck -- "Identical (No op)" --> Skip["Increment duplicate_count (Skip write)"]
    StateCheck -- "Newer Payload" --> Proceed["Proceed to Target Upsert"]
```

### 3.4 Temporal Monotonicity & Late-Arrival Recovery
```mermaid
flowchart TD
    E[Incoming Event E for Entity ID] --> T{Record exists in Target?}
    T -- "No" --> Insert["Insert into target_records & event_history"]
    T -- "Yes" --> Comp{"E.updated_at >= Target.last_updated_at?"}
    Comp -- "Yes (In Order / Newer)" --> Update["Update target_records & Append event_history"]
    Comp -- "No (Late-Arriving / Out-of-Order)" --> OutOfOrder["Append to event_history only<br/>(Preserve Target Active State!)"]
```

### 3.5 Failure Recovery & Rollback Mechanics
- **ACID Transaction Boundaries**: All write operations for a batch—target upsert, CDC event history, quarantine logging, and watermark advancement—are executed within a single `sqlite3` transaction (`BEGIN IMMEDIATE`).
- **Crash Safety**: In case of a crash or unhandled runtime failure, `ROLLBACK` triggers automatically. No orphan target rows, partial watermarks, or desynchronized metrics can exist.
- **File Registry Checksum**: Incoming batches are registered with their SHA-256 checksum in `file_registry`. Reruns of identical files skip reprocessing unless `--force-reprocess` is explicitly passed.
