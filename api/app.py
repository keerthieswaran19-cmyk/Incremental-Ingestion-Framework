"""FastAPI application for the Incremental Ingestion Framework."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.schemas import DLQReplayRequest, IngestionRequest, QuarantineItem, RunSummaryResponse, WatermarkItem
from src.analytics import ReconciliationEngine
from src.config import DB_PATH, PROJECT_ROOT, SOURCE_DATA_DIR
from src.database import get_connection
from src.pipeline import PipelineRunner
from src.recovery import RecoveryManager

app = FastAPI(
    title="Incremental Ingestion Engine API",
    description="Enterprise REST API for data ingestion, CDC monitoring, reconciliation, and dead-letter queue management.",
    version="1.0.0",
)

# Enable CORS for local UI and external clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve UI static assets
ui_path = PROJECT_ROOT / "ui"
if ui_path.exists():
    app.mount("/static", StaticFiles(directory=str(ui_path)), name="static")


@app.get("/", response_class=HTMLResponse)
def get_root():
    """Serves the Mission-Control Operations Dashboard UI."""
    index_file = ui_path / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Incremental Ingestion Framework API</h1><p>Visit <a href='/docs'>/docs</a> for Swagger documentation.</p>")


@app.post("/api/ingest", response_model=Dict[str, Any])
def trigger_ingestion(req: IngestionRequest):
    """Triggers an incremental ingestion run for a given source file."""
    # Resolve file path
    target_file = Path(req.source_file)
    if not target_file.is_absolute():
        target_file = SOURCE_DATA_DIR / req.source_file
        if not target_file.exists():
            target_file = PROJECT_ROOT / req.source_file

    if not target_file.exists():
        raise HTTPException(status_code=404, detail=f"Source file not found: {req.source_file}")

    try:
        runner = PipelineRunner(db_path=DB_PATH)
        result = runner.run(
            source_file=target_file,
            entity_name=req.entity_name,
            force_reprocess=req.force_reprocess,
            incremental=req.incremental,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/runs", response_model=List[Dict[str, Any]])
def get_recent_runs(limit: int = 50):
    """Retrieves operational execution audit logs."""
    conn = get_connection(DB_PATH)
    engine = ReconciliationEngine(conn)
    runs = engine.get_run_metrics()
    conn.close()
    return runs[:limit]


@app.get("/api/runs/{run_id}/reconcile", response_model=Dict[str, Any])
def get_run_reconciliation(run_id: str):
    """Verifies mathematical conservation equation for a specific run."""
    conn = get_connection(DB_PATH)
    engine = ReconciliationEngine(conn)
    result = engine.verify_reconciliation(run_id)
    conn.close()
    return result


@app.get("/api/reconciliation", response_model=Dict[str, Any])
def get_full_reconciliation():
    """Returns global reconciliation matrix and table counts."""
    conn = get_connection(DB_PATH)
    engine = ReconciliationEngine(conn)
    report = engine.generate_reconciliation_report()
    conn.close()
    return report


@app.get("/api/quarantine", response_model=List[QuarantineItem])
def get_quarantine_records(
    limit: int = 100,
    error_code: Optional[str] = None,
):
    """Returns dead-letter queue records for inspection."""
    conn = get_connection(DB_PATH)
    cursor = conn.cursor()
    if error_code:
        cursor.execute("SELECT * FROM quarantine WHERE error_code = ? ORDER BY created_at DESC LIMIT ?", (error_code, limit))
    else:
        cursor.execute("SELECT * FROM quarantine ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.post("/api/quarantine/{quarantine_id}/replay", response_model=Dict[str, Any])
def replay_quarantine_record_api(quarantine_id: str, req: Optional[DLQReplayRequest] = None):
    """Safely and idempotently replays a quarantined record into the target database."""
    conn = get_connection(DB_PATH)
    try:
        corrected = req.corrected_payload if req else None
        res = RecoveryManager.replay_quarantine_record(conn, quarantine_id, corrected)
        if res.get("status") == "NOT_FOUND":
            raise HTTPException(status_code=404, detail=res.get("message", "Not found"))
        elif res.get("status") == "VALIDATION_FAILED":
            raise HTTPException(status_code=422, detail=res)
        return res
    finally:
        conn.close()


@app.get("/api/watermarks", response_model=List[WatermarkItem])
def get_watermarks():
    """Returns all current entity high watermarks."""
    conn = get_connection(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM watermark_state")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.get("/api/overview", response_model=Dict[str, Any])
def get_system_overview():
    """Returns high-level KPI dashboard metrics."""
    conn = get_connection(DB_PATH)
    engine = ReconciliationEngine(conn)
    table_counts = engine.get_table_counts()

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as active_cnt FROM target_records WHERE is_deleted = 0")
    active_cnt = cursor.fetchone()["active_cnt"]

    cursor.execute("SELECT COUNT(*) as tombstone_cnt FROM target_records WHERE is_deleted = 1")
    tombstone_cnt = cursor.fetchone()["tombstone_cnt"]

    cursor.execute("SELECT AVG(records_per_sec) as avg_speed, MAX(records_per_sec) as max_speed FROM pipeline_runs WHERE status = 'SUCCESS'")
    speed_row = cursor.fetchone()

    cursor.execute("SELECT * FROM watermark_state")
    watermarks = [dict(w) for w in cursor.fetchall()]
    conn.close()

    return {
        "status": "HEALTHY",
        "database": "SQLite (WAL Mode Enabled)",
        "table_counts": table_counts,
        "target_snapshot": {
            "active_records": active_cnt,
            "tombstones": tombstone_cnt,
            "total_entities": active_cnt + tombstone_cnt,
        },
        "performance": {
            "avg_records_per_sec": round(speed_row["avg_speed"] or 0.0, 1),
            "max_records_per_sec": round(speed_row["max_speed"] or 0.0, 1),
        },
        "watermarks": watermarks,
    }


@app.get("/api/target-records")
def get_target_records(limit: int = 50, search: Optional[str] = None):
    """Returns curated target records for Event Explorer."""
    conn = get_connection(DB_PATH)
    cursor = conn.cursor()
    if search:
        cursor.execute(
            "SELECT * FROM target_records WHERE natural_key LIKE ? OR entity_type LIKE ? ORDER BY last_updated_at DESC LIMIT ?",
            (f"%{search}%", f"%{search}%", limit)
        )
    else:
        cursor.execute("SELECT * FROM target_records ORDER BY last_updated_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.get("/api/events")
def get_event_history(limit: int = 50, natural_key: Optional[str] = None):
    """Returns immutable CDC event history entries."""
    conn = get_connection(DB_PATH)
    cursor = conn.cursor()
    if natural_key:
        cursor.execute(
            "SELECT * FROM event_history WHERE natural_key = ? ORDER BY event_timestamp DESC LIMIT ?",
            (natural_key, limit)
        )
    else:
        cursor.execute("SELECT * FROM event_history ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.post("/api/generate-sample")
def api_generate_sample(count: int = 100):
    """Generates reproducible sample data on the fly."""
    from scripts.generate_data import generate_dataset
    from src.config import SAMPLE_DATA_FILE
    out_file = generate_dataset(count=count, output_path=SAMPLE_DATA_FILE, include_anomalies=True)
    return {"status": "SUCCESS", "records_generated": count, "file": str(out_file.name)}

