from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from desktop_automation_perception.contracts import OCRBackend
from desktop_automation_perception.models import OCRExtractionResult, OCRTextBlock, OCRTextMatchResult


@dataclass(slots=True)
class TesseractOCRBackend:
    def capture_screenshot(self, region: tuple[int, int, int, int] | None = None):
        import pyautogui

        if region is None:
            return pyautogui.screenshot()
        left, top, right, bottom = region
        return pyautogui.screenshot(region=(left, top, right - left, bottom - top))

    def load_image(self, screenshot_path: str):
        from PIL import Image

        return Image.open(screenshot_path)

    def extract_blocks(self, image, language: str) -> list[OCRTextBlock]:
        import pytesseract

        data = pytesseract.image_to_data(
            image,
            lang=language,
            output_type=pytesseract.Output.DICT,
        )
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
    ) -> OCRTextMatchResult:
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

        if best_block is None or best_score < fuzzy_threshold:
            return OCRTextMatchResult(
                succeeded=False,
                target=target,
                confidence=best_score,
                matched_text=best_block.text if best_block else None,
                reason="No OCR text matched the target strongly enough.",
            )

        return OCRTextMatchResult(
            succeeded=True,
            target=target,
            bounds=best_block.bounds,
            confidence=best_score,
            matched_text=best_block.text,
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
