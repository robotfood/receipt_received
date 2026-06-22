# Application Spec: Local Invoice Photo -> QuickBooks Online Itemized Receipt Import

## Goal

Build a small Python application that accepts invoice/receipt photos, extracts structured receipt data locally, lets the user review/correct the result, and exports or posts the data to QuickBooks Online as itemized expense records with the original receipt image attached.

The extraction must prioritize these itemized bookkeeping fields:

* item
* weight/unit
* cost
* taxes
* supplier
* date of purchase
* method of payment

Item-level tax is expected to be the trickiest field and must be explicitly reviewable. Weight/unit extraction can also be inconsistent across suppliers; the app should preserve what was read and allow later normalization against a product/vendor database.

## Target User

A small business owner or bookkeeper who wants to photograph receipts/invoices and reduce manual QuickBooks entry.

## Core Workflow

1. User uploads or captures an invoice/receipt photo.
2. App preprocesses the image.
3. App extracts receipt fields and line items using a local OCR/document model.
4. App normalizes data into a strict JSON schema.
5. App validates totals, taxes, dates, and confidence scores.
6. User reviews and edits extracted data.
7. App exports to QuickBooks-compatible JSON/CSV or posts directly to QuickBooks Online.
8. App stores the original image, extraction result, review status, and QuickBooks sync result.

## Local Model Strategy

### Recommended MVP Pipeline

Use a two-stage local pipeline:

1. OCR/layout extraction:

   * PaddleOCR or docTR
   * Extract raw text, bounding boxes, and layout order.

2. Structured extraction:

   * Small local LLM or rules engine
   * Convert OCR output into normalized JSON:

     * vendor
     * date
     * subtotal
     * tax
     * total
     * currency
     * payment method
     * line items
     * item weight/unit
     * item tax/tax code
     * expense category suggestions

This is safer than relying only on a multimodal model because receipts need exact numbers.

### Optional Multimodal Model

Use Qwen2.5-VL-3B-Instruct locally for:

* Hard-to-read receipts
* Vendor/category classification
* Fallback extraction when OCR layout is poor

Tesla P4 has 8 GB VRAM, so the app should target quantized/small models only. Larger 7B+ vision models should be treated as optional/non-MVP.

## Input Requirements

Supported input:

* JPG
* PNG
* HEIC if converted server-side
* PDF later, not MVP

Image preprocessing:

* auto-rotate
* perspective correction
* crop receipt boundaries
* de-noise
* contrast/sharpen
* resize for model limits
* preserve original image unchanged

## Test Input

For MVP development and verification, use the receipt images under `test-input/`:

* `test-input/20260528_1207371.jpg`
* `test-input/20260528_1210531.jpg`
* `test-input/20260528_1810481.jpg`
* `test-input/20260528_1811031.jpg`

## Output JSON Schema

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
    "currency": "CAD",
    "payment_method": "",
    "subtotal": 0.00,
    "tax": 0.00,
    "tip": 0.00,
    "total": 0.00
  },
  "line_items": [
    {
      "description": "",
      "item": "",
      "quantity": 1,
      "unit": "",
      "weight": null,
      "unit_price": 0.00,
      "amount": 0.00,
      "tax": 0.00,
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

## QuickBooks Online Integration

MVP should support two modes:

### Mode A: Review + Export

Export reviewed receipts as:

* JSON file
* CSV file for manual import/reconciliation
* folder containing original image + extracted JSON

This mode avoids API auth complexity during early development.

### Mode B: Direct QBO Sync

Use QuickBooks Online OAuth 2.0.

For each reviewed receipt:

1. Resolve or create Vendor.
2. Resolve expense/category account.
3. Create a Purchase or Bill transaction.
4. Add account-based or item-based line details.
5. Upload original receipt image as an attachment.
6. Link attachment to the created transaction.
7. Store QBO transaction ID and sync status.

## Review UI

Simple local web UI using FastAPI + HTMX, Streamlit, or Flask.

Required screens:

* Upload receipt
* Extraction result
* Side-by-side image and editable fields
* Line item editor
* Warnings/errors panel
* "Export" button
* "Sync to QuickBooks" button

Validation rules:

* line items must add up to subtotal or total within tolerance
* line-item taxes must add up to receipt tax within tolerance when item tax is available
* item-level tax must be highlighted when inferred, missing, or inconsistent
* weight/unit must be preserved exactly as read and flagged when ambiguous
* date must be valid
* total must be present
* vendor must be present
* low-confidence fields must be highlighted
* never auto-sync low-confidence receipts without review

## Suggested Tech Stack

Backend:

* Python 3.11+
* FastAPI
* SQLite for MVP
* SQLModel or SQLAlchemy
* Pydantic for schemas
* Celery/RQ optional for background processing

OCR/model:

* PaddleOCR or docTR for OCR
* optional Qwen2.5-VL-3B quantized fallback
* OpenCV for preprocessing
* RapidFuzz for vendor matching
* dateparser for messy dates

QuickBooks:

* intuit-oauth or requests-based OAuth client
* encrypted token storage
* sandbox mode first

Storage:

* local filesystem for images
* SQLite metadata DB
* later: S3-compatible object storage

## MVP Milestones

### Milestone 1: Local Extraction Only

* Upload receipt image
* Preprocess image
* Run OCR
* Show raw text
* Save original image and OCR result

### Milestone 2: Structured JSON

* Extract supplier, purchase date, payment method, total, and total tax
* Extract line items where possible
* Extract item, weight/unit, cost, and item-level tax where visible or inferable
* Validate totals
* Save normalized JSON

### Milestone 3: Review UI

* Editable extracted fields
* Confidence warnings
* Approve/reject workflow

### Milestone 4: QuickBooks Export

* Export JSON/CSV
* Map fields to QBO Purchase/Bill structure

### Milestone 5: QuickBooks Direct Sync

* OAuth connection
* Create Purchase/Bill
* Upload and attach receipt image
* Store sync result

## Non-Goals for MVP

* fully automatic bookkeeping without review
* perfect line-item extraction from every receipt
* multi-company SaaS support
* mobile app
* cloud model dependency
* automatic tax filing logic

## Key Risks

1. OCR accuracy on crumpled/blurry receipts.
2. Line-item extraction is harder than totals/vendor/date.
3. QuickBooks category/account mapping needs user configuration.
4. Tesla P4 VRAM limits model choices.
5. QBO sync requires careful OAuth/token handling.
6. Item-level tax is often not printed directly and may need inference from tax codes, receipt legends, or vendor/product rules.
7. Weight/unit may be represented inconsistently across suppliers and should be normalized later against a database.

## Acceptance Criteria

A receipt is considered successfully processed when:

* original image is stored
* vendor, date, total, and tax are extracted or marked for review
* supplier, date of purchase, and method of payment are extracted or marked for review
* item, weight/unit, cost, and taxes are extracted for line items when readable
* item-level tax is either extracted, inferred with a warning, or marked for review
* user can correct fields
* validated JSON is produced
* reviewed receipt can be exported
* QBO sync creates a transaction and attaches the original receipt image
