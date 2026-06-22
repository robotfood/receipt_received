# Local Receipt Image Extraction Specification

## Goal

Build a local Python application that accepts receipt or invoice photos, extracts structured bookkeeping data, lets the user review and correct the result, and exports QuickBooks-compatible records. The original image must be preserved and attached to the reviewed output.

The current application is optimized for grocery/vendor receipts where the important fields are:

- Supplier/vendor
- Date and time of purchase
- Method of payment
- Item name/description
- Weight and unit
- Quantity
- Unit cost
- Line cost
- Item tax and tax code
- Receipt subtotal, tax, tip, and total

Item-level tax and weight/unit parsing are expected to require human review when the receipt does not print them clearly.

## Target User

A small business owner or bookkeeper who photographs receipts and wants to reduce manual QuickBooks entry while keeping a human review step before export or sync.

## Current Workflow

1. User opens the local web app.
2. User previews a test receipt or uploads a receipt image.
3. App preserves the original image under `data/originals/`.
4. App creates OCR-friendly and VLM-friendly processed derivatives.
5. App runs staged local multimodal extraction through Ollama.
6. App normalizes the result into the strict receipt JSON schema.
7. App validates required fields, totals, line item sums, and review warnings.
8. User reviews the image beside editable receipt fields and line items.
9. User can approve, export, sync, rescan, run preliminary-layout rescan, or manually crop/retry the itemized list.
10. App stores metadata, OCR/layout output, extracted JSON, review status, and sync/export results in SQLite.

## Implemented Extraction Modes

### Standard Staged VLM Scan

The default scan uses a local Ollama vision model:

```text
hf.co/unsloth/Qwen3-VL-8B-Instruct-GGUF:UD-Q4_K_XL
```

The app creates fixed receipt regions:

- Summary/header crop
- Line-item crop
- Totals/payment crop

The line-item crop is split into smaller chunks before being sent to the VLM. Chunking is adaptive by image height and can be tuned with environment variables.

### Preliminary Layout Pass + Full Scan

The receipt review page includes a `Prelim pass + full scan` action.

This mode:

1. Runs a local OCR/layout pass.
2. Estimates the whole receipt outline.
3. Detects rough semantic regions:
   - `vendor`
   - `transaction`
   - `line_items`
   - `totals`
4. Saves the preliminary layout result.
5. Uses those regions to create focused VLM crops.
6. Runs the full staged VLM extraction.
7. Redirects to the new receipt version and shows region overlays in the UI.

OCR is used here mostly for layout and anchors, not as the final extraction authority.

### Manual Itemized Crop Retry

On the receipt review page, the user can select the itemized list area manually.

The app then:

1. Saves the selected crop.
2. Asks how many horizontal chunks to split it into.
3. Sends only those focused chunks to the VLM line-item prompt.
4. Replaces the current receipt line items in place.
5. Keeps vendor, transaction, totals, and payment fields unchanged.

## OCR Backends

The app tries local OCR backends in this order:

1. Apple Vision OCR on macOS, if `pyobjc-framework-Vision` is installed.
2. PaddleOCR, if `paddleocr` is installed.
3. Tesseract, if `pytesseract` and the system `tesseract` binary are installed.

For Apple Silicon development, Apple Vision is the preferred lightweight layout backend.

## Input Requirements

Supported image inputs:

- JPG
- JPEG
- PNG
- HEIC if conversion support is present

Not MVP:

- PDF ingestion
- Multi-page invoices
- Mobile capture app

Image handling requirements:

- Preserve original image unchanged.
- Correct EXIF orientation.
- Generate OCR-friendly derivative.
- Generate VLM-friendly color derivative.
- Resize derivatives for model/runtime limits.
- Prefer focused crops over sending background-heavy photos to the VLM.

## Receipt JSON Output Schema

This is the main structured extraction schema. Use this schema when benchmarking Google or other image-to-JSON models.

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

### Field Notes

