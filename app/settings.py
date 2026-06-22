from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
ORIGINALS_DIR = DATA_DIR / "originals"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORTS_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "receipts.sqlite3"
TEST_INPUT_DIR = ROOT_DIR / "test-input"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}
TOTAL_TOLERANCE = 0.03
EXTRACTION_VERSION = "qwen3-vl-adaptive-chunks-v2"


def ensure_data_dirs() -> None:
    for path in (DATA_DIR, ORIGINALS_DIR, PROCESSED_DIR, EXPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
