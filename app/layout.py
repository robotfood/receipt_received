from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from app.models import LayoutRegion, OCRBox, OCRResult, PreliminaryLayout


@dataclass(frozen=True)
class PixelOCRBox:
    text: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2


TOTAL_RE = re.compile(r"\b(total|subtotal|sous[- ]?total|tax|tps|tvq|gst|qst|paid|amount|balance)\b", re.I)
TRANSACTION_RE = re.compile(
    r"\b(date|time|invoice|receipt|transaction|visa|mastercard|debit|credit|card|auth|approval|tps|tvq|gst|qst)\b",
    re.I,
)
ITEM_RE = re.compile(r"(\d+[.,]\d{2}\b|\bkg\b|\blb\b|\bun\b|\bea\b|@\s*\$?\d|/\s*(?:kg|lb|un|ea))", re.I)


def detect_preliminary_layout(image_path: Path, ocr_result: OCRResult) -> PreliminaryLayout:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size

    boxes = normalize_ocr_boxes(ocr_result.boxes, width, height, ocr_result.engine)
    warnings: list[str] = []

    outline = _receipt_outline_from_boxes(boxes, width, height)
    if outline is None:
        outline = _receipt_outline_from_image(image_path)
        if outline is None:
            outline = LayoutRegion(
                name="receipt_outline",
                box=[0, 0, width, height],
                confidence=0.15,
                reason="No reliable receipt outline found; using full image.",
            )
            warnings.append("Preliminary pass could not isolate the receipt outline.")
        else:
            warnings.append("Receipt outline estimated from image contrast because OCR boxes were insufficient.")

    regions = _semantic_regions(boxes, outline, width, height, warnings)
    return PreliminaryLayout(
        engine=f"prelim_layout:{ocr_result.engine}",
        image_width=width,
        image_height=height,
        receipt_outline=outline,
        regions=regions,
        warnings=warnings,
    )


def normalize_ocr_boxes(
    boxes: list[OCRBox],
    image_width: int,
    image_height: int,
    engine: str = "",
) -> list[PixelOCRBox]:
    normalized: list[PixelOCRBox] = []
    for box in boxes:
        points = box.box
        if len(points) < 8:
            continue
        xs = [float(points[index]) for index in range(0, len(points), 2)]
        ys = [float(points[index]) for index in range(1, len(points), 2)]
        if not xs or not ys:
            continue

        if max(xs + ys) <= 1.01:
            x1 = int(min(xs) * image_width)
            x2 = int(max(xs) * image_width)
            y1 = int((1 - max(ys)) * image_height)
            y2 = int((1 - min(ys)) * image_height)
        else:
            x1 = int(min(xs))
            x2 = int(max(xs))
            y1 = int(min(ys))
            y2 = int(max(ys))

        x1 = max(0, min(image_width - 1, x1))
        x2 = max(x1 + 1, min(image_width, x2))
        y1 = max(0, min(image_height - 1, y1))
        y2 = max(y1 + 1, min(image_height, y2))
        text = box.text.strip()
        if text:
            normalized.append(PixelOCRBox(text=text, confidence=box.confidence, x1=x1, y1=y1, x2=x2, y2=y2))
    return normalized


def _receipt_outline_from_boxes(boxes: list[PixelOCRBox], image_width: int, image_height: int) -> LayoutRegion | None:
    useful = [box for box in boxes if box.confidence >= 0.15]
    if len(useful) < 4:
        return None
    x1, y1, x2, y2 = _union_box(useful)
    pad_x = int((x2 - x1) * 0.12)
    pad_y = int((y2 - y1) * 0.08)
    box = _clamp_box([x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y], image_width, image_height)
    area_ratio = ((box[2] - box[0]) * (box[3] - box[1])) / max(1, image_width * image_height)
    confidence = 0.75 if area_ratio < 0.85 else 0.55
    return LayoutRegion(
        name="receipt_outline",
        box=box,
        confidence=confidence,
        reason="Padded bounding box around OCR text.",
    )


def _receipt_outline_from_image(image_path: Path) -> LayoutRegion | None:
    try:
        import cv2
        import numpy as np

        image = cv2.imread(str(image_path))
        if image is None:
            return None
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        image_area = width * height
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < image_area * 0.08:
                continue
            roi = gray[y : y + h, x : x + w]
            brightness = float(np.mean(roi)) if roi.size else 0.0
            aspect = h / max(1, w)
            score = area * (1.0 + brightness / 255.0) * (1.2 if aspect > 1.2 else 1.0)
            candidates.append((score, [x, y, x + w, y + h]))
        if not candidates:
            return None
        _, box = max(candidates, key=lambda item: item[0])
        return LayoutRegion(
            name="receipt_outline",
            box=_clamp_box(box, width, height),
            confidence=0.45,
            reason="Bright paper-like contour detected in the image.",
        )
    except Exception:
        return None


