from __future__ import annotations

from pathlib import Path

from app.exporters import to_qbo_purchase_payload
from app.models import ReceiptRecord


def sync_receipt_to_qbo(record: ReceiptRecord) -> dict:
    """Placeholder for Direct QBO Sync.

    The MVP exports a QBO-shaped Purchase payload locally. Direct sync needs OAuth
    credentials, realm selection, encrypted token storage, and sandbox validation.
    """
    payload = to_qbo_purchase_payload(record)
    attachment = Path(record.original_path)
    return {
        "status": "not_configured",
        "message": "QuickBooks OAuth is not configured. Use Export mode or add OAuth settings.",
        "would_create": payload,
        "would_attach": str(attachment),
    }
