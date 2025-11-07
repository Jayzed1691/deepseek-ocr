"""
Smart PDF Loader with Hybrid Text Extraction
Tries native text extraction first, falls back to OCR when needed
"""

import io
import fitz  # PyMuPDF
from PIL import Image, ImageOps
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class ExtractionMethod(Enum):
    """Method used to extract text from a page"""
    TEXT = "text"           # Native text extraction
    OCR = "ocr"             # OCR processing
    HYBRID = "hybrid"       # Mixed (some text, some OCR)
    EMPTY = "empty"         # No content found


@dataclass
class PageResult:
    """Result from processing a single page"""
    page_number: int
    text: str
    method: ExtractionMethod
    image: Optional[Image.Image] = None
    char_count: int = 0
    processing_time: float = 0.0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        self.char_count = len(self.text)


@dataclass
class ProcessingStats:
    """Statistics about the processing session"""
    total_pages: int = 0
    text_extracted_pages: int = 0
    ocr_processed_pages: int = 0
    empty_pages: int = 0
    total_chars: int = 0
    total_time: float = 0.0
    text_extraction_time: float = 0.0
    ocr_processing_time: float = 0.0

    @property
    def text_percentage(self) -> float:
        """Percentage of pages that used text extraction"""
        if self.total_pages == 0:
            return 0.0
        return (self.text_extracted_pages / self.total_pages) * 100

    @property
    def ocr_percentage(self) -> float:
        """Percentage of pages that required OCR"""
        if self.total_pages == 0:
            return 0.0
        return (self.ocr_processed_pages / self.total_pages) * 100

    @property
    def average_time_per_page(self) -> float:
        """Average processing time per page"""
        if self.total_pages == 0:
            return 0.0
        return self.total_time / self.total_pages

    @property
    def speedup_estimate(self) -> float:
        """Estimated speedup vs pure OCR approach"""
        if self.total_pages == 0 or self.ocr_processed_pages == 0:
            return 1.0

        # Estimate: text extraction is ~50x faster than OCR
        avg_ocr_time = self.ocr_processing_time / max(self.ocr_processed_pages, 1)
        hypothetical_all_ocr_time = self.total_pages * avg_ocr_time

        if hypothetical_all_ocr_time == 0:
            return 1.0

        return hypothetical_all_ocr_time / max(self.total_time, 0.001)


