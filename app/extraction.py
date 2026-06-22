from __future__ import annotations

import re
from datetime import datetime

from app.models import ConfidenceInfo, LineItem, OCRResult, QuickBooksInfo, ReceiptJSON, Transaction, Vendor
from app.settings import TOTAL_TOLERANCE


MONEY_RE = re.compile(r"(?<!\d)(?:\$|CAD\s*)?(-?\d{1,4}(?:[,\s]\d{3})*(?:[.,]\d{2}))(?!\d)", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
TAX_ID_RE = re.compile(r"\b(?:GST|HST|QST|TPS|TVQ|BN|RT)\s*(?:#|NO\.?|NUMBER)?\s*[:\-]?\s*([A-Z0-9 -]{6,20})", re.IGNORECASE)

TOTAL_LABELS = ("total", "amount due", "balance due", "grand total")
SUBTOTAL_LABELS = ("subtotal", "sub total", "sous-total", "sous total", "net")
TAX_LABELS = ("tax", "hst", "gst", "qst", "pst", "tps", "tvq")
PAYMENT_LABELS = ("visa", "mastercard", "amex", "debit", "cash", "credit")


def extract_receipt_json(ocr: OCRResult) -> ReceiptJSON:
    lines = [line.strip() for line in ocr.raw_text.splitlines() if line.strip()]
    vendor = _extract_vendor(lines)
    transaction = Transaction(
        date=_extract_date(lines),
        currency=_extract_currency(lines),
        payment_method=_extract_payment_method(lines),
        subtotal=_amount_for_labels(lines, SUBTOTAL_LABELS),
        tax=_amount_for_labels(lines, TAX_LABELS),
        tip=_amount_for_labels(lines, ("tip", "gratuity")),
        total=_extract_total(lines),
    )
    line_items = _extract_line_items(lines)
    warnings = _validate(vendor, transaction, line_items, ocr)
    confidence = _confidence(warnings, ocr, transaction)
    return ReceiptJSON(
        vendor=vendor,
        transaction=transaction,
        line_items=line_items,
        quickbooks=QuickBooksInfo(),
        confidence=confidence,
    )


def validate_receipt(receipt: ReceiptJSON) -> ReceiptJSON:
    warnings = _validate(receipt.vendor, receipt.transaction, receipt.line_items, None)
    receipt.confidence.warnings = warnings
    receipt.confidence.requires_review = bool(warnings) or receipt.confidence.overall < 0.85
    return receipt


def _extract_vendor(lines: list[str]) -> Vendor:
    ignored = (
        "receipt",
        "invoice",
        "tax invoice",
        "customer copy",
        "merchant copy",
        "not a member",
        "exclusive",
        "offer",
        "reward",
        "delivery",
        "download",
        "online",
        "join now",
    )
    candidates = [
        line
        for line in lines[:10]
        if len(line) > 2
        and not line.replace(" ", "").isdigit()
        and not any(word in line.lower() for word in ignored)
        and not line.lstrip().startswith(("•", "*", "-"))
    ]
    preferred = [
        line
        for line in candidates
        if re.search(r"\b(super|supermarche|supermarket|market|marche|store|foods?|grocery|pharmacy|restaurant)\b", line, re.I)
    ]
    name = preferred[0] if preferred else (candidates[0] if candidates else "")
    address = ""
    phone = ""
    tax_id = ""

    for line in lines:
        if not phone:
            match = PHONE_RE.search(line)
            if match:
                phone = match.group(0)
        if not tax_id:
            match = TAX_ID_RE.search(line)
            if match:
                tax_id = match.group(1).strip()
        if not address and re.search(r"\d+ .+\b(?:st|street|ave|avenue|rd|road|blvd|drive|dr|lane|ln)\b", line, re.I):
            address = line

    return Vendor(name=name, address=address, phone=phone, tax_id=tax_id)


def _extract_date(lines: list[str]) -> str:
    joined = "\n".join(lines)
    date_patterns = [
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b\d{4}\.\d{1,2}\.\d{1,2}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4}\b",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, joined, re.IGNORECASE)
        if match:
            parsed = _parse_date(match.group(0))
            if parsed:
                return parsed
    return ""


def _parse_date(value: str) -> str:
    try:
        import dateparser

        parsed = dateparser.parse(value, settings={"PREFER_DAY_OF_MONTH": "first"})
        return parsed.date().isoformat() if parsed else ""
    except Exception:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
    return ""


def _extract_currency(lines: list[str]) -> str:
    text = "\n".join(lines).upper()
    if " USD" in text or "US$" in text:
        return "USD"
    return "CAD"


def _extract_payment_method(lines: list[str]) -> str:
    for line in lines:
        lower = line.lower()
        for label in PAYMENT_LABELS:
            if label in lower:
                return label.upper()
    return ""


def _extract_total(lines: list[str]) -> float:
    labelled = _amount_for_labels(lines, TOTAL_LABELS)
    if labelled:
        return labelled
    amounts = [
        _parse_money(match.group(1))
        for line in lines
        if not _looks_like_date(line)
        for match in MONEY_RE.finditer(line)
    ]
    return max(amounts) if amounts else 0.0


def _amount_for_labels(lines: list[str], labels: tuple[str, ...]) -> float:
    best = 0.0
    for index, line in enumerate(lines):
        lower = line.lower()
        if labels == TOTAL_LABELS and any(label in lower for label in SUBTOTAL_LABELS):
            continue
        if any(label in lower for label in labels):
            amounts = [_parse_money(match.group(1)) for match in MONEY_RE.finditer(line)]
            if amounts:
                best = amounts[-1]
                continue
            lookahead = lines[index + 1 :] if labels == TOTAL_LABELS else lines[index + 1 : index + 4]
            lookahead_amounts: list[float] = []
            for next_line in lookahead:
                if _looks_like_date(next_line):
                    continue
                amounts = [_parse_money(match.group(1)) for match in MONEY_RE.finditer(next_line)]
                if amounts:
                    lookahead_amounts.extend(amounts)
                    if labels != TOTAL_LABELS:
                        break
            if lookahead_amounts:
                best = max(lookahead_amounts) if labels == TOTAL_LABELS else lookahead_amounts[-1]
    return best


def _extract_line_items(lines: list[str]) -> list[LineItem]:
    items: list[LineItem] = []
    stop_words = ("subtotal", "total", "tax", "hst", "gst", "qst", "change", "balance", "amount due")
    for line in lines:
        lower = line.lower()
        if any(word in lower for word in stop_words):
            continue
        if _looks_like_date(line):
            continue
        if "/kg" in lower or "/ko" in lower or re.search(r"\bkg\b", lower):
            continue
        amounts = [_parse_money(match.group(1)) for match in MONEY_RE.finditer(line)]
        if not amounts:
            continue
        description = MONEY_RE.sub("", line).strip(" -:\t")
        if len(description) < 2:
            continue
        amount = amounts[-1]
        items.append(
            LineItem(
                description=description[:120],
                item=description[:120],
                quantity=1,
                unit_price=amount,
                amount=amount,
                taxable=True,
                suggested_qbo_account=_suggest_account(description),
                confidence=0.45,
            )
        )
    return items[:30]


def _suggest_account(description: str) -> str:
    lower = description.lower()
    if any(word in lower for word in ("fuel", "gas", "parking", "uber", "taxi")):
        return "Automobile"
    if any(word in lower for word in ("meal", "restaurant", "coffee", "food")):
        return "Meals and entertainment"
    if any(word in lower for word in ("paper", "ink", "office", "stationery")):
        return "Office expenses"
    return "General business expense"


def _validate(
    vendor: Vendor,
    transaction: Transaction,
    line_items: list[LineItem],
    ocr: OCRResult | None,
) -> list[str]:
    warnings: list[str] = []
    if ocr and ocr.warnings:
        warnings.extend(ocr.warnings)
    if not vendor.name:
        warnings.append("Vendor is missing.")
    if not transaction.date:
        warnings.append("Transaction date is missing or invalid.")
    if transaction.total <= 0:
        warnings.append("Total is missing.")
    if transaction.tax < 0:
        warnings.append("Tax cannot be negative.")
    if line_items:
        line_sum = round(sum(item.amount for item in line_items), 2)
        item_tax_sum = round(sum(item.tax for item in line_items), 2)
        subtotal = round(transaction.subtotal, 2)
        total_without_tax_tip = round(transaction.total - transaction.tax - transaction.tip, 2)
        if transaction.tax and item_tax_sum and abs(item_tax_sum - round(transaction.tax, 2)) > TOTAL_TOLERANCE:
            warnings.append(f"Line-item taxes total {item_tax_sum:.2f}, but receipt tax is {transaction.tax:.2f}.")
        elif transaction.tax and not item_tax_sum:
            warnings.append("Line-item tax is missing or not allocated.")
        for item in line_items:
            if item.weight is None and item.unit.lower() in {"kg", "g", "lb", "oz"}:
                warnings.append(f"Weight is missing for weighted item: {item.description or item.item}.")
        if subtotal and abs(line_sum - subtotal) > TOTAL_TOLERANCE:
            warnings.append(f"Line items total {line_sum:.2f}, but subtotal is {subtotal:.2f}.")
        elif not subtotal and transaction.total and abs(line_sum - total_without_tax_tip) > TOTAL_TOLERANCE:
            warnings.append(f"Line items total {line_sum:.2f}, but total less tax/tip is {total_without_tax_tip:.2f}.")
    else:
        warnings.append("No readable line items were extracted.")
    return warnings


def _confidence(warnings: list[str], ocr: OCRResult, transaction: Transaction) -> ConfidenceInfo:
    box_confidences = [box.confidence for box in ocr.boxes if box.confidence > 0]
    ocr_score = sum(box_confidences) / len(box_confidences) if box_confidences else 0.0
    field_score = 0.0
    field_score += 0.25 if transaction.total > 0 else 0
    field_score += 0.20 if transaction.date else 0
    field_score += 0.15 if transaction.tax >= 0 else 0
    overall = min(0.95, round((ocr_score * 0.4) + field_score, 2))
    if warnings:
        overall = min(overall, 0.75)
    return ConfidenceInfo(overall=overall, requires_review=bool(warnings) or overall < 0.85, warnings=warnings)


def _parse_money(value: str) -> float:
    try:
        normalized = value.replace(" ", "")
        if "," in normalized and "." not in normalized:
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
        return float(normalized)
    except ValueError:
        return 0.0


def _looks_like_date(value: str) -> bool:
    return bool(
        re.search(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b", value)
        or re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", value)
    )
