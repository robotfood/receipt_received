from __future__ import annotations

import base64
import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

class VLMLineItemSchema(BaseModel):
    item: str = ""
    description: str = ""
    weight: float | None = None
    unit: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    cost: float = 0.0
    tax: float = 0.0
    tax_code: str = ""
    taxable: bool = True

class VLMLineItemsSchema(BaseModel):
    line_items: list[VLMLineItemSchema] = []
    warnings: list[str] = []

class VLMSummarySchema(BaseModel):
    supplier: str = ""
    supplier_address: str = ""
    supplier_phone: str = ""
    supplier_tax_id: str = ""
    date_of_purchase: str = ""
    time_of_purchase: str = ""
    invoice_number: str = ""
    method_of_payment: str = ""
    currency: str = "CAD"
    subtotal: float = 0.0
    tax: float = 0.0
    tip: float = 0.0
    total: float = 0.0
    warnings: list[str] = []

class TaxCodeLegendItem(BaseModel):
    code: str = ""
    meaning: str = ""
    rate: float = 0.0

class VLMTotalsSchema(BaseModel):
    date_of_purchase: str = ""
    supplier_tax_id: str = ""
    method_of_payment: str = ""
    currency: str = "CAD"
    subtotal: float = 0.0
    tax: float = 0.0
    tip: float = 0.0
    total: float = 0.0
    card_amount: float = 0.0
    tax_code_legend: list[TaxCodeLegendItem] = []
    warnings: list[str] = []

class VLMFullExtractionSchema(BaseModel):
    supplier: str = ""
    supplier_address: str = ""
    supplier_phone: str = ""
    supplier_tax_id: str = ""
    date_of_purchase: str = ""
    time_of_purchase: str = ""
    invoice_number: str = ""
    method_of_payment: str = ""
    currency: str = "CAD"
    subtotal: float = 0.0
    tax: float = 0.0
    tip: float = 0.0
    total: float = 0.0
    line_items: list[VLMLineItemSchema] = []
    warnings: list[str] = []

from app.extraction import validate_receipt
from app.image_regions import ReceiptRegions
from app.models import ConfidenceInfo, LineItem, QuickBooksInfo, ReceiptJSON, Transaction, Vendor


DEFAULT_MODEL = "hf.co/unsloth/Qwen3-VL-8B-Instruct-GGUF:UD-Q4_K_XL"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
PHONE_PATTERN_HINT = "(514)747-1240, 514-747-1240, 514 747 1240, or 514.747.1240"
TAX_ID_PATTERN_HINT = "TPS#135747137RT, TVQ#1225478038TQ0001, GST#135747137RT, QST#1225478038TQ0001"


class VLMExtractionError(Exception):
    pass


def extract_receipt_with_vlm(image_path: Path) -> ReceiptJSON | None:
    try:
        return _extract_receipt_with_vlm(image_path)
    except VLMExtractionError:
        return None


def extract_receipt_with_vlm_or_raise(image_path: Path) -> ReceiptJSON:
    return _extract_receipt_with_vlm(image_path)


def extract_receipt_staged_with_vlm_or_raise(regions: ReceiptRegions) -> ReceiptJSON:
    pass_results: dict[str, dict[str, Any]] = {}
    pass_warnings: list[str] = []

    staged_passes = [
        ("summary", regions.summary, _summary_prompt(), 768, VLMSummarySchema.model_json_schema()),
        ("totals", regions.totals, _totals_prompt(), 1024, VLMTotalsSchema.model_json_schema()),
    ]
    for name, image_path, prompt, num_predict, schema in staged_passes:
        try:
            pass_results[name] = _call_ollama_json(image_path, prompt, num_predict=num_predict, schema=schema)
        except VLMExtractionError as exc:
            pass_results[name] = {}
            pass_warnings.append(f"VLM {name} pass failed: {exc}")

    chunk_items, line_warnings, chunk_successes = _extract_raw_line_items_from_chunks(regions.line_item_chunks)
    pass_warnings.extend(line_warnings)
    pass_results["line_items"] = {"line_items": _dedupe_raw_line_items(chunk_items)}
    pass_warnings.append(f"VLM line-item chunks succeeded: {chunk_successes}/{len(regions.line_item_chunks)}.")

    if not any(pass_results.values()):
        raise VLMExtractionError("All staged VLM passes failed.")

    receipt = _merge_staged_results(pass_results, pass_warnings)
    model_warnings = list(receipt.confidence.warnings)
    receipt = validate_receipt(receipt)
    receipt.confidence.warnings = _merge_warnings(receipt.confidence.warnings, model_warnings)
    receipt.confidence.requires_review = True
    receipt.confidence.warnings.append("Extracted by staged multimodal local model. Review before export or sync.")
    return receipt