- `vendor.tax_id` should contain business tax IDs such as `TPS#...`, `TVQ#...`, `GST#...`, or `QST#...`.
- `vendor.phone` should contain only phone-like values, not tax IDs.
- `transaction.date` must be normalized to `YYYY-MM-DD` when visible.
- `transaction.currency` defaults to `CAD`.
- `line_items[].amount` is the line total/cost.
- `line_items[].tax` should be item-level tax only when visible or confidently attributable.
- `line_items[].tax_code` should preserve printed tax code markers.
- `line_items[].weight` and `line_items[].unit` should preserve weighted-item information such as `1.245 kg`.
- `confidence.requires_review` should be true for low confidence, missing required fields, failed validations, or any inferred item tax.

## Preliminary Image Layout Schema

This schema describes the image regions detected before full extraction. It is useful for benchmarking whether a model can find the receipt boundary and key sections before extracting text.

```json
{
  "engine": "prelim_layout:apple_vision",
  "image_width": 1200,
  "image_height": 1800,
  "receipt_outline": {
    "name": "receipt_outline",
    "box": [100, 50, 900, 1750],
    "confidence": 0.75,
    "reason": "Padded bounding box around OCR text."
  },
  "regions": [
    {
      "name": "vendor",
      "box": [100, 50, 900, 420],
      "confidence": 0.45,
      "reason": "Top receipt band; expected vendor/header location."
    },
    {
      "name": "transaction",
      "box": [100, 80, 900, 520],
      "confidence": 0.65,
      "reason": "Anchored by date/payment/tax-id keywords."
    },
    {
      "name": "line_items",
      "box": [100, 420, 900, 1320],
      "confidence": 0.7,
      "reason": "Dense middle rows with prices/units."
    },
    {
      "name": "totals",
      "box": [100, 1320, 900, 1750],
      "confidence": 0.75,
      "reason": "Anchored by total/tax/payment keywords."
    }
  ],
  "warnings": []
}
```

Coordinate convention:

- `box` is `[x1, y1, x2, y2]`.
- Coordinates are pixel coordinates in the processed VLM image.
- Origin is top-left.
- `x2` and `y2` are the lower-right bounds.

Expected region names:

- `receipt_outline`
- `vendor`
- `transaction`
- `line_items`
- `totals`

## Validation Rules

The app should warn and require review when:

- Vendor is missing.
- Date is missing or invalid.
- Total is missing.
- Line items do not add up to subtotal or total within tolerance.
- Item-level tax totals are inconsistent with receipt tax.
- Weight/unit is ambiguous or missing for weighted items.
- VLM pass times out or returns invalid JSON.
- OCR/layout pass cannot confidently locate major regions.
- Any low-confidence fields affect QuickBooks posting.

The app must never auto-sync low-confidence receipts without review.

## QuickBooks Export and Sync

MVP export mode:

- Export reviewed receipt JSON.
- Export CSV for manual reconciliation/import.
- Export a folder containing the original image and extracted data.

Direct QBO sync target:

1. Resolve or create Vendor.
2. Resolve expense/category account.
3. Create Purchase or Bill transaction.
4. Add account-based or item-based line details.
5. Upload original receipt image as an attachment.
6. Link attachment to the created transaction.
7. Store QBO transaction ID and sync status.

OAuth and production QBO sync are intentionally later-stage compared with local extraction and review.

## Persistence

Runtime data is stored under `data/`:

- `data/originals/`: unchanged source images.
- `data/processed/`: OCR/VLM derivatives, layout crops, and chunk crops.
- `data/receipts.sqlite3`: receipt metadata, OCR result, preliminary layout result, extracted JSON, status, and sync result.
- `data/exports/`: JSON/CSV/QBO-shaped export bundles.

Duplicate handling:

- The app hashes the input image.
- It avoids showing duplicate current-version receipts by default.
- New extraction versions or forced rescans can create newer receipt versions from the same source image.

## Current Non-Goals

- Fully automatic bookkeeping without review.
- Perfect line-item extraction from every receipt.
- Perfect item-level tax inference when the receipt does not print item tax.
- Multi-company SaaS support.
- Mobile app.
- Cloud-only model dependency.
- Automatic tax filing logic.
