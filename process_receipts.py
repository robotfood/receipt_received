from __future__ import annotations

import argparse
from pathlib import Path

from app import db
from app.pipeline import process_image
from app.settings import TEST_INPUT_DIR, ensure_data_dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="Process local receipt images.")
    parser.add_argument("paths", nargs="*", type=Path, help="Receipt image paths. Defaults to test-input/*.jpg.")
    args = parser.parse_args()

    ensure_data_dirs()
    db.init_db()

    paths = args.paths or sorted(TEST_INPUT_DIR.glob("*.jpg"))
    if not paths:
        raise SystemExit("No input images found.")

    for path in paths:
        try:
            result = process_image(path)
        except Exception as exc:
            print(f"{path}: failed -> {type(exc).__name__}: {exc}")
            continue
        print(f"{path}: receipt #{result.receipt_id} -> {result.status.value}")
        print(result.extracted_json.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
