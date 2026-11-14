"""Watermark management module for tracking and advancing incremental high watermarks."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional


class WatermarkManager:
    """Manages incremental high watermarks with transaction safety."""

    DEFAULT_WATERMARK = "1970-01-01T00:00:00+00:00"

    @classmethod
    def get_high_watermark(cls, conn: sqlite3.Connection, entity_name: str) -> str:
        """Retrieves the current committed high-watermark for an entity."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT high_watermark FROM watermark_state WHERE entity_name = ?",
            (entity_name.upper(),),
        )
        row = cursor.fetchone()
        cursor.close()

        if row and row["high_watermark"]:
            return row["high_watermark"]
        return cls.DEFAULT_WATERMARK

    @classmethod
    def advance_watermark(
        cls,
        cursor: sqlite3.Cursor,
        entity_name: str,
        new_watermark: str,
        run_id: str,
    ) -> None:
        """
        Advances the high watermark within an active transaction.
        Only advances if new_watermark > current_high_watermark.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_entity = entity_name.upper()

        cursor.execute(
            """
            INSERT INTO watermark_state (entity_name, high_watermark, updated_at, last_run_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_name) DO UPDATE SET
                high_watermark = CASE
                    WHEN excluded.high_watermark > watermark_state.high_watermark THEN excluded.high_watermark
                    ELSE watermark_state.high_watermark
                END,
                updated_at = excluded.updated_at,
                last_run_id = excluded.last_run_id;
            """,
            (clean_entity, new_watermark, now_iso, run_id),
        )
