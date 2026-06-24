from __future__ import annotations

from pathlib import Path

from app.settings import PROCESSED_DIR


def preprocess_image(input_path: Path, receipt_id_hint: str) -> Path:
    """Create an OCR-friendly derivative while preserving the original unchanged."""
    output_path = PROCESSED_DIR / f"{receipt_id_hint}.png"

    try:
        import cv2
        import numpy as np

        image = cv2.imread(str(input_path))
        if image is None:
            raise ValueError(f"OpenCV could not read {input_path}")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
        gray = cv2.convertScaleAbs(gray, alpha=1.35, beta=8)

        height, width = gray.shape[:2]
        max_side = max(height, width)
        if max_side > 2200:
            scale = 2200 / max_side
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(gray, -1, kernel)
        cv2.imwrite(str(output_path), sharpened)
        return output_path
    except Exception:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        with Image.open(input_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("L")
            img.thumbnail((2200, 2200))
            img = ImageEnhance.Contrast(img).enhance(1.4)
            img = img.filter(ImageFilter.SHARPEN)
            img.save(output_path)
        return output_path


def preprocess_image_for_vlm(input_path: Path, receipt_id_hint: str) -> Path:
    """Create a VLM-friendly color derivative (upright, color-preserved, resized)."""
    output_path = PROCESSED_DIR / f"{receipt_id_hint}_vlm.jpg"

    try:
        from PIL import Image, ImageEnhance, ImageOps

        with Image.open(input_path) as img:
            # 1. Correct EXIF orientation (auto-rotation)
            img = ImageOps.exif_transpose(img)

            # 2. Keep in color (VLM uses color semantic clues)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # 3. Dynamic resizing (resizing to max 1280px on the longest side to speed up inference)
            img.thumbnail((1280, 1280))

            # 4. Mild contrast enhancement to assist low-light/faint prints
            img = ImageEnhance.Contrast(img).enhance(1.15)

            img.save(output_path, "JPEG", quality=90)
        return output_path
    except Exception:
        # Fallback to copy if PIL fails
        import shutil

        shutil.copy2(input_path, output_path)
        return output_path


def auto_rotate_image_if_needed(image_path: Path) -> bool:
    """Detect if receipt image is rotated 90/180/270 degrees and correct it in-place."""
    import platform
    if platform.system() != "Darwin":
        return False

    try:
        from PIL import Image, ImageOps
        from Foundation import NSURL
        from Vision import (
            VNImageRequestHandler,
            VNRecognizeTextRequest,
            VNRequestTextRecognitionLevelAccurate,
        )
    except Exception:
        return False

    def check_orientation(img_p: Path) -> tuple[float, bool]:
        with Image.open(img_p) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
            
        url = NSURL.fileURLWithPath_(str(img_p))
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        success, _ = handler.performRequests_error_([request], None)
        if not success:
            return 0.0, False
            
        results = request.results() or []
        tall_lines = 0
        wide_lines = 0
        y_centers = []
        for obs in results:
            rect = obs.boundingBox()
            w_px = rect.size.width * w
            h_px = rect.size.height * h
            # Convert bottom-left coordinates of Apple Vision to standard top-left space for Y flow checking
            y_px = (1.0 - (rect.origin.y + rect.size.height / 2)) * h
            
            if h_px > w_px * 1.5:
                tall_lines += 1
            elif w_px > h_px * 1.5:
                wide_lines += 1
            y_centers.append(y_px)
            
        total = tall_lines + wide_lines
        vertical_ratio = tall_lines / total if total >= 4 else 0.0
        
        is_upside_down = False
        if len(y_centers) >= 4:
            k = max(1, len(y_centers) // 5)
            first_avg = sum(y_centers[:k]) / k
            last_avg = sum(y_centers[-k:]) / k
            if first_avg > last_avg:
                is_upside_down = True
                
        return vertical_ratio, is_upside_down

    try:
        ratio, is_upside_down = check_orientation(image_path)
        if ratio > 0.6:
            # Sideways rotation! Let's rotate 90 degrees Clockwise first (ROTATE_270 in PIL terms)
            temp_path = image_path.with_name(f"temp_rot_{image_path.name}")
            with Image.open(image_path) as img:
                img = ImageOps.exif_transpose(img)
                rotated = img.transpose(Image.ROTATE_270)
                rotated.save(temp_path, "JPEG", quality=95)
                
            try:
                new_ratio, new_upside_down = check_orientation(temp_path)
                if new_ratio <= 0.4 and not new_upside_down:
                    # 90 degrees Clockwise was correct!
                    with Image.open(image_path) as img:
                        img = ImageOps.exif_transpose(img)
                        rotated = img.transpose(Image.ROTATE_270)
                        if "exif" in rotated.info:
                            del rotated.info["exif"]
                        rotated.save(image_path, "JPEG", quality=95)
                    print(f"Auto-rotated {image_path.name} 90 degrees Clockwise to make it upright.")
                    return True
                else:
                    # Otherwise, rotate 90 degrees Counter-Clockwise (ROTATE_90)
                    with Image.open(image_path) as img:
                        img = ImageOps.exif_transpose(img)
                        rotated = img.transpose(Image.ROTATE_90)
                        if "exif" in rotated.info:
                            del rotated.info["exif"]
                        rotated.save(image_path, "JPEG", quality=95)
                    print(f"Auto-rotated {image_path.name} 90 degrees Counter-Clockwise to make it upright.")
                    return True
            finally:
                temp_path.unlink(missing_ok=True)
        elif is_upside_down:
            # Image text runs horizontally, but upside down (180 degrees)
            with Image.open(image_path) as img:
                img = ImageOps.exif_transpose(img)
                rotated = img.transpose(Image.ROTATE_180)
                if "exif" in rotated.info:
                    del rotated.info["exif"]
                rotated.save(image_path, "JPEG", quality=95)
            print(f"Auto-rotated {image_path.name} 180 degrees to correct reading orientation.")
            return True
    except Exception as exc:
        print(f"Auto-rotation failed: {exc}")
        
    return False