def extract_line_items_with_vlm_or_raise(chunk_paths: list[Path]) -> tuple[list[LineItem], list[str]]:
    raw_items, warnings, chunk_successes = _extract_raw_line_items_from_chunks(chunk_paths)
    payload = {
        "supplier": "",
        "currency": "CAD",
        "line_items": _dedupe_raw_line_items(raw_items),
        "warnings": [
            *warnings,
            f"Manual VLM line-item chunks succeeded: {chunk_successes}/{len(chunk_paths)}.",
        ],
    }
    receipt = _normalize_model_json(payload)
    return receipt.line_items, receipt.confidence.warnings


def _extract_raw_line_items_from_chunks(chunk_paths: list[Path]) -> tuple[list[dict[str, Any]], list[str], int]:
    chunk_items: list[dict[str, Any]] = []
    warnings: list[str] = []
    chunk_successes = 0
    for index, chunk_path in enumerate(chunk_paths, start=1):
        try:
            payload = _call_ollama_json(
                chunk_path,
                _line_items_prompt(),
                num_predict=1536,
                schema=VLMLineItemsSchema.model_json_schema(),
                timeout_env="RECEIPT_VLM_LINE_ITEM_TIMEOUT",
                default_timeout=90,
            )
            items = payload.get("line_items") or payload.get("items") or []
            if items:
                chunk_items.extend(items)
            chunk_successes += 1
            for warning in payload.get("warnings", []):
                if str(warning).strip():
                    warnings.append(f"VLM line-item chunk {index}: {warning}")
        except VLMExtractionError as exc:
            warnings.append(f"VLM line-item chunk {index} failed: {exc}")
    return chunk_items, warnings, chunk_successes


def _extract_receipt_with_vlm(image_path: Path) -> ReceiptJSON:
    raw_payload = _call_ollama_json(image_path, _prompt(), num_predict=4096, schema=VLMFullExtractionSchema.model_json_schema())
    try:
        receipt = _normalize_model_json(raw_payload)
    except (ValueError, ValidationError, TypeError, KeyError) as exc:
        raise VLMExtractionError(f"Invalid model JSON: {exc}. Response: {str(raw_payload)[:500]}") from exc

    receipt.confidence = ConfidenceInfo(
        overall=min(max(receipt.confidence.overall or 0.85, 0.0), 0.95),
        requires_review=True,
        warnings=receipt.confidence.warnings,
    )
    receipt.confidence.warnings.append(f"Extracted by multimodal local model: {_model_name()}. Review before sync.")
    model_warnings = list(receipt.confidence.warnings)
    receipt = validate_receipt(receipt)
    receipt.confidence.warnings = _merge_warnings(receipt.confidence.warnings, model_warnings)
    receipt.confidence.requires_review = True
    if "Multimodal extraction requires review before export or sync." not in receipt.confidence.warnings:
        receipt.confidence.warnings.append("Multimodal extraction requires review before export or sync.")
    return receipt


