"""Configuration settings for the Incremental Ingestion Framework."""

from pathlib import Path
import os

# Project Root Directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Core Paths
DATA_DIR = PROJECT_ROOT / "data"
SOURCE_DATA_DIR = DATA_DIR / "source"
SAMPLE_DATA_FILE = SOURCE_DATA_DIR / "sample_data.json"
GENERATED_DATA_DIR = SOURCE_DATA_DIR / "generated_data"

OUTPUT_DIR = DATA_DIR / "output"
PROCESSED_DIR = OUTPUT_DIR / "processed"
REPORTS_DIR = OUTPUT_DIR / "reports"

DATABASE_DIR = PROJECT_ROOT / "database"
DEFAULT_DB_PATH = DATABASE_DIR / "ingestion.db"

# Database Configuration
DB_PATH = Path(os.getenv("INGESTION_DB_PATH", str(DEFAULT_DB_PATH)))

# Execution Defaults
DEFAULT_BATCH_SIZE = int(os.getenv("DEFAULT_BATCH_SIZE", "5000"))
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEFAULT_SEED = int(os.getenv("RANDOM_SEED", "42"))
DEFAULT_LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))  # 7-day incremental lookback window

# Ensure mandatory directories exist
for path in [SOURCE_DATA_DIR, GENERATED_DATA_DIR, PROCESSED_DIR, REPORTS_DIR, DATABASE_DIR]:
    path.mkdir(parents=True, exist_ok=True)
