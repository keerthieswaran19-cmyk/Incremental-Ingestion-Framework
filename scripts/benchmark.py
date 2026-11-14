"""Performance benchmarking script measuring 100,000+ record throughput and latency."""

import argparse
import json
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_data import generate_dataset
from src.config import GENERATED_DATA_DIR, REPORTS_DIR, DB_PATH
from src.database import init_database, get_connection
from src.pipeline import PipelineRunner
from src.analytics import ReconciliationEngine


def run_benchmark(record_count: int = 100000, seed: int = 42) -> Path:
    """Executes end-to-end benchmark on 100k+ records and outputs reports."""
    print("=" * 80)
    print(f"STARTING ENTERPRISE INGESTION BENCHMARK: {record_count:,} RECORDS")
    print("=" * 80)

    benchmark_file = GENERATED_DATA_DIR / f"benchmark_{record_count}_seed{seed}.json"

    # Step 1: Generate dataset if not already present
    if not benchmark_file.exists():
        print(f"Generating synthetic dataset with {record_count:,} records...")
        gen_start = time.perf_counter()
        generate_dataset(count=record_count, seed=seed, output_path=benchmark_file, include_anomalies=True)
        gen_time = time.perf_counter() - gen_start
        print(f"Data generation completed in {gen_time:.2f} seconds.")
    else:
        print(f"Using existing benchmark data file: {benchmark_file}")

    # Step 2: Initialize fresh benchmark database
    bench_db = DB_PATH.parent / "benchmark.db"
    if bench_db.exists():
        bench_db.unlink(missing_ok=True)
        # remove any wal/shm files
        Path(str(bench_db) + "-wal").unlink(missing_ok=True)
        Path(str(bench_db) + "-shm").unlink(missing_ok=True)

    init_database(bench_db)

    # Step 3: Execute Ingestion Pipeline
    runner = PipelineRunner(db_path=bench_db)
    print(f"\nExecuting ingestion pipeline on {benchmark_file.name}...")
    pipeline_start = time.perf_counter()
    summary = runner.run(benchmark_file, force_reprocess=True)
    pipeline_duration = time.perf_counter() - pipeline_start

    throughput = record_count / pipeline_duration if pipeline_duration > 0 else 0.0

    print("\n" + "=" * 80)
    print("BENCHMARK EXECUTION SUMMARY")
    print("=" * 80)
    print(f"Status:             {summary['status']}")
    print(f"Run ID:             {summary['run_id']}")
    print(f"Total Input:        {summary['input_count']:,} records")
    print(f"New Inserts:        {summary['inserted_count']:,}")
    print(f"Updates:            {summary['updated_count']:,}")
    print(f"Deletes:            {summary['deleted_count']:,}")
    print(f"Quarantined (DLQ):  {summary['quarantined_count']:,}")
    print(f"Duplicates Skipped: {summary['duplicate_count']:,}")
    print(f"Late Arrivals:      {summary['late_arriving_count']:,}")
    print(f"Execution Duration: {pipeline_duration:.2f} seconds ({summary['duration_ms']:.1f} ms)")
    print(f"Throughput:         {throughput:,.2f} records/sec")
    print("=" * 80)

    # Step 4: Perform Mathematical Reconciliation
    conn = get_connection(bench_db)
    engine = ReconciliationEngine(conn)
    recon = engine.verify_reconciliation(summary["run_id"])
    full_report = engine.generate_reconciliation_report(REPORTS_DIR / "reconciliation_report.json")
    conn.close()

    print(f"Reconciliation Status: {recon['status']}")
    print(f"Mathematical Conservation Equation Soundness: {recon['is_mathematically_sound']}")

    # Save benchmark metrics artifact
    benchmark_results = {
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "record_count": record_count,
        "seed": seed,
        "duration_seconds": round(pipeline_duration, 3),
        "duration_ms": summary["duration_ms"],
        "records_per_second": round(throughput, 2),
        "metrics": summary,
        "reconciliation": recon,
        "target_table_statistics": full_report["target_statistics"],
    }

    result_path = REPORTS_DIR / "benchmark_results.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2)

    print(f"\nBenchmark artifacts saved to:\n  - {result_path}\n  - {REPORTS_DIR / 'reconciliation_report.json'}")
    return result_path


def main():
    parser = argparse.ArgumentParser(description="Performance Benchmark for Ingestion Framework")
    parser.add_argument("--count", type=int, default=100000, help="Number of records to benchmark")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    run_benchmark(record_count=args.count, seed=args.seed)


if __name__ == "__main__":
    main()
