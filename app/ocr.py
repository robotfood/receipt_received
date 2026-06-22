from __future__ import annotations

from pathlib import Path
import platform

from app.models import OCRBox, OCRResult


def run_ocr(image_path: Path) -> OCRResult:
    """Run the first available local OCR backend."""
    for runner in (_run_apple_vision, _run_paddleocr, _run_pytesseract):
        result = runner(image_path)
        if result is not None:
            return result

    return OCRResult(
        engine="none",
        raw_text="",
        boxes=[],
        warnings=[
            "No OCR backend is installed. Install PaddleOCR or pytesseract/Tesseract to extract text."
        ],
    )


def _run_apple_vision(image_path: Path) -> OCRResult | None:
    if platform.system() != "Darwin":
        return None

    try:
        from Foundation import NSURL
        from Vision import (
            VNImageRequestHandler,
            VNRecognizeTextRequest,
            VNRequestTextRecognitionLevelAccurate,
        )
    except Exception:
        return None

    try:
        url = NSURL.fileURLWithPath_(str(image_path))
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)

        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        success, error = handler.performRequests_error_([request], None)
        if not success:
            message = str(error) if error else "unknown Vision error"
            return OCRResult(engine="apple_vision", raw_text="", warnings=[f"Apple Vision OCR failed: {message}"])

        boxes: list[OCRBox] = []
        lines: list[str] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string()).strip()
            if not text:
                continue
            confidence = float(candidate.confidence())
            rect = observation.boundingBox()
            box = [
                float(rect.origin.x),
                float(rect.origin.y),
                float(rect.origin.x + rect.size.width),
                float(rect.origin.y),
                float(rect.origin.x + rect.size.width),
                float(rect.origin.y + rect.size.height),
                float(rect.origin.x),
                float(rect.origin.y + rect.size.height),
            ]
            lines.append(text)
            boxes.append(OCRBox(text=text, confidence=confidence, box=box))

        return OCRResult(engine="apple_vision", raw_text="\n".join(lines), boxes=boxes)
    except Exception as exc:
        return OCRResult(engine="apple_vision", raw_text="", warnings=[f"Apple Vision OCR failed: {exc}"])


def _run_paddleocr(image_path: Path) -> OCRResult | None:
    try:
        from paddleocr import PaddleOCR
    except Exception:
        return None

    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        raw = ocr.ocr(str(image_path), cls=True)
        boxes: list[OCRBox] = []
        lines: list[str] = []
        for page in raw or []:
            for item in page or []:
                box, text_info = item
                text, confidence = text_info
                lines.append(text)
                flat_box = [float(v) for point in box for v in point]
                boxes.append(OCRBox(text=text, confidence=float(confidence), box=flat_box))
        return OCRResult(engine="paddleocr", raw_text="\n".join(lines), boxes=boxes)
    except Exception as exc:
        return OCRResult(engine="paddleocr", raw_text="", warnings=[f"PaddleOCR failed: {exc}"])


def _run_pytesseract(image_path: Path) -> OCRResult | None:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(image_path) as img:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        boxes: list[OCRBox] = []
        lines: list[str] = []
        count = len(data.get("text", []))
        for index in range(count):
            text = (data["text"][index] or "").strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][index]) / 100
            except ValueError:
                confidence = 0.0
            left = float(data["left"][index])
            top = float(data["top"][index])
            width = float(data["width"][index])
            height = float(data["height"][index])
            boxes.append(
                OCRBox(
                    text=text,
                    confidence=max(confidence, 0.0),
                    box=[left, top, left + width, top, left + width, top + height, left, top + height],
                )
            )
            lines.append(text)
        return OCRResult(engine="pytesseract", raw_text="\n".join(lines), boxes=boxes)
    except Exception as exc:
        return OCRResult(engine="pytesseract", raw_text="", warnings=[f"pytesseract failed: {exc}"])
