from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.exporters import export_receipt_bundle
from app.extraction import validate_receipt
from app.image_regions import create_manual_line_item_chunks
from app.models import LineItem, ReceiptJSON, ReceiptStatus, Transaction, Vendor
from app.pipeline import process_image
from app.qbo import sync_receipt_to_qbo
from app.settings import ROOT_DIR, TEST_INPUT_DIR, ensure_data_dirs
from app.vlm import VLMExtractionError, extract_line_items_with_vlm_or_raise


app = FastAPI(title="Local Receipt to QuickBooks Importer")
templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")


@app.on_event("startup")
def startup() -> None:
    ensure_data_dirs()
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, show: str = "latest") -> HTMLResponse:
    show_all = show == "all"
    receipts = db.list_receipts(latest_per_source=not show_all)
    version_counts = {receipt.source_hash: db.count_receipt_versions(receipt.source_hash) for receipt in receipts}
    test_images = sorted(TEST_INPUT_DIR.glob("*.jpg"))
    return templates.TemplateResponse(
        request,
        "index.html",
        {"receipts": receipts, "test_images": test_images, "show_all": show_all, "version_counts": version_counts},
    )


@app.get("/test-input/{filename}/preview", response_class=HTMLResponse)
def test_input_preview(request: Request, filename: str) -> HTMLResponse:
    image_path = _test_image_or_404(filename)
    return templates.TemplateResponse(request, "preview.html", {"image": image_path})


@app.get("/test-input/{filename}/image")
def test_input_image(filename: str) -> FileResponse:
    image_path = _test_image_or_404(filename)
    return FileResponse(image_path, filename=image_path.name)


@app.post("/test-input/{filename}/process")
def process_test_image_from_preview(filename: str) -> JSONResponse:
    image_path = _test_image_or_404(filename)
    result = process_image(image_path)
    return JSONResponse({"receipt_id": result.receipt_id, "redirect_url": f"/receipts/{result.receipt_id}"})


@app.post("/upload")
async def upload_receipt(file: Annotated[UploadFile, File()]) -> RedirectResponse:
    ensure_data_dirs()
    tmp_path = ROOT_DIR / "data" / f"upload-{file.filename}"
    contents = await file.read()
    tmp_path.write_bytes(contents)
    try:
        result = process_image(tmp_path, original_filename=file.filename)
    finally:
        tmp_path.unlink(missing_ok=True)
    return RedirectResponse(f"/receipts/{result.receipt_id}", status_code=303)


@app.post("/process-test")
def process_test_image(path: Annotated[str, Form()]) -> RedirectResponse:
    image_path = Path(path)
    if not image_path.is_file() or TEST_INPUT_DIR not in image_path.parents:
        raise HTTPException(status_code=400, detail="Invalid test input path.")
    result = process_image(image_path)
    return RedirectResponse(f"/receipts/{result.receipt_id}", status_code=303)


@app.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_detail(request: Request, receipt_id: int) -> HTMLResponse:
    record = _get_or_404(receipt_id)
    return templates.TemplateResponse(request, "receipt.html", {"record": record})


@app.get("/receipts/{receipt_id}/image")
def original_image(receipt_id: int) -> FileResponse:
    record = _get_or_404(receipt_id)
    return FileResponse(record.original_path, filename=record.original_filename)


@app.get("/receipts/{receipt_id}/json")
def receipt_json(receipt_id: int) -> ReceiptJSON:
    return _get_or_404(receipt_id).extracted_json


@app.post("/receipts/{receipt_id}/review")
def review_receipt(
    receipt_id: int,
    vendor_name: Annotated[str, Form()] = "",
    vendor_address: Annotated[str, Form()] = "",
    vendor_phone: Annotated[str, Form()] = "",
    vendor_tax_id: Annotated[str, Form()] = "",
    transaction_date: Annotated[str, Form()] = "",
    transaction_time: Annotated[str, Form()] = "",
    invoice_number: Annotated[str, Form()] = "",
    currency: Annotated[str, Form()] = "CAD",
    payment_method: Annotated[str, Form()] = "",
    subtotal: Annotated[float, Form()] = 0.0,
    tax: Annotated[float, Form()] = 0.0,
    tip: Annotated[float, Form()] = 0.0,
    total: Annotated[float, Form()] = 0.0,
    line_items_json: Annotated[str, Form()] = "[]",
    account_ref: Annotated[str, Form()] = "",
) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    try:
        line_items_payload = json.loads(line_items_json or "[]")
        line_items = [LineItem.model_validate(item) for item in line_items_payload]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid line item JSON: {exc}") from exc

    receipt = record.extracted_json
    receipt.vendor = Vendor(name=vendor_name, address=vendor_address, phone=vendor_phone, tax_id=vendor_tax_id)
    receipt.transaction = Transaction(
        date=transaction_date,
        time=transaction_time,
        invoice_number=invoice_number,
        currency=currency,
        payment_method=payment_method,
        subtotal=subtotal,
        tax=tax,
        tip=tip,
        total=total,
    )
    receipt.line_items = line_items
    receipt.quickbooks.account_ref = account_ref or None
    receipt = validate_receipt(receipt)
    status = ReceiptStatus.NEEDS_REVIEW if receipt.confidence.requires_review else ReceiptStatus.REVIEWED
    db.update_extracted_json(receipt_id, receipt, status)
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


