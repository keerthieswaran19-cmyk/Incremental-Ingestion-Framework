"""Synthetic data generator producing reproducible, dirty, multi-scenario datasets."""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import GENERATED_DATA_DIR, SAMPLE_DATA_FILE


def generate_dataset(
    count: int = 1000,
    seed: int = 42,
    output_path: Path = SAMPLE_DATA_FILE,
    include_anomalies: bool = True,
) -> Path:
    """Generates synthetic dataset with deterministic pseudo-random seeds."""
    random.seed(seed)
    base_time = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)

    records = []
    # Natural keys pool for creating updates, deletes, duplicates, and late arrivals
    entity_keys = [f"CUST_{i:06d}" for i in range(1, max(10, int(count * 0.6)))]
    active_keys = set(entity_keys[:5])

    print(f"Generating {count} synthetic records (seed={seed}, anomalies={include_anomalies})...")

    # Ratio breakdown
    anomaly_budget = int(count * 0.08) if include_anomalies else 0
    duplicate_budget = int(count * 0.05)
    delete_budget = int(count * 0.05)
    late_budget = int(count * 0.05)

    current_sim_time = base_time

    for i in range(count):
        current_sim_time += timedelta(seconds=random.randint(5, 60))
        iso_time = current_sim_time.isoformat()

        # 1. Inject Anomalies if budget permits
        if anomaly_budget > 0 and random.random() < 0.08:
            anomaly_budget -= 1
            anomaly_type = random.choice([
                "missing_key",
                "invalid_timestamp",
                "invalid_ref_key",
                "negative_amount",
                "malformed_structure",
            ])

            if anomaly_type == "missing_key":
                records.append({
                    "natural_key": None,
                    "name": "Missing Key User",
                    "updated_at": iso_time,
                    "operation_type": "INSERT",
                    "amount": 100.0,
                })
            elif anomaly_type == "invalid_timestamp":
                records.append({
                    "natural_key": f"ERR_{i}",
                    "name": "Corrupt Time User",
                    "updated_at": "INVALID_DATE_TIME_STRING_999",
                    "operation_type": "INSERT",
                    "amount": 250.0,
                })
            elif anomaly_type == "invalid_ref_key":
                records.append({
                    "natural_key": f"CUST_{random.choice(entity_keys)}",
                    "name": "Bad Ref User",
                    "updated_at": iso_time,
                    "operation_type": "INSERT",
                    "account_id": "INVALID-REF-9999",  # Should start with ACC-
                    "amount": 50.0,
                })
            elif anomaly_type == "negative_amount":
                records.append({
                    "natural_key": f"CUST_{random.choice(entity_keys)}",
                    "name": "Negative Amount User",
                    "updated_at": iso_time,
                    "operation_type": "INSERT",
                    "account_id": "ACC-12345",
                    "amount": -500.0,  # Negative amount rejected
                })
            elif anomaly_type == "malformed_structure":
                records.append("NOT_A_VALID_JSON_OBJECT")
            continue

        # 2. Inject Late-Arriving / Out-of-Order Records
        if late_budget > 0 and active_keys and random.random() < 0.07:
            late_budget -= 1
            target_key = random.choice(list(active_keys))
            # Timestamp deliberately 5 days in the PAST
            late_time = (current_sim_time - timedelta(days=5, hours=random.randint(1, 10))).isoformat()
            records.append({
                "natural_key": target_key,
                "name": f"Late Arriving Update for {target_key}",
                "updated_at": late_time,
                "operation_type": "UPDATE",
                "account_id": f"ACC-{random.randint(1000, 9999)}",
                "amount": round(random.uniform(10.0, 500.0), 2),
                "is_late": True,
            })
            continue

        # 3. Inject Deletes / Tombstones
        if delete_budget > 0 and len(active_keys) > 5 and random.random() < 0.06:
            delete_budget -= 1
            delete_key = random.choice(list(active_keys))
            records.append({
                "natural_key": delete_key,
                "name": f"Tombstone for {delete_key}",
                "updated_at": iso_time,
                "operation_type": "DELETE",
                "account_id": f"ACC-{random.randint(1000, 9999)}",
                "amount": 0.0,
            })
            continue

        # 4. Inject Duplicate Events
        if duplicate_budget > 0 and len(records) > 0 and random.random() < 0.05:
            duplicate_budget -= 1
            # Duplicate the last normal record exactly
            dup_target = records[-1]
            if isinstance(dup_target, dict):
                records.append(dict(dup_target))
                continue

        # 5. Normal Record (Insert or Update)
        key = random.choice(entity_keys)
        active_keys.add(key)
        op_type = "UPDATE" if key in active_keys and random.random() < 0.3 else "INSERT"

        records.append({
            "natural_key": key,
            "entity_type": "CUSTOMER",
            "name": f"Customer {key}",
            "email": f"{key.lower()}@example.com",
            "updated_at": iso_time,
            "operation_type": op_type,
            "account_id": f"ACC-{random.randint(1000, 9999)}",
            "tier": random.choice(["STANDARD", "PREMIUM", "ENTERPRISE"]),
            "amount": round(random.uniform(10.0, 2500.0), 2),
        })

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Successfully generated {len(records)} records at: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Synthetic Data Generator for Incremental Ingestion")
    parser.add_argument("--count", type=int, default=100, help="Number of records to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--output", type=str, default=str(SAMPLE_DATA_FILE), help="Output file path")
    parser.add_argument("--clean-only", action="store_true", help="Exclude anomalies")
    args = parser.parse_args()

    out_file = Path(args.output)
    generate_dataset(
        count=args.count,
        seed=args.seed,
        output_path=out_file,
        include_anomalies=not args.clean_only,
    )


if __name__ == "__main__":
    main()