def _call_ollama_json(
    image_path: Path,
    prompt: str,
    *,
    num_predict: int,
    schema: dict[str, Any] | None = None,
    timeout_env: str = "RECEIPT_VLM_TIMEOUT",
    default_timeout: int = 90,
) -> dict[str, Any]:
    model = os.getenv("RECEIPT_VLM_MODEL", DEFAULT_MODEL)
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [_image_b64(image_path)],
        "stream": False,
        "format": schema if schema else "json",
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            "num_predict": num_predict,
        },
    }

    try:
        timeout = float(os.getenv(timeout_env, os.getenv("RECEIPT_VLM_TIMEOUT", str(default_timeout))))
        data = _post_ollama_generate(f"{base_url}/api/generate", payload, timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VLMExtractionError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError) as exc:
        raise VLMExtractionError(str(exc)) from exc

    raw_response = str(data.get("response", ""))
    try:
        return _extract_json(raw_response)
    except (ValueError, ValidationError, TypeError, KeyError) as exc:
        raise VLMExtractionError(f"Invalid model JSON: {exc}. Response: {raw_response[:500]}") from exc


def _post_ollama_generate(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    encoded_payload = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_data = response.read()
            return json.loads(res_data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VLMExtractionError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError) as exc:
        raise VLMExtractionError(str(exc)) from exc


def _image_b64(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _model_name() -> str:
    return os.getenv("RECEIPT_VLM_MODEL", DEFAULT_MODEL)


def _extract_json(raw_response: str) -> dict[str, Any]:
    raw_response = raw_response.strip()
    if raw_response.startswith("```"):
        raw_response = re.sub(r"^```(?:json)?\s*", "", raw_response)
        raw_response = re.sub(r"\s*```$", "", raw_response)
    return json.loads(raw_response)


def _normalize_model_json(payload: dict[str, Any]) -> ReceiptJSON:
    supplier = str(payload.get("supplier") or payload.get("vendor") or payload.get("vendor_name") or "")
    raw_tax_ids = payload.get("supplier_tax_ids") or payload.get("tax_ids") or payload.get("tax_id") or payload.get("supplier_tax_id") or ""
    tax_id = _format_tax_ids(raw_tax_ids)
    line_items = []
    for raw_item in payload.get("line_items") or payload.get("items") or []:
        item_name = str(raw_item.get("item") or raw_item.get("name") or raw_item.get("description") or "")
        amount = _number(raw_item.get("cost", raw_item.get("amount", 0.0)))
        line_items.append(
            LineItem(
                description=str(raw_item.get("description") or item_name),
                item=item_name,
                quantity=_number(raw_item.get("quantity", 1.0), default=1.0),
                unit=str(raw_item.get("unit") or raw_item.get("weight_unit") or ""),
                weight=_optional_number(raw_item.get("weight")),
                unit_price=_number(raw_item.get("unit_price", 0.0)),
                amount=amount,
                tax=_number(raw_item.get("tax", 0.0)),
                tax_code=str(raw_item.get("tax_code") or ""),
                taxable=bool(raw_item.get("taxable", True)),
                suggested_qbo_account=str(raw_item.get("suggested_qbo_account") or "General business expense"),
                confidence=_number(raw_item.get("confidence", 0.75), default=0.75),
            )
        )

    warnings = [str(warning) for warning in payload.get("warnings", []) if str(warning).strip()]
    return ReceiptJSON(
        vendor=Vendor(
            name=supplier,
            address=str(payload.get("supplier_address") or payload.get("address") or ""),
            phone=_clean_phone(str(payload.get("supplier_phone") or payload.get("phone") or "")),
            tax_id=tax_id,
        ),
        transaction=Transaction(
            date=str(payload.get("date_of_purchase") or payload.get("date") or ""),
            time=str(payload.get("time_of_purchase") or payload.get("time") or ""),
            invoice_number=str(payload.get("invoice_number") or payload.get("receipt_number") or payload.get("invoice_no") or payload.get("receipt_no") or ""),
            currency=str(payload.get("currency") or "CAD"),
            payment_method=str(payload.get("method_of_payment") or payload.get("payment_method") or ""),
            subtotal=_number(payload.get("subtotal", 0.0)),
            tax=_number(payload.get("tax", payload.get("total_tax", 0.0))),
            tip=_number(payload.get("tip", 0.0)),
            total=_number(payload.get("total", 0.0)),
        ),
        line_items=line_items,
        quickbooks=QuickBooksInfo(),
        confidence=ConfidenceInfo(overall=0.85, requires_review=True, warnings=warnings),
    )


def _merge_staged_results(pass_results: dict[str, dict[str, Any]], pass_warnings: list[str]) -> ReceiptJSON:
    summary = pass_results.get("summary", {})
    items = pass_results.get("line_items", {})
    totals = pass_results.get("totals", {})

    merged: dict[str, Any] = {}
    merged.update(summary)

    for key in (
        "supplier_phone",
        "supplier_tax_id",
        "date_of_purchase",
        "time_of_purchase",
        "invoice_number",
        "subtotal",
        "tax",
        "tip",
        "total",
        "method_of_payment",
        "currency",
    ):
        value = totals.get(key)
        if value not in (None, "", 0, 0.0, []):
            merged[key] = value

    line_items = items.get("line_items") or items.get("items") or []
    if line_items:
        merged["line_items"] = line_items

    warnings = []
    for payload in (summary, items, totals):
        warnings.extend(str(warning) for warning in payload.get("warnings", []) if str(warning).strip())
    warnings.extend(pass_warnings)
    warnings.append(f"Staged VLM model: {_model_name()}.")
    merged["warnings"] = warnings

    return _normalize_model_json(merged)


def _dedupe_raw_line_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in raw_items:
        key = (
            _norm_key(item.get("item") or item.get("description")),
            _norm_key(item.get("weight")),
            _norm_key(item.get("unit_price")),
            _norm_key(item.get("cost", item.get("amount"))),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _norm_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _merge_warnings(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for warning in [*primary, *secondary]:
        if warning and warning not in merged:
            merged.append(warning)
    return merged


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", ".").replace("$", "").strip())
    except ValueError:
        return default


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return _number(value)


def _format_tax_ids(value: Any) -> str:
    if isinstance(value, list):
        return " / ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _clean_phone(value: str) -> str:
    value = value.strip()
    if re.search(r"\b(?:TPS|TVQ|GST|QST)\s*#?", value, re.IGNORECASE):
        return ""
    return value


def _prompt() -> str:
    return """
Look at the receipt or invoice image and extract only receipt information.
Return only JSON. No markdown. No explanation.

IMPORTANT: Pay close attention to handwritten corrections, annotations, cross-outs, and cancellations (e.g., "Return", "Cancelled", "Cancel"). If a printed total, subtotal, tax, price, or item is crossed out and corrected by hand, extract the hand-corrected value as the active value instead of the crossed-out printed value. If a line item is crossed out or marked as returned/cancelled, do not extract it, or adjust its cost to 0, and add a note to the `warnings` list explaining the return.

Use visible text from the image. Do not invent missing values.
Do not extract non-Latin/non-English script characters (e.g., ignore Chinese characters). For bilingual or multilingual descriptions, extract only the English or French text and ignore any Asian script.
Use CAD unless another currency is printed.
Dates must be YYYY-MM-DD. Money values must be numbers.
Supplier phone must match a phone format such as: """ + PHONE_PATTERN_HINT + """.
Do not put TPS, TVQ, GST, or QST numbers in supplier_phone.
TPS/TVQ/GST/QST numbers are supplier tax IDs. Examples: """ + TAX_ID_PATTERN_HINT + """.

Receipt fields to extract:
- supplier
- supplier_address
- supplier_phone
- supplier_tax_id
- date_of_purchase
- time_of_purchase
- invoice_number
- method_of_payment
- subtotal
- tax
- tip
- total
- line_items

For each line item, extract the printed item name, description, weight, unit,
quantity, unit price, line cost, item tax, printed tax code, and taxable flag.
If item tax is not printed, use 0.0 and add a warning.
If a tax code is printed beside an item, preserve it in tax_code.
For weighted items, parse examples like "1.245 kg @ $6.57/kg" as:
weight=1.245, unit="kg", unit_price=6.57, cost=the line total.

Return exactly this JSON shape:
{"supplier":"","supplier_address":"","supplier_phone":"","supplier_tax_id":"","date_of_purchase":"","time_of_purchase":"","invoice_number":"","method_of_payment":"","currency":"CAD",
"subtotal":0.0,"tax":0.0,"tip":0.0,"total":0.0,
"line_items":[{"item":"","description":"","weight":null,"unit":"",
"quantity":1,"unit_price":0.0,"cost":0.0,"tax":0.0,"tax_code":"",
"taxable":true}],
"warnings":[]}
""".strip()


def _summary_prompt() -> str:
    return """
Extract receipt summary fields from the image. Return only JSON.
IMPORTANT: Pay attention to handwritten corrections or edits. If a printed value (like date, invoice number, or totals) is crossed out and corrected by hand, extract the hand-corrected value.
Use visible text only. Dates: YYYY-MM-DD. Money: numbers.
Supplier phone must look like """ + PHONE_PATTERN_HINT + """.
TPS/TVQ/GST/QST numbers are tax IDs, not phone numbers. Examples: """ + TAX_ID_PATTERN_HINT + """.
{"supplier":"","supplier_address":"","supplier_phone":"","supplier_tax_id":"",
"date_of_purchase":"","time_of_purchase":"","invoice_number":"",
"method_of_payment":"","currency":"CAD","subtotal":0.0,"tax":0.0,
"tip":0.0,"total":0.0,"warnings":[]}
""".strip()


def _line_items_prompt() -> str:
    return """
Look at this receipt/invoice line-item crop and extract only visible line items.
Return only JSON. No markdown. No explanation. Use visible text only.
Ignore header, totals, payment, tax summary, customer copy, and footer text.
Do not invent missing items.

IMPORTANT: Look for handwritten cross-outs, cancellations (e.g., "Return", "Cancelled", "Cancel"), or price adjustments on each line item. If a line item is crossed out or marked as returned/cancelled, do not extract it, or adjust its cost to 0, and add a note to the `warnings` list explaining the return.

For each line item, extract item, description, weight, unit, quantity, unit_price,
cost, tax, tax_code, taxable. If item tax is not printed, use 0.0. Preserve
printed tax codes. For weighted items like "1.245 kg @ $6.57/kg", use
weight=1.245, unit="kg", unit_price=6.57, cost=line total.

Return exactly this JSON shape:
{"line_items":[{"item":"","description":"","weight":null,"unit":"",
"quantity":1,"unit_price":0.0,"cost":0.0,"tax":0.0,"tax_code":"",
"taxable":true}],"warnings":[]}
""".strip()


def _totals_prompt() -> str:
    return """
Look at this receipt/invoice totals/payment crop and extract only totals, taxes,
payment, and any tax-code legend.
Return only JSON. No markdown. No explanation. Use visible text only.

IMPORTANT: Look for handwritten changes or corrections to the subtotal, taxes, and grand total. If a printed total/subtotal is crossed out and a new one is written by hand, extract the hand-corrected value as the active value.

Dates must be YYYY-MM-DD if visible. Money values must be numbers.
If TPS/TVQ/GST/QST tax IDs are visible, put them in supplier_tax_id, not payment or phone.

Extract subtotal, tax, tip, total, method_of_payment, card_amount, currency,
date_of_purchase if visible, and tax_code_legend if visible.

Return exactly this JSON shape:
{"date_of_purchase":"","supplier_tax_id":"","method_of_payment":"","currency":"CAD",
"subtotal":0.0,"tax":0.0,"tip":0.0,"total":0.0,"card_amount":0.0,
"tax_code_legend":[{"code":"","meaning":"","rate":0.0}],"warnings":[]}
""".strip()
