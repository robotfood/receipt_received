from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ReceiptStatus(str, Enum):
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    REVIEWED = "reviewed"
    EXPORTED = "exported"
    SYNCED = "synced"
    SYNC_FAILED = "sync_failed"


class Vendor(BaseModel):
    name: str = ""
    address: str = ""
    phone: str = ""
    tax_id: str = ""


class Transaction(BaseModel):
    date: str = ""
    time: str = ""
    invoice_number: str = ""
    currency: str = "CAD"
    payment_method: str = ""
    subtotal: float = 0.0
    tax: float = 0.0
    tip: float = 0.0
    total: float = 0.0

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return (value or "CAD").upper()


class LineItem(BaseModel):
    description: str = ""
    item: str = ""
    quantity: float = 1.0
    unit: str = ""
    weight: float | None = None
    unit_price: float = 0.0
    amount: float = 0.0
    tax: float = 0.0
    tax_code: str = ""
    taxable: bool = True
    suggested_qbo_account: str = ""
    confidence: float = 0.0


class QuickBooksInfo(BaseModel):
    target_entity: str = "Purchase"
    vendor_ref: str | None = None
    account_ref: str | None = None
    attach_original_image: bool = True


class ConfidenceInfo(BaseModel):
    overall: float = 0.0
    requires_review: bool = True
    warnings: list[str] = Field(default_factory=list)


class ReceiptJSON(BaseModel):
    document_type: str = "receipt_or_invoice"
    vendor: Vendor = Field(default_factory=Vendor)
    transaction: Transaction = Field(default_factory=Transaction)
    line_items: list[LineItem] = Field(default_factory=list)
    quickbooks: QuickBooksInfo = Field(default_factory=QuickBooksInfo)
    confidence: ConfidenceInfo = Field(default_factory=ConfidenceInfo)


class OCRBox(BaseModel):
    text: str
    confidence: float = 0.0
    box: list[float] = Field(default_factory=list)


class OCRResult(BaseModel):
    engine: str
    raw_text: str = ""
    boxes: list[OCRBox] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LayoutRegion(BaseModel):
    name: str
    box: list[int] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


class PreliminaryLayout(BaseModel):
    engine: str = ""
    image_width: int = 0
    image_height: int = 0
    receipt_outline: LayoutRegion | None = None
    regions: list[LayoutRegion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReceiptRecord(BaseModel):
    id: int
    source_hash: str = ""
    extraction_version: str = ""
    original_filename: str
    original_path: str
    processed_path: str | None = None
    ocr_result: OCRResult | None = None
    layout_result: PreliminaryLayout | None = None
    extracted_json: ReceiptJSON
    status: ReceiptStatus
    qbo_sync_result: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ReceiptCreateResult(BaseModel):
    receipt_id: int
    status: ReceiptStatus
    extracted_json: ReceiptJSON
    reused_existing: bool = False


def today_iso() -> str:
    return date.today().isoformat()