class SmartPDFLoader:
    """
    Intelligent PDF loader that uses hybrid text extraction.

    Strategy:
    1. Try native text extraction first (fast, 100% accurate)
    2. If page has minimal text, fall back to OCR
    3. Track statistics for performance analysis
    """

    def __init__(
        self,
        text_threshold: int = 50,
        force_ocr: bool = False,
        dpi: int = 144,
        enable_image_extraction: bool = True
    ):
        """
        Initialize the smart PDF loader.

        Args:
            text_threshold: Minimum characters to consider page as having text
            force_ocr: Always use OCR, ignore text extraction
            dpi: DPI for image conversion (when OCR is needed)
            enable_image_extraction: Whether to extract images for visualization
        """
        self.text_threshold = text_threshold
        self.force_ocr = force_ocr
        self.dpi = dpi
        self.enable_image_extraction = enable_image_extraction
        self.stats = ProcessingStats()

    def load_pdf(
        self,
        pdf_bytes: bytes,
        ocr_callback: Optional[callable] = None
    ) -> Tuple[List[PageResult], ProcessingStats]:
        """
        Load and process a PDF with hybrid extraction.

        Args:
            pdf_bytes: PDF file content as bytes
            ocr_callback: Optional callback function for OCR processing
                         Signature: ocr_callback(page_image) -> text

        Returns:
            Tuple of (list of PageResult objects, ProcessingStats)
        """
        import time

        pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
        results = []

        self.stats = ProcessingStats()
        self.stats.total_pages = pdf_document.page_count

        start_time = time.time()

        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            page_start = time.time()

            # Get page image (for visualization or OCR)
            page_image = None
            if self.enable_image_extraction or self.force_ocr:
                page_image = self._page_to_image(page)

            # Determine extraction method
            if self.force_ocr:
                # Force OCR mode
                text, method = self._extract_with_ocr(page, page_image, ocr_callback)
                self.stats.ocr_processed_pages += 1
            else:
                # Smart hybrid mode
                text, method = self._smart_extract(page, page_image, ocr_callback)

                if method == ExtractionMethod.TEXT:
                    self.stats.text_extracted_pages += 1
                elif method == ExtractionMethod.OCR:
                    self.stats.ocr_processed_pages += 1
                elif method == ExtractionMethod.EMPTY:
                    self.stats.empty_pages += 1

            page_time = time.time() - page_start

            # Track timing
            if method == ExtractionMethod.TEXT:
                self.stats.text_extraction_time += page_time
            elif method == ExtractionMethod.OCR:
                self.stats.ocr_processing_time += page_time

            self.stats.total_chars += len(text)

            # Create result
            result = PageResult(
                page_number=page_num + 1,
                text=text,
                method=method,
                image=page_image,
                processing_time=page_time,
                metadata={
                    'width': page.rect.width,
                    'height': page.rect.height,
                    'rotation': page.rotation
                }
            )
            results.append(result)

        self.stats.total_time = time.time() - start_time
        pdf_document.close()

        return results, self.stats

    def _smart_extract(
        self,
        page,
        page_image: Optional[Image.Image],
        ocr_callback: Optional[callable]
    ) -> Tuple[str, ExtractionMethod]:
        """
        Smart extraction: try text first, fall back to OCR.

        Args:
            page: PyMuPDF page object
            page_image: PIL Image of the page
            ocr_callback: OCR function

        Returns:
            Tuple of (extracted_text, method_used)
        """
        # Try text extraction first
        text = page.get_text()

        # Check if page has sufficient text
        if len(text.strip()) >= self.text_threshold:
            # Success! Use native text
            return text.strip(), ExtractionMethod.TEXT

        # Text extraction insufficient, try OCR
        if ocr_callback and page_image:
            ocr_text = ocr_callback(page_image)
            if ocr_text and len(ocr_text.strip()) > 0:
                return ocr_text.strip(), ExtractionMethod.OCR

        # Nothing found
        return text.strip(), ExtractionMethod.EMPTY

    def _extract_with_ocr(
        self,
        page,
        page_image: Image.Image,
        ocr_callback: Optional[callable]
    ) -> Tuple[str, ExtractionMethod]:
        """
        Force OCR extraction (ignore native text).

        Args:
            page: PyMuPDF page object
            page_image: PIL Image of the page
            ocr_callback: OCR function

        Returns:
            Tuple of (extracted_text, method_used)
        """
        if ocr_callback and page_image:
            text = ocr_callback(page_image)
            return text.strip() if text else "", ExtractionMethod.OCR

        return "", ExtractionMethod.EMPTY

    def _page_to_image(self, page) -> Image.Image:
        """
        Convert PDF page to PIL Image.

        Args:
            page: PyMuPDF page object

        Returns:
            PIL Image
        """
        zoom = self.dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        img_data = pixmap.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # Correct orientation
        corrected_image = ImageOps.exif_transpose(img)
        return corrected_image.convert('RGB')


def format_stats_summary(stats: ProcessingStats) -> str:
    """
    Format processing statistics as a human-readable summary.

    Args:
        stats: ProcessingStats object

    Returns:
        Formatted string
    """
    lines = [
        f"📊 Processing Summary",
        f"",
        f"Total Pages: {stats.total_pages}",
        f"Text Extracted: {stats.text_extracted_pages} ({stats.text_percentage:.1f}%)",
        f"OCR Processed: {stats.ocr_processed_pages} ({stats.ocr_percentage:.1f}%)",
        f"Empty Pages: {stats.empty_pages}",
        f"",
        f"Total Characters: {stats.total_chars:,}",
        f"Total Time: {stats.total_time:.2f}s",
        f"Avg Time/Page: {stats.average_time_per_page:.2f}s",
        f"",
        f"⚡ Estimated Speedup vs Pure OCR: {stats.speedup_estimate:.1f}x"
    ]
    return "\n".join(lines)


# Example usage and testing
if __name__ == "__main__":
    import sys

    # Mock OCR callback for testing
    def mock_ocr(image):
        return f"[OCR placeholder text for {image.size} image]"

    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        loader = SmartPDFLoader(text_threshold=50, force_ocr=False)
        results, stats = loader.load_pdf(pdf_bytes, ocr_callback=mock_ocr)

        print(format_stats_summary(stats))
        print("\n" + "="*50 + "\n")

        for result in results[:3]:  # Show first 3 pages
            print(f"Page {result.page_number} ({result.method.value}):")
            print(f"  Chars: {result.char_count}")
            print(f"  Time: {result.processing_time:.2f}s")
            print(f"  Text preview: {result.text[:100]}...")
            print()
    else:
        print("Usage: python smart_pdf_loader.py <pdf_file>")
