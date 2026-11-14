"""Pydantic schemas for REST API endpoints."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class IngestionRequest(BaseModel):
    """Request payload to trigger an ingestion run."""
    source_file: str = Field(..., description="File path relative to data/source or absolute path")
    entity_name: str = Field(default="CUSTOMER", description="Target entity domain")
    force_reprocess: bool = Field(default=False, description="Bypass file idempotency registry check")
    incremental: bool = Field(default=True, description="Filter records below current high watermark")


class RunSummaryResponse(BaseModel):
    """Summary metrics of a pipeline execution."""
    run_id: str
    status: str
    source_file: Optional[str] = None
    input_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    quarantined_count: int = 0
    duplicate_count: int = 0
    late_arriving_count: int = 0
    duration_ms: Optional[float] = None
    records_per_sec: Optional[float] = None
    watermark_start: Optional[str] = None
    watermark_end: Optional[str] = None
    error_message: Optional[str] = None


class QuarantineItem(BaseModel):
    """Dead-letter queue record details."""
    quarantine_id: str
    source_file: str
    raw_payload: str
    error_code: str
    error_message: str
    occurrence_count: int = 1
    replayed: int
    run_id: str
    created_at: str


class WatermarkItem(BaseModel):
    """High watermark state for an entity."""
    entity_name: str
    high_watermark: str
    updated_at: str
    last_run_id: str


class DLQReplayRequest(BaseModel):
    """Payload for replaying a quarantined DLQ record."""
    corrected_payload: Optional[Dict[str, Any]] = Field(default=None, description="Optional corrected JSON payload")