@app.post("/receipts/{receipt_id}/approve")
def approve_receipt(receipt_id: int) -> RedirectResponse:
    _get_or_404(receipt_id)
    db.update_status(receipt_id, ReceiptStatus.REVIEWED)
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


@app.post("/receipts/{receipt_id}/rescan")
def rescan_receipt(receipt_id: int) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    result = process_image(Path(record.original_path), original_filename=record.original_filename, force_new=True)
    return RedirectResponse(f"/receipts/{result.receipt_id}", status_code=303)


@app.post("/receipts/{receipt_id}/prelim-rescan")
def preliminary_rescan_receipt(receipt_id: int) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    result = process_image(
        Path(record.original_path),
        original_filename=record.original_filename,
        force_new=True,
        use_preliminary_layout=True,
    )
    return RedirectResponse(f"/receipts/{result.receipt_id}", status_code=303)


@app.post("/receipts/{receipt_id}/manual-line-items")
def manual_line_items(
    receipt_id: int,
    crop_x: Annotated[int, Form()],
    crop_y: Annotated[int, Form()],
    crop_width: Annotated[int, Form()],
    crop_height: Annotated[int, Form()],
    split_count: Annotated[int, Form()] = 3,
) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    if crop_width < 20 or crop_height < 20:
        raise HTTPException(status_code=400, detail="Selected crop is too small.")

    chunks = create_manual_line_item_chunks(
        Path(record.original_path),
        receipt_id,
        x=crop_x,
        y=crop_y,
        width=crop_width,
        height=crop_height,
        split_count=split_count,
        ocr_result=record.ocr_result,
    )
    try:
        line_items, warnings = extract_line_items_with_vlm_or_raise(chunks)
    except VLMExtractionError as exc:
        receipt = record.extracted_json
        receipt.confidence.warnings = _line_item_retry_warnings(receipt.confidence.warnings)
        receipt.confidence.warnings.append(f"Manual line-item crop failed: {exc}")
        receipt.confidence.requires_review = True
        db.update_extracted_json(receipt_id, receipt, ReceiptStatus.NEEDS_REVIEW)
        return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)

    receipt = record.extracted_json
    receipt.line_items = line_items
    receipt.confidence.requires_review = True
    receipt.confidence.warnings = _line_item_retry_warnings(receipt.confidence.warnings)
    receipt.confidence.warnings.extend(warning for warning in warnings if warning not in receipt.confidence.warnings)
    receipt.confidence.warnings.append(
        f"Line items replaced from manual crop using {len(chunks)} focused chunk{'s' if len(chunks) != 1 else ''}."
    )
    receipt = validate_receipt(receipt)
    db.update_extracted_json(receipt_id, receipt, ReceiptStatus.NEEDS_REVIEW)
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


@app.post("/receipts/{receipt_id}/export")
def export_receipt(receipt_id: int) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    export_receipt_bundle(record)
    db.update_status(receipt_id, ReceiptStatus.EXPORTED)
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


@app.get("/receipts/{receipt_id}/export")
def download_export(receipt_id: int) -> FileResponse:
    record = _get_or_404(receipt_id)
    export_dir = export_receipt_bundle(record)
    json_path = export_dir / "receipt.json"
    return FileResponse(json_path, filename=f"receipt-{receipt_id}.json")


@app.post("/receipts/{receipt_id}/sync")
def sync_receipt(receipt_id: int) -> RedirectResponse:
    record = _get_or_404(receipt_id)
    result = sync_receipt_to_qbo(record)
    status = ReceiptStatus.SYNCED if result.get("status") == "synced" else ReceiptStatus.SYNC_FAILED
    db.update_status(receipt_id, status, result)
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


def _get_or_404(receipt_id: int):
    record = db.get_receipt(receipt_id)
    if not record:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return record


def _test_image_or_404(filename: str) -> Path:
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Test image not found.")
    image_path = TEST_INPUT_DIR / filename
    if not image_path.is_file() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=404, detail="Test image not found.")
    return image_path


def _line_item_retry_warnings(warnings: list[str]) -> list[str]:
    stale_prefixes = (
        "No readable line items were extracted.",
        "VLM line-item chunk",
        "VLM line-item chunks succeeded:",
        "Manual VLM line-item chunks succeeded:",
        "Manual line-item crop failed:",
        "Line items replaced from manual crop",
    )
    return [warning for warning in warnings if not warning.startswith(stale_prefixes)]
