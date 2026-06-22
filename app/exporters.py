from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from app.models import ReceiptRecord
from app.settings import EXPORTS_DIR


def export_receipt_bundle(record: ReceiptRecord) -> Path:
    export_dir = EXPORTS_DIR / f"receipt-{record.id}"
    export_dir.mkdir(parents=True, exist_ok=True)

    json_path = export_dir / "receipt.json"
    json_path.write_text(record.extracted_json.model_dump_json(indent=2), encoding="utf-8")

    csv_path = export_dir / "receipt.csv"
    _write_csv(record, csv_path)

    original_path = Path(record.original_path)
    if original_path.exists():
        shutil.copy2(original_path, export_dir / original_path.name)

    qbo_path = export_dir / "qbo_purchase.json"
    qbo_path.write_text(json.dumps(to_qbo_purchase_payload(record), indent=2), encoding="utf-8")
    return export_dir


def _write_csv(record: ReceiptRecord, csv_path: Path) -> None:
    receipt = record.extracted_json
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "receipt_id",
                "vendor",
                "date",
                "currency",
                "description",
                "item",
                "weight",
                "quantity",
                "unit",
                "unit_price",
                "amount",
                "line_tax",
                "tax_code",
                "taxable",
                "suggested_qbo_account",
                "total",
                "tax",
            ],
        )
        writer.writeheader()
        if receipt.line_items:
            for item in receipt.line_items:
                writer.writerow(
                    {
                        "receipt_id": record.id,
                        "vendor": receipt.vendor.name,
                        "date": receipt.transaction.date,
                        "currency": receipt.transaction.currency,
                        "description": item.description,
                        "item": item.item,
                        "weight": item.weight,
                        "quantity": item.quantity,
                        "unit": item.unit,
                        "unit_price": item.unit_price,
                        "amount": item.amount,
                        "line_tax": item.tax,
                        "tax_code": item.tax_code,
                        "taxable": item.taxable,
                        "suggested_qbo_account": item.suggested_qbo_account,
                        "total": receipt.transaction.total,
                        "tax": receipt.transaction.tax,
                    }
                )
        else:
            writer.writerow(
                {
                    "receipt_id": record.id,
                    "vendor": receipt.vendor.name,
                    "date": receipt.transaction.date,
                    "currency": receipt.transaction.currency,
                    "description": "Receipt total",
                    "item": "Receipt total",
                    "weight": "",
                    "quantity": 1,
                    "unit": "",
                    "unit_price": receipt.transaction.total,
                    "amount": receipt.transaction.total,
                    "line_tax": receipt.transaction.tax,
                    "tax_code": "",
                    "taxable": "",
                    "suggested_qbo_account": receipt.quickbooks.account_ref or "",
                    "total": receipt.transaction.total,
                    "tax": receipt.transaction.tax,
                }
            )


def to_qbo_purchase_payload(record: ReceiptRecord) -> dict:
    receipt = record.extracted_json
    lines = []
    source_items = receipt.line_items or []
    if not source_items:
        source_items = []

    for item in source_items:
        lines.append(
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": round(item.amount, 2),
                "Description": item.description or item.item,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {
                        "value": receipt.quickbooks.account_ref or item.suggested_qbo_account,
                        "name": item.suggested_qbo_account,
                    },
                    "BillableStatus": "NotBillable",
                    "TaxCodeRef": {"value": "TAX" if item.taxable else "NON"},
                },
            }
        )

    if not lines:
        lines.append(
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": round(receipt.transaction.total, 2),
                "Description": "Receipt total",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": receipt.quickbooks.account_ref or "", "name": "Uncategorized Expense"},
                    "BillableStatus": "NotBillable",
                },
            }
        )

    return {
        "TxnDate": receipt.transaction.date,
        "CurrencyRef": {"value": receipt.transaction.currency},
        "EntityRef": {"type": "Vendor", "value": receipt.quickbooks.vendor_ref or "", "name": receipt.vendor.name},
        "PaymentType": receipt.transaction.payment_method or "Cash",
        "TotalAmt": round(receipt.transaction.total, 2),
        "Line": lines,
        "PrivateNote": f"Imported from local receipt record {record.id}. Attach {Path(record.original_path).name}.",
    }
