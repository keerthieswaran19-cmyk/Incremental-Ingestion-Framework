"""Analytics and mathematical reconciliation module for pipeline verification."""

import json
import sqlite3
from typing import Any, Dict, List, Optional
from pathlib import Path


class ReconciliationEngine:
    """Verifies pipeline mathematical invariants and produces audit summaries."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_run_metrics(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves metrics for a specific run or all runs."""
        cursor = self.conn.cursor()
        if run_id:
            cursor.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,))
        else:
            cursor.execute("SELECT * FROM pipeline_runs ORDER BY start_time DESC LIMIT 50")
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def get_table_counts(self) -> Dict[str, int]:
        """Returns row counts across all pipeline tables."""
        cursor = self.conn.cursor()
        tables = [
            "source_events",
            "target_records",
            "event_history",
            "quarantine",
            "pipeline_runs",
            "watermark_state",
            "file_registry",
        ]
        counts = {}
        for tbl in tables:
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {tbl}")
            counts[tbl] = cursor.fetchone()["cnt"]
        cursor.close()
        return counts

    def verify_reconciliation(self, run_id: str) -> Dict[str, Any]:
        """
        Validates the Conservation of Data equation for a run:
        Input == Inserted + Updated + Deleted + Quarantined + Duplicates
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,))
        run = cursor.fetchone()
        cursor.close()

        if not run:
            return {"status": "ERROR", "message": f"Run ID {run_id} not found."}

        input_cnt = run["input_count"]
        ins_cnt = run["inserted_count"]
        upd_cnt = run["updated_count"]
        del_cnt = run["deleted_count"]
        quar_cnt = run["quarantined_count"]
        dup_cnt = run["duplicate_count"]

        sum_components = ins_cnt + upd_cnt + del_cnt + quar_cnt + dup_cnt
        discrepancy = input_cnt - sum_components
        is_balanced = discrepancy == 0

        result = {
            "run_id": run_id,
            "status": "BALANCED" if is_balanced else "DISCREPANCY_DETECTED",
            "conservation_equation": "input_count == (inserted + updated + deleted + quarantined + duplicate)",
            "metrics": {
                "input_count": input_cnt,
                "inserted_count": ins_cnt,
                "updated_count": upd_cnt,
                "deleted_count": del_cnt,
                "quarantined_count": quar_cnt,
                "duplicate_count": dup_cnt,
                "sum_of_components": sum_components,
                "discrepancy": discrepancy,
            },
            "is_mathematically_sound": is_balanced,
        }
        return result

    def generate_reconciliation_report(self, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """Generates a full audit reconciliation report across all historical runs."""
        table_counts = self.get_table_counts()
        recent_runs = self.get_run_metrics()

        run_verifications = []
        for r in recent_runs:
            run_verifications.append(self.verify_reconciliation(r["run_id"]))

        # Target statistics
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as active_count FROM target_records WHERE is_deleted = 0")
        active_target = cursor.fetchone()["active_count"]

        cursor.execute("SELECT COUNT(*) as tombstone_count FROM target_records WHERE is_deleted = 1")
        tombstone_target = cursor.fetchone()["tombstone_count"]

        cursor.execute("SELECT high_watermark, entity_name FROM watermark_state")
        watermarks = [dict(w) for w in cursor.fetchall()]
        cursor.close()

        report = {
            "summary": "Full Ingestion Framework Reconciliation Report",
            "table_counts": table_counts,
            "target_statistics": {
                "active_records": active_target,
                "tombstoned_records": tombstone_target,
                "total_target_records": active_target + tombstone_target,
            },
            "watermarks": watermarks,
            "recent_runs_reconciliation": run_verifications,
        }

        if output_path:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)

        return report
