from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from time import time

from app.settings import PROCESSED_DIR
from app.models import LayoutRegion, PreliminaryLayout, OCRResult


@dataclass(frozen=True)
class ReceiptRegions:
    full: Path
    summary: Path
    line_items: Path
    line_item_chunks: list[Path]
    totals: Path


def create_receipt_regions(image_path: Path, receipt_id_hint: str) -> ReceiptRegions:
    """Create overlapping receipt crops for focused VLM passes."""
    from PIL import Image, ImageOps

    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        summary = _crop_vertical(img, width, height, 0.00, 0.42)
        line_items = _crop_vertical(img, width, height, 0.14, 0.88)
        totals = _crop_vertical(img, width, height, 0.58, 1.00)

        summary_path = PROCESSED_DIR / f"{receipt_id_hint}_summary.jpg"
        items_path = PROCESSED_DIR / f"{receipt_id_hint}_items.jpg"
        totals_path = PROCESSED_DIR / f"{receipt_id_hint}_totals.jpg"

        _save_vlm_region(summary, summary_path)
        _save_vlm_region(line_items, items_path)
        _save_vlm_region(totals, totals_path)
        chunk_paths = _save_line_item_chunks(line_items, receipt_id_hint)

    return ReceiptRegions(
        full=image_path,
        summary=summary_path,
        line_items=items_path,
        line_item_chunks=chunk_paths,
        totals=totals_path,
    )


def create_receipt_regions_from_layout(
    image_path: Path,
    receipt_id_hint: str,
    layout: PreliminaryLayout,
    ocr_result: OCRResult | None = None,
) -> ReceiptRegions:
    """Create focused VLM crops from preliminary OCR/layout regions."""
    from PIL import Image, ImageOps

    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        outline = layout.receipt_outline
        summary_box = _summary_box(layout, width, height)
        items_region = _region(layout, "line_items", min_confidence=0.30)
        totals_region = _region(layout, "totals", min_confidence=0.25)

        if outline and outline.confidence >= 0.25:
            default_items_box = _vertical_box(outline.box, 0.18, 0.78)
            default_totals_box = _vertical_box(outline.box, 0.62, 1.00)
        else:
            default_items_box = [0, int(height * 0.14), width, int(height * 0.88)]
            default_totals_box = [0, int(height * 0.58), width, height]

        items_crop_box = _clamp_box(items_region.box if items_region else default_items_box, width, height)
        summary = img.crop(tuple(_clamp_box(summary_box, width, height)))
        line_items = img.crop(tuple(items_crop_box))
        totals = img.crop(tuple(_clamp_box(totals_region.box if totals_region else default_totals_box, width, height)))

        summary_path = PROCESSED_DIR / f"{receipt_id_hint}_layout_summary.jpg"
        items_path = PROCESSED_DIR / f"{receipt_id_hint}_layout_items.jpg"
        totals_path = PROCESSED_DIR / f"{receipt_id_hint}_layout_totals.jpg"

        _save_vlm_region(summary, summary_path)
        _save_vlm_region(line_items, items_path)
        _save_vlm_region(totals, totals_path)
        chunk_paths = _save_line_item_chunks(
            line_items,
            f"{receipt_id_hint}_layout",
            ocr_result=ocr_result,
            crop_box=items_crop_box,
            image_width=width,
            image_height=height,
        )

    return ReceiptRegions(
        full=image_path,
        summary=summary_path,
        line_items=items_path,
        line_item_chunks=chunk_paths,
        totals=totals_path,
    )


def create_manual_line_item_chunks(
    image_path: Path,
    receipt_id: int,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    split_count: int,
    ocr_result: OCRResult | None = None,
) -> list[Path]:
    """Crop a user-selected itemized region and split it for focused VLM extraction."""
    from PIL import Image, ImageOps

    split_count = max(1, min(12, split_count))
    stamp = int(time())
    hint = f"receipt_{receipt_id}_manual_{stamp}"

    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        image_width, image_height = img.size
        left = max(0, min(image_width - 1, x))
        top = max(0, min(image_height - 1, y))
        right = max(left + 1, min(image_width, x + width))
        bottom = max(top + 1, min(image_height, y + height))

        crop = img.crop((left, top, right, bottom))
        crop_path = PROCESSED_DIR / f"{hint}_items.jpg"
        _save_vlm_region(crop.copy(), crop_path)
        return _save_line_item_chunks(
            crop,
            hint,
            chunk_count=split_count,
            ocr_result=ocr_result,
            crop_box=[left, top, right, bottom],
            image_width=image_width,
            image_height=image_height,
        )


def _crop_vertical(img, width: int, height: int, top_ratio: float, bottom_ratio: float):
    top = max(0, min(height, int(height * top_ratio)))
    bottom = max(top + 1, min(height, int(height * bottom_ratio)))
    return img.crop((0, top, width, bottom))


def _summary_box(layout: PreliminaryLayout, width: int, height: int) -> list[int]:
    vendor = _region(layout, "vendor", min_confidence=0.20)
    transaction = _region(layout, "transaction", min_confidence=0.20)
    boxes = [region.box for region in (vendor, transaction) if region]
    if boxes:
        return _union_boxes(boxes)
    if layout.receipt_outline:
        return _vertical_box(layout.receipt_outline.box, 0.0, 0.42)
    return [0, 0, width, int(height * 0.42)]


