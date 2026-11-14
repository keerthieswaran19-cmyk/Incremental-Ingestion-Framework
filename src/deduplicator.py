"""Deduplication module for in-batch windowing and cross-batch state deduplication."""

from typing import Dict, List, Tuple
from src.transformer import TransformedRecord


class Deduplicator:
    """Handles in-batch window deduplication and duplicate detection."""

    @staticmethod
    def deduplicate_batch(records: List[TransformedRecord]) -> Tuple[List[TransformedRecord], int]:
        """
        Deduplicates records within a single batch.
        If multiple events exist for the same natural_key:
        - Keeps the event with the latest `updated_at`.
        - If timestamps are equal, keeps the last occurrence.
        Returns (deduplicated_list, duplicate_count).
        """
        if not records:
            return [], 0

        seen_map: Dict[str, TransformedRecord] = {}
        duplicates_removed = 0

        for rec in records:
            key = rec.natural_key
            if key in seen_map:
                existing = seen_map[key]
                # Compare timestamps
                if rec.updated_at >= existing.updated_at:
                    seen_map[key] = rec
                duplicates_removed += 1
            else:
                seen_map[key] = rec

        return list(seen_map.values()), duplicates_removed