def _semantic_regions(
    boxes: list[PixelOCRBox],
    outline: LayoutRegion,
    image_width: int,
    image_height: int,
    warnings: list[str],
) -> list[LayoutRegion]:
    ox1, oy1, ox2, oy2 = outline.box
    receipt_h = max(1, oy2 - oy1)
    receipt_w = max(1, ox2 - ox1)
    inside = [box for box in boxes if _overlap_ratio([box.x1, box.y1, box.x2, box.y2], outline.box) > 0.2]

    top_limit = oy1 + int(receipt_h * 0.28)
    vendor = LayoutRegion(
        name="vendor",
        box=_clamp_box([ox1, oy1, ox2, top_limit], image_width, image_height),
        confidence=0.45 if inside else 0.25,
        reason="Top receipt band; expected vendor/header location.",
    )

    total_boxes = [box for box in inside if TOTAL_RE.search(box.text)]
    if total_boxes:
        totals_box = _expand_box(_union_box(total_boxes), int(receipt_w * 0.08), int(receipt_h * 0.08))
        totals_box = [ox1, max(oy1, totals_box[1]), ox2, oy2]
        totals = LayoutRegion(
            name="totals",
            box=_clamp_box(totals_box, image_width, image_height),
            confidence=0.75,
            reason="Anchored by total/tax/payment keywords.",
        )
    else:
        totals = LayoutRegion(
            name="totals",
            box=_clamp_box([ox1, oy1 + int(receipt_h * 0.68), ox2, oy2], image_width, image_height),
            confidence=0.30,
            reason="Fallback lower receipt band; no total keywords found.",
        )
        warnings.append("Preliminary pass did not find strong totals anchors.")

    transaction_boxes = [box for box in inside if TRANSACTION_RE.search(box.text)]
    if transaction_boxes:
        tx_box = _expand_box(_union_box(transaction_boxes), int(receipt_w * 0.08), int(receipt_h * 0.04))
        transaction = LayoutRegion(
            name="transaction",
            box=_clamp_box(tx_box, image_width, image_height),
            confidence=0.65,
            reason="Anchored by date/payment/tax-id keywords.",
        )
    else:
        transaction = LayoutRegion(
            name="transaction",
            box=_clamp_box([ox1, oy1, ox2, oy1 + int(receipt_h * 0.38)], image_width, image_height),
            confidence=0.25,
            reason="Fallback upper receipt band; no transaction anchors found.",
        )

    item_candidates = [
        box
        for box in inside
        if box.y1 > vendor.box[3] - int(receipt_h * 0.05)
        and box.y2 < totals.box[1] + int(receipt_h * 0.04)
        and (ITEM_RE.search(box.text) or box.x2 > ox1 + receipt_w * 0.62)
    ]
    if len(item_candidates) >= 3:
        item_box = _expand_box(_union_box(item_candidates), int(receipt_w * 0.08), int(receipt_h * 0.05))
        item_box = [ox1, max(vendor.box[3], item_box[1]), ox2, min(totals.box[1], item_box[3])]
        confidence = 0.70
        reason = "Dense middle rows with prices/units."
    else:
        item_box = [ox1, vendor.box[3], ox2, totals.box[1]]
        confidence = 0.35
        reason = "Fallback middle receipt band; weak item row anchors."
        warnings.append("Preliminary pass did not find strong itemized-list anchors.")

    if item_box[3] <= item_box[1] + 10:
        item_box = [ox1, oy1 + int(receipt_h * 0.24), ox2, oy1 + int(receipt_h * 0.72)]
        confidence = min(confidence, 0.25)

    itemized = LayoutRegion(
        name="line_items",
        box=_clamp_box(item_box, image_width, image_height),
        confidence=confidence,
        reason=reason,
    )

    return [vendor, transaction, itemized, totals]


def _union_box(boxes: list[PixelOCRBox]) -> list[int]:
    return [
        min(box.x1 for box in boxes),
        min(box.y1 for box in boxes),
        max(box.x2 for box in boxes),
        max(box.y2 for box in boxes),
    ]


def _expand_box(box: list[int], pad_x: int, pad_y: int) -> list[int]:
    return [box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y]


def _clamp_box(box: list[int], image_width: int, image_height: int) -> list[int]:
    x1 = max(0, min(image_width - 1, int(box[0])))
    y1 = max(0, min(image_height - 1, int(box[1])))
    x2 = max(x1 + 1, min(image_width, int(box[2])))
    y2 = max(y1 + 1, min(image_height, int(box[3])))
    return [x1, y1, x2, y2]


def _overlap_ratio(a: list[int], b: list[int]) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    overlap = (right - left) * (bottom - top)
    area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    return overlap / area