def _region(layout: PreliminaryLayout, name: str, *, min_confidence: float) -> LayoutRegion | None:
    for region in layout.regions:
        if region.name == name and region.confidence >= min_confidence and len(region.box) == 4:
            return region
    return None


def _vertical_box(box: list[int], top_ratio: float, bottom_ratio: float) -> list[int]:
    x1, y1, x2, y2 = box
    height = y2 - y1
    return [x1, y1 + int(height * top_ratio), x2, y1 + int(height * bottom_ratio)]


def _union_boxes(boxes: list[list[int]]) -> list[int]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _clamp_box(box: list[int], width: int, height: int) -> list[int]:
    x1 = max(0, min(width - 1, int(box[0])))
    y1 = max(0, min(height - 1, int(box[1])))
    x2 = max(x1 + 1, min(width, int(box[2])))
    y2 = max(y1 + 1, min(height, int(box[3])))
    return [x1, y1, x2, y2]


def _save_vlm_region(img, path: Path) -> None:
    max_side = int(os.getenv("RECEIPT_VLM_REGION_MAX_SIDE", "768"))
    img.thumbnail((max_side, max_side))
    img.save(path, "JPEG", quality=88)


def _save_line_item_chunks(
    line_items_img,
    receipt_id_hint: str,
    chunk_count: int | None = None,
    ocr_result: OCRResult | None = None,
    crop_box: list[int] | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> list[Path]:
    overlap_ratio = float(os.getenv("RECEIPT_LINE_ITEM_CHUNK_OVERLAP", "0.12"))
    width, height = line_items_img.size
    chunk_count = chunk_count or _line_item_chunk_count(height)
    
    if ocr_result and crop_box and image_width and image_height:
        try:
            cuts = _calculate_ocr_guided_cuts(
                height, chunk_count, ocr_result, crop_box, image_width, image_height
            )
        except Exception:
            cuts = [int((i + 1) * (height / chunk_count)) for i in range(chunk_count - 1)]
    else:
        cuts = [int((i + 1) * (height / chunk_count)) for i in range(chunk_count - 1)]

    chunk_height = height / chunk_count
    overlap = chunk_height * overlap_ratio
    paths: list[Path] = []

    for index in range(chunk_count):
        if index == 0:
            top = 0
        else:
            top = max(0, int(cuts[index - 1] - overlap))
            
        if index == chunk_count - 1:
            bottom = height
        else:
            bottom = min(height, int(cuts[index] + overlap))

        if bottom <= top:
            continue
        chunk = line_items_img.crop((0, top, width, bottom))
        path = PROCESSED_DIR / f"{receipt_id_hint}_items_{index + 1:02d}.jpg"
        _save_vlm_region(chunk, path)
        paths.append(path)

    return paths


def _calculate_ocr_guided_cuts(
    height: int,
    chunk_count: int,
    ocr_result: OCRResult,
    crop_box: list[int],
    image_width: int,
    image_height: int,
) -> list[int]:
    from app.layout import normalize_ocr_boxes

    pixel_boxes = normalize_ocr_boxes(ocr_result.boxes, image_width, image_height, ocr_result.engine)
    cx1, cy1, cx2, cy2 = crop_box

    scale_x = 1.0
    scale_y = 1.0
    if ocr_result.engine != "apple_vision":
        max_side = max(image_width, image_height)
        if max_side > 2000:
            scale = 2000 / max_side
            vlm_w = int(image_width * scale)
            vlm_h = int(image_height * scale)
            xs = [box.x2 for box in pixel_boxes]
            ys = [box.y2 for box in pixel_boxes]
            if xs and max(xs) <= vlm_w + 5 and ys and max(ys) <= vlm_h + 5:
                scale_x = image_width / vlm_w
                scale_y = image_height / vlm_h

    density = [0] * height

    for box in pixel_boxes:
        bx1 = int(box.x1 * scale_x)
        by1 = int(box.y1 * scale_y)
        bx2 = int(box.x2 * scale_x)
        by2 = int(box.y2 * scale_y)
        
        if not (by2 < cy1 or by1 > cy2):
            rel_y1 = max(0, min(height - 1, by1 - cy1))
            rel_y2 = max(rel_y1 + 1, min(height, by2 - cy1))
            
            for y in range(int(rel_y1), int(rel_y2)):
                density[y] += 1

    cuts: list[int] = []
    for index in range(1, chunk_count):
        ideal_y = index * (height / chunk_count)
        margin = int(height / (chunk_count * 2.5))
        search_start = max(0, int(ideal_y - margin))
        search_end = min(height - 1, int(ideal_y + margin))
        
        best_y = int(ideal_y)
        best_score = float("inf")
        
        for y in range(search_start, search_end + 1):
            score = density[y] * 1000 + abs(y - ideal_y)
            if score < best_score:
                best_score = score
                best_y = y
        
        cuts.append(best_y)
        
    return cuts


def _line_item_chunk_count(height: int) -> int:
    explicit = os.getenv("RECEIPT_LINE_ITEM_CHUNKS")
    if explicit:
        return max(1, int(explicit))

    target_height = max(160, int(os.getenv("RECEIPT_LINE_ITEM_TARGET_CHUNK_HEIGHT", "210")))
    min_chunks = max(1, int(os.getenv("RECEIPT_LINE_ITEM_MIN_CHUNKS", "2")))
    max_chunks = max(min_chunks, int(os.getenv("RECEIPT_LINE_ITEM_MAX_CHUNKS", "5")))
    estimated = (height + target_height - 1) // target_height
    return max(min_chunks, min(max_chunks, estimated))
