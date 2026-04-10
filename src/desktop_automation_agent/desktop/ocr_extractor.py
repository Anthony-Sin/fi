from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from desktop_automation_agent.contracts import OCRBackend
from desktop_automation_agent.models import OCRExtractionResult, OCRTextBlock, OCRTextMatchResult


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TesseractOCRBackend:
    def capture_screenshot(self, region: tuple[int, int, int, int] | None = None) -> Any | None:
        try:
            import pyautogui

            if region is None:
                return pyautogui.screenshot()
            left, top, right, bottom = region
            return pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        except Exception as e:
            logger.warning(f"OCR capture screenshot failed: {e}")
            return None

    def load_image(self, screenshot_path: str) -> Any | None:
        try:
            from PIL import Image

            return Image.open(screenshot_path)
        except Exception as e:
            logger.warning(f"OCR load image failed: {e}")
            return None

    def extract_blocks(self, image: Any, language: str) -> list[OCRTextBlock]:
        if image is None:
            return []
        try:
            import pytesseract

            data = pytesseract.image_to_data(
                image,
                lang=language,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as e:
            logger.warning(f"pytesseract extraction failed: {e}")
            return []
        blocks: list[OCRTextBlock] = []

        for index, text in enumerate(data["text"]):
            normalized = (text or "").strip()
            if not normalized:
                continue
            confidence_value = float(data["conf"][index])
            left = int(data["left"][index])
            top = int(data["top"][index])
            width = int(data["width"][index])
            height = int(data["height"][index])
            blocks.append(
                OCRTextBlock(
                    text=normalized,
                    confidence=max(0.0, min(confidence_value / 100.0, 1.0)),
                    bounds=(left, top, left + width, top + height),
                )
            )

        return blocks


@dataclass(slots=True)
class OCRExtractor:
    backend: OCRBackend
    ai_fallback: object | None = None

    def extract_text(
        self,
        *,
        screenshot_path: str | None = None,
        region_of_interest: tuple[int, int, int, int] | None = None,
        language: str = "eng",
        minimum_confidence: float = 0.0,
    ) -> OCRExtractionResult:
        image = (
            self.backend.load_image(screenshot_path)
            if screenshot_path is not None
            else self.backend.capture_screenshot(region_of_interest)
        )
        if image is None:
            return OCRExtractionResult(
                blocks=[],
                language=language,
                region_of_interest=region_of_interest,
            )
        blocks = self.backend.extract_blocks(image, language)
        filtered = [
            self._offset_block(block, region_of_interest)
            for block in blocks
            if block.confidence >= minimum_confidence
        ]
        return OCRExtractionResult(
            blocks=filtered,
            language=language,
            region_of_interest=region_of_interest,
        )

    def find_text(
        self,
        *,
        target: str,
        screenshot_path: str | None = None,
        region_of_interest: tuple[int, int, int, int] | None = None,
        language: str = "eng",
        minimum_confidence: float = 0.0,
        fuzzy_threshold: float = 0.72,
        ai_fallback: object | None = None,
    ) -> OCRTextMatchResult:
        ai_fallback = ai_fallback or self.ai_fallback
        extraction = self.extract_text(
            screenshot_path=screenshot_path,
            region_of_interest=region_of_interest,
            language=language,
            minimum_confidence=minimum_confidence,
        )
        best_block: OCRTextBlock | None = None
        best_score = 0.0
        target_normalized = self._normalize(target)

        for block in extraction.blocks:
            score = self._score_match(target_normalized, self._normalize(block.text))
            if score > best_score:
                best_score = score
                best_block = block

        if best_block is not None and best_score >= fuzzy_threshold:
            return OCRTextMatchResult(
                succeeded=True,
                target=target,
                bounds=best_block.bounds,
                confidence=best_score,
                matched_text=best_block.text,
            )

        # AI Fallback
        if ai_fallback is not None:
            image = (
                self.backend.load_image(screenshot_path)
                if screenshot_path is not None
                else self.backend.capture_screenshot(region_of_interest)
            )
            prompt = f"Find the coordinates of the text '{target}' in this image. Return the coordinates as [left, top, right, bottom]. If not found, say 'NOT_FOUND'."
            try:
                ai_response = ai_fallback.analyze_image(prompt, image)
                if "NOT_FOUND" not in ai_response:
                    # Simple regex to extract coordinates like [10, 20, 100, 50]
                    import re
                    coords = re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", ai_response)
                    if coords:
                        bounds = tuple(map(int, coords[0]))
                        return OCRTextMatchResult(
                            succeeded=True,
                            target=target,
                            bounds=bounds,
                            confidence=0.95, # High confidence for AI vision
                            matched_text=target,
                            detail="Found via AI vision fallback."
                        )
            except Exception as e:
                logger.warning(f"AI Fallback failed: {e}")

        return OCRTextMatchResult(
            succeeded=False,
            target=target,
            confidence=best_score,
            matched_text=best_block.text if best_block else None,
            reason="No OCR text matched the target strongly enough.",
        )

    def _score_match(self, target: str, candidate: str) -> float:
        if candidate == target:
            return 1.0
        if target in candidate:
            return 0.92
        if candidate in target:
            return 0.88
        return SequenceMatcher(None, target, candidate).ratio()

    def _normalize(self, text: str) -> str:
        return " ".join(text.casefold().split())

    def _offset_block(
        self,
        block: OCRTextBlock,
        region_of_interest: tuple[int, int, int, int] | None,
    ) -> OCRTextBlock:
        if region_of_interest is None:
            return block
        left_offset, top_offset, _, _ = region_of_interest
        left, top, right, bottom = block.bounds
        return OCRTextBlock(
            text=block.text,
            confidence=block.confidence,
            bounds=(
                left + left_offset,
                top + top_offset,
                right + left_offset,
                bottom + top_offset,
            ),
        )
