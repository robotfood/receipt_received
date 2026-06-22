from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app import db
from app.extraction import extract_receipt_json, validate_receipt
from app.image_regions import create_receipt_regions, create_receipt_regions_from_layout
from app.layout import detect_preliminary_layout
from app.models import OCRResult, ReceiptCreateResult, ReceiptJSON, ReceiptStatus
from app.ocr import run_ocr
from app.preprocess import auto_rotate_image_if_needed, preprocess_image, preprocess_image_for_vlm
from app.settings import ALLOWED_IMAGE_EXTENSIONS, EXTRACTION_VERSION, ORIGINALS_DIR
from app.vlm import VLMExtractionError, extract_receipt_staged_with_vlm_or_raise


def process_image(
    source_path: Path,
    original_filename: str | None = None,
    *,
    force_new: bool = False,
    use_preliminary_layout: bool = False,
) -> ReceiptCreateResult:
    suffix = source_path.suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: {suffix}")

    source_hash = db.file_sha256(source_path)
    extraction_version = f"{EXTRACTION_VERSION}+prelim-layout-v1" if use_preliminary_layout else EXTRACTION_VERSION
    existing = db.find_receipt_by_hash_and_version(source_hash, extraction_version)
    if existing and not force_new:
        return ReceiptCreateResult(
            receipt_id=existing.id,
            status=existing.status,
            extracted_json=existing.extracted_json,
            reused_existing=True,
        )

    token = uuid4().hex
    filename = original_filename or source_path.name
    stored_original = ORIGINALS_DIR / f"{token}{suffix if suffix != '.heic' else '.jpg'}"
    shutil.copy2(source_path, stored_original)
    auto_rotate_image_if_needed(stored_original)

    processed = preprocess_image(stored_original, token)
    processed_vlm = preprocess_image_for_vlm(stored_original, token)
    layout_result = None
    ocr_result: OCRResult | None = None
    if use_preliminary_layout:
        ocr_result = run_ocr(processed_vlm)
        layout_result = detect_preliminary_layout(processed_vlm, ocr_result)

    try:
        regions = (
            create_receipt_regions_from_layout(processed_vlm, token, layout_result, ocr_result)
            if layout_result
            else create_receipt_regions(processed_vlm, token)
        )
        extracted = extract_receipt_staged_with_vlm_or_raise(regions)
        if layout_result and _is_failed_preliminary_vlm_result(extracted):
            raise VLMExtractionError("Preliminary layout scan produced no vendor and no line items.")
        if _needs_ocr_backfill(extracted):
            ocr_result = ocr_result or run_ocr(processed_vlm)
            extracted = _backfill_missing_fields_from_ocr(extracted, ocr_result)
            backfill_warnings = list(extracted.confidence.warnings)
            extracted = validate_receipt(extracted)
            extracted.confidence.warnings = _merge_warnings(extracted.confidence.warnings, backfill_warnings)
            extracted.confidence.requires_review = True
        if layout_result:
            extracted.confidence.warnings.append(
                "Preliminary OCR/layout pass selected receipt regions before VLM extraction."
            )
        elif not layout_result:
            ocr_result = OCRResult(
                engine="ollama_vlm_staged",
                raw_text="",
                warnings=["OCR was bypassed. Extraction used staged image-region passes with a multimodal local model."],
            )
    except VLMExtractionError as exc:
        ocr_result = ocr_result or run_ocr(processed)
        extracted = extract_receipt_json(ocr_result)
        extracted.confidence.warnings.insert(
            0,
            f"Multimodal model failed; fell back to OCR/rules extraction. Reason: {exc}",
        )
    status = ReceiptStatus.NEEDS_REVIEW if extracted.confidence.requires_review else ReceiptStatus.EXTRACTED

    receipt_id = db.create_receipt(
        source_hash=source_hash,
        extraction_version=extraction_version,
        original_filename=filename,
        original_path=stored_original,
        processed_path=processed,
        ocr_result=ocr_result,
        layout_result=layout_result,
        extracted_json=extracted,
        status=status,
    )
    return ReceiptCreateResult(receipt_id=receipt_id, status=status, extracted_json=extracted)


def _is_failed_preliminary_vlm_result(extracted) -> bool:
    vendor_missing = not extracted.vendor.name.strip()
    items_missing = not extracted.line_items
    timed_out = any("timed out" in warning.lower() for warning in extracted.confidence.warnings)
    no_line_chunks = any("line-item chunks succeeded: 0/" in warning.lower() for warning in extracted.confidence.warnings)
    return vendor_missing and items_missing and (timed_out or no_line_chunks)


def _needs_ocr_backfill(extracted: ReceiptJSON) -> bool:
    missing_core_fields = (
        not extracted.vendor.name.strip()
        or not extracted.transaction.date
        or extracted.transaction.total <= 0
    )
    vlm_struggled = any(
        phrase in warning.lower()
        for warning in extracted.confidence.warnings
        for phrase in ("timed out", "summary pass failed", "totals pass failed")
    )
    return missing_core_fields and vlm_struggled


def _backfill_missing_fields_from_ocr(extracted: ReceiptJSON, ocr_result: OCRResult) -> ReceiptJSON:
    ocr_extracted = extract_receipt_json(ocr_result)
    changed: list[str] = []

    if not extracted.vendor.name.strip() and ocr_extracted.vendor.name.strip():
        extracted.vendor = ocr_extracted.vendor
        changed.append("vendor")
    else:
        if not extracted.vendor.address and ocr_extracted.vendor.address:
            extracted.vendor.address = ocr_extracted.vendor.address
            changed.append("vendor address")
        if not extracted.vendor.phone and ocr_extracted.vendor.phone:
            extracted.vendor.phone = ocr_extracted.vendor.phone
            changed.append("vendor phone")
        if not extracted.vendor.tax_id and ocr_extracted.vendor.tax_id:
            extracted.vendor.tax_id = ocr_extracted.vendor.tax_id
            changed.append("vendor tax ID")

    if not extracted.transaction.date and ocr_extracted.transaction.date:
        extracted.transaction.date = ocr_extracted.transaction.date
        changed.append("date")
    if not extracted.transaction.payment_method and ocr_extracted.transaction.payment_method:
        extracted.transaction.payment_method = ocr_extracted.transaction.payment_method
        changed.append("payment method")
    if extracted.transaction.subtotal <= 0 and ocr_extracted.transaction.subtotal > 0:
        extracted.transaction.subtotal = ocr_extracted.transaction.subtotal
        changed.append("subtotal")
    if extracted.transaction.tax <= 0 and ocr_extracted.transaction.tax > 0:
        extracted.transaction.tax = ocr_extracted.transaction.tax
        changed.append("tax")
    if extracted.transaction.total <= 0 and ocr_extracted.transaction.total > 0:
        extracted.transaction.total = ocr_extracted.transaction.total
        changed.append("total")

    if changed:
        extracted.confidence.warnings.append(f"OCR backfilled missing fields: {', '.join(changed)}.")
    return extracted


def _merge_warnings(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for warning in [*primary, *secondary]:
        if warning and warning not in merged:
            merged.append(warning)
    return merged
