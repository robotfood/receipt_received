# Local Receipt to QuickBooks Import

Local FastAPI app for turning receipt/invoice photos into reviewed, itemized bookkeeping data. The app keeps extraction local, uses Ollama vision models for structured extraction, stores review state in SQLite, and exports QuickBooks-shaped files.

See [specification.md](specification.md) for the full product and extraction specification, including benchmark schemas.

## Current Features

- Preview receipt images before processing.
- Upload JPG/JPEG/PNG/HEIC receipt images.
- Preserve original image unchanged.
- Run staged local VLM extraction with focused summary, line-item, and totals crops.
- Run an optional preliminary OCR/layout pass that detects receipt outline and major regions.
- Show preliminary layout overlays in the review UI.
- Manually select the itemized-list crop and rerun line-item extraction only.
- Edit vendor, transaction, totals, and line items.
- Validate missing fields and inconsistent totals.
- Export reviewed receipt data as JSON/CSV/QBO-shaped files.
- Store duplicate-aware receipt versions by source image hash and extraction version.

## Requirements

- Python 3.11+
- `uv`
- Ollama running locally
- A local multimodal model installed in Ollama

Default model:

```text
hf.co/unsloth/Qwen3-VL-8B-Instruct-GGUF:UD-Q4_K_XL
```

Install/pull the model in Ollama before processing receipts.

## Install

```bash
uv sync
```

Recommended on Apple Silicon if you want the preliminary OCR/layout pass to use Apple Vision:

```bash
uv sync --extra ocr-apple
```

Optional cross-platform OCR fallback:

```bash
uv sync --extra ocr-paddle
```

## Run The Web App

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Open:

```text
http://127.0.0.1:8001/
```

## Test Images

The app includes local test receipts under:

```text
test-input/
```

The landing page lists those images with thumbnails. Open one, preview it, then process it.

## Review Workflow

On a receipt review page, you can:

- `Rescan`: rerun the standard staged VLM scan and create a new receipt version.
- `Prelim pass + full scan`: run OCR/layout detection first, then full focused VLM extraction.
- `Select item list`: manually draw a crop around itemized rows, choose split count, and rerun line-item extraction only.
- `Approve`: mark the receipt reviewed.
- `Export`: create export files.
- `Sync to QuickBooks`: currently a stubbed/sandbox path depending on QBO configuration.
- `Download JSON`: download the reviewed receipt JSON.

## Runtime Tuning

Model and Ollama endpoint:

```bash
RECEIPT_VLM_MODEL=hf.co/unsloth/Qwen3-VL-8B-Instruct-GGUF:UD-Q4_K_XL
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Timeouts:

```bash
# Summary/totals pass timeout, seconds
RECEIPT_VLM_TIMEOUT=30

# Line-item chunk timeout, seconds
RECEIPT_VLM_LINE_ITEM_TIMEOUT=90
```

Line-item chunking:

```bash
# Override exact chunk count
RECEIPT_LINE_ITEM_CHUNKS=4

# Adaptive chunking knobs
RECEIPT_LINE_ITEM_TARGET_CHUNK_HEIGHT=210
RECEIPT_LINE_ITEM_MIN_CHUNKS=2
RECEIPT_LINE_ITEM_MAX_CHUNKS=5
RECEIPT_LINE_ITEM_CHUNK_OVERLAP=0.12
```

VLM crop image size:

```bash
RECEIPT_VLM_REGION_MAX_SIDE=768
```

## CLI Processing

Process all images in `test-input/`:

```bash
uv run python process_receipts.py
```

Process one image:

```bash
uv run python process_receipts.py test-input/20260528_1207371.jpg
```

## Output Schema

The primary extracted receipt JSON schema is documented in [specification.md](specification.md#receipt-json-output-schema).

High-level shape:

```json
{
  "document_type": "receipt_or_invoice",
  "vendor": {
    "name": "",
    "address": "",
    "phone": "",
    "tax_id": ""
  },
  "transaction": {
    "date": "YYYY-MM-DD",
    "time": "HH:MM:SS",
    "invoice_number": "",
    "currency": "CAD",
    "payment_method": "",
    "subtotal": 0.0,
    "tax": 0.0,
    "tip": 0.0,
    "total": 0.0
  },
  "line_items": [
    {
      "description": "",
      "item": "",
      "quantity": 1.0,
      "unit": "",
      "weight": null,
      "unit_price": 0.0,
      "amount": 0.0,
      "tax": 0.0,
      "tax_code": "",
      "taxable": true,
      "suggested_qbo_account": "",
      "confidence": 0.0
    }
  ],
  "quickbooks": {
    "target_entity": "Purchase",
    "vendor_ref": null,
    "account_ref": null,
    "attach_original_image": true
  },
  "confidence": {
    "overall": 0.0,
    "requires_review": true,
    "warnings": []
  }
}
```

The preliminary image-layout schema is documented in [specification.md](specification.md#preliminary-image-layout-schema). Use it when benchmarking model ability to locate receipt outline, vendor/header, transaction fields, itemized list, and totals before extraction.

## Data Storage

Runtime files are written under `data/`:

- `data/originals/`: unchanged original images.
- `data/processed/`: OCR/VLM derivatives, region crops, and chunk crops.
- `data/receipts.sqlite3`: metadata, OCR output, preliminary layout output, extracted JSON, status, and sync result.
- `data/exports/`: exported receipt bundles.

## OCR Backends

If VLM extraction fails, or when running the preliminary layout pass, the app tries:

1. Apple Vision OCR on macOS, if installed through `uv sync --extra ocr-apple`.
2. PaddleOCR, if installed through `uv sync --extra ocr-paddle`.
3. Tesseract, if `pytesseract` and system `tesseract` are installed.

Without OCR, the standard staged VLM scan can still run. The preliminary layout pass will have weaker fallback behavior.

## QuickBooks Status

The app currently focuses on local extraction, review, and export. Direct QuickBooks Online sync is scaffolded but should be treated as later-stage until OAuth, token storage, sandbox validation, vendor/account mapping, and attachment upload are fully configured.
