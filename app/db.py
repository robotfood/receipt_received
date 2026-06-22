from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import OCRResult, PreliminaryLayout, ReceiptJSON, ReceiptRecord, ReceiptStatus
from app.settings import DB_PATH, EXTRACTION_VERSION, ensure_data_dirs


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash TEXT NOT NULL DEFAULT '',
                extraction_version TEXT NOT NULL DEFAULT '',
                original_filename TEXT NOT NULL,
                original_path TEXT NOT NULL,
                processed_path TEXT,
                ocr_json TEXT,
                layout_json TEXT,
                extracted_json TEXT NOT NULL,
                status TEXT NOT NULL,
                qbo_sync_result TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "source_hash", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "extraction_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "layout_json", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_source_version ON receipts(source_hash, extraction_version)"
        )
        _backfill_identity_columns(conn)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)").fetchall()}
    if name not in columns:
        conn.execute(f"ALTER TABLE receipts ADD COLUMN {name} {definition}")


def _backfill_identity_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, original_path FROM receipts WHERE source_hash = '' OR extraction_version = ''"
    ).fetchall()
    for row in rows:
        original_path = Path(row["original_path"])
        source_hash = file_sha256(original_path) if original_path.exists() else ""
        conn.execute(
            """
            UPDATE receipts
            SET source_hash = CASE WHEN source_hash = '' THEN ? ELSE source_hash END,
                extraction_version = CASE WHEN extraction_version = '' THEN ? ELSE extraction_version END
            WHERE id = ?
            """,
            (source_hash, "legacy-import", row["id"]),
        )


def _row_to_record(row: sqlite3.Row) -> ReceiptRecord:
    ocr_payload = json.loads(row["ocr_json"]) if row["ocr_json"] else None
    layout_payload = json.loads(row["layout_json"]) if "layout_json" in row.keys() and row["layout_json"] else None
    return ReceiptRecord(
        id=row["id"],
        source_hash=row["source_hash"],
        extraction_version=row["extraction_version"],
        original_filename=row["original_filename"],
        original_path=row["original_path"],
        processed_path=row["processed_path"],
        ocr_result=OCRResult.model_validate(ocr_payload) if ocr_payload else None,
        layout_result=PreliminaryLayout.model_validate(layout_payload) if layout_payload else None,
        extracted_json=ReceiptJSON.model_validate(json.loads(row["extracted_json"])),
        status=ReceiptStatus(row["status"]),
        qbo_sync_result=json.loads(row["qbo_sync_result"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_receipt(
    *,
    source_hash: str,
    extraction_version: str,
    original_filename: str,
    original_path: Path,
    processed_path: Path | None,
    ocr_result: OCRResult,
    layout_result: PreliminaryLayout | None = None,
    extracted_json: ReceiptJSON,
    status: ReceiptStatus,
) -> int:
    now = utc_now()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO receipts (
                source_hash,
                extraction_version,
                original_filename,
                original_path,
                processed_path,
                ocr_json,
                layout_json,
                extracted_json,
                status,
                qbo_sync_result,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                extraction_version,
                original_filename,
                str(original_path),
                str(processed_path) if processed_path else None,
                ocr_result.model_dump_json(),
                layout_result.model_dump_json() if layout_result else None,
                extracted_json.model_dump_json(),
                status.value,
                "{}",
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def list_receipts(*, latest_per_source: bool = False) -> list[ReceiptRecord]:
    with connect() as conn:
        if latest_per_source:
            rows = conn.execute(
                """
                SELECT r.*
                FROM receipts r
                JOIN (
                    SELECT source_hash, MAX(id) AS latest_id
                    FROM receipts
                    GROUP BY source_hash
                ) latest ON latest.latest_id = r.id
                ORDER BY r.created_at DESC, r.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM receipts ORDER BY created_at DESC, id DESC").fetchall()
    return [_row_to_record(row) for row in rows]


def count_receipt_versions(source_hash: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM receipts WHERE source_hash = ?", (source_hash,)).fetchone()
    return int(row["count"]) if row else 0


def find_receipt_by_hash_and_version(source_hash: str, extraction_version: str) -> ReceiptRecord | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM receipts
            WHERE source_hash = ? AND extraction_version = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_hash, extraction_version),
        ).fetchone()
    return _row_to_record(row) if row else None


def get_receipt(receipt_id: int) -> ReceiptRecord | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    return _row_to_record(row) if row else None


def update_extracted_json(receipt_id: int, extracted_json: ReceiptJSON, status: ReceiptStatus) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE receipts SET extracted_json = ?, status = ?, updated_at = ? WHERE id = ?",
            (extracted_json.model_dump_json(), status.value, utc_now(), receipt_id),
        )


def update_status(receipt_id: int, status: ReceiptStatus, qbo_sync_result: dict[str, Any] | None = None) -> None:
    fields = ["status = ?", "updated_at = ?"]
    values: list[Any] = [status.value, utc_now()]
    if qbo_sync_result is not None:
        fields.append("qbo_sync_result = ?")
        values.append(json.dumps(qbo_sync_result))
    values.append(receipt_id)
    with connect() as conn:
        conn.execute(f"UPDATE receipts SET {', '.join(fields)} WHERE id = ?", values)
