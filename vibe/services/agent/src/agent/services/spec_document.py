"""Document extraction for the stateless reviewer (no RAG or database imports)."""

import csv
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal
from unicodedata import category
from zlib import error as ZlibError

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from agent.services.spec_office import OfficeDocumentError, OfficeDocumentLimit, extract_office_units
from agent.services.spec_ocr import (
    MAX_OCR_SECONDS, ImageDocumentError, ImageDocumentLimit, image_kind,
    ocr_images, ocr_pdf_pages, probe_pdf_page_count,
)

MAX_REVIEW_FILE_BYTES = 25 * 1024 * 1024
MAX_REVIEW_CHARACTERS = 100_000
MAX_REVIEW_PAGES = 150
EXTRACTION_CHUNK_CHARACTERS = 5_000
SPARSE_SCAN_ALNUM_LIMIT = 120
SCAN_OCR_MIN_ADDITIONAL_ALNUM = 8
Source = Literal["pdf", "txt", "md", "docx", "xlsx", "csv", "jpg", "jpeg", "png"]
IMAGE_SOURCES = {"jpg", "jpeg", "png"}

# Malformed PDF structures and unsupported content streams can fail at
# different layers of pypdf. Keep the original page position on these errors.
PDF_READ_ERRORS = (
    PyPdfError, KeyError, TypeError, ValueError, RecursionError, IndexError,
    AttributeError, OverflowError, NotImplementedError, OSError, ZlibError,
)


class SpecificationValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_quote(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Specification:
    filename: str
    source: Source
    units: list[str]
    sha256: str | None = None
    ocr_pages: list[int] = field(default_factory=list)
    ocr_units: list[int] = field(default_factory=list)
    unreadable_units: list[int] = field(default_factory=list)

    @property
    def unreadable_pages(self) -> list[int]:
        return (
            [i for i, unit in enumerate(self.units, 1) if not unit.strip()]
            if self.source == "pdf"
            else []
        )

    def numbered_units(self) -> list[dict[str, str | int | bool]]:
        return [
            {
                "index": index, "text": text,
                **({"ocr": True} if index in self.ocr_units or index in self.ocr_pages else {}),
                **({"unreadable": True} if index in self.unreadable_units else {}),
            }
            for index, text in enumerate(self.units, 1)
        ]

    def extraction_chunks(self) -> list[tuple[int, int]]:
        """Partition complete original units, never truncate a line or page."""
        chunks = []
        start, size = 1, 0
        for index, unit in enumerate(self.units, 1):
            if index > start and (size + len(unit) + 1 > EXTRACTION_CHUNK_CHARACTERS or index - start >= 500):
                chunks.append((start, index - 1))
                start, size = index, 0
            size += len(unit) + 1
        chunks.append((start, len(self.units)))
        return chunks

    def verify_quote(self, start: int, end: int, quote: str) -> None:
        if not 1 <= start <= end <= len(self.units):
            raise ValueError("Invalid source location")
        if self.source == "pdf" and any(
            not unit.strip() for unit in self.units[start - 1 : end]
        ):
            raise ValueError("Citation crosses an unreadable page")
        if any(start <= index <= end for index in self.unreadable_units):
            raise ValueError("Citation crosses an unreadable image block")
        if not normalize_quote(quote) or normalize_quote(quote) not in normalize_quote(
            " ".join(self.units[start - 1 : end])
        ):
            raise ValueError("Quote is absent from the cited source")

    def location_text(self, start: int, end: int) -> str:
        label = "стр." if self.source == "pdf" else ("изображение" if start == end else "изображения") if self.source in IMAGE_SOURCES else ("блок" if start == end else "блоки") if self.source == "docx" else ("строка выгрузки" if start == end else "строки выгрузки") if self.source == "xlsx" else "строка" if start == end else "строки"
        return f"{label} {start}" + (f"–{end}" if start != end else "")


def extract_specification(
    filename: str, content_type: str, content: bytes, *,
    max_file_bytes: int = MAX_REVIEW_FILE_BYTES,
    max_characters: int = MAX_REVIEW_CHARACTERS,
    max_pages: int = MAX_REVIEW_PAGES,
    ocr_seconds: int = MAX_OCR_SECONDS,
) -> Specification:
    filename = Path(filename.replace("\\", "/")).name.strip()
    if (
        not filename
        or filename in {".", ".."}
        or any(ord(c) < 32 for c in filename)
        or len(filename.encode("utf-8")) > 255
    ):
        raise SpecificationValidationError(
            "INVALID_FILENAME", "A valid filename is required"
        )
    suffix = Path(filename).suffix.lower()
    allowed_content_types = {
        ".pdf": {"application/pdf"},
        ".txt": {"text/plain"},
        ".md": {"text/markdown", "text/plain", "text/x-markdown"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
        ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
        ".csv": {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"},
        ".jpg": {"image/jpeg", "image/jpg"},
        ".jpeg": {"image/jpeg", "image/jpg"},
        ".png": {"image/png"},
    }
    if suffix not in allowed_content_types:
        raise SpecificationValidationError(
            "UNSUPPORTED_FILE_TYPE", "Only PDF, TXT, Markdown, DOCX, XLSX, CSV, JPG, JPEG and PNG files are supported"
        )
    mime = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if mime != "application/octet-stream" and mime not in allowed_content_types[suffix]:
        raise SpecificationValidationError(
            "CONTENT_TYPE_MISMATCH", "Content type does not match the filename"
        )
    if len(content) > max_file_bytes:
        raise SpecificationValidationError(
            "FILE_TOO_LARGE", f"File exceeds the {max_file_bytes // (1024 * 1024)} MiB review limit"
        )
    if not content:
        raise SpecificationValidationError("EMPTY_FILE", "Uploaded file is empty")
    source: Source
    ocr_pages: list[int] = []
    ocr_units: list[int] = []
    unreadable_units: list[int] = []
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise SpecificationValidationError("INVALID_PDF", "File is not a valid PDF")
        source = "pdf"
        units, ocr_pages = extract_pdf_pages(content, max_pages=max_pages, max_characters=max_characters, ocr_seconds=ocr_seconds)
    elif suffix in {".docx", ".xlsx"}:
        source = "docx" if suffix == ".docx" else "xlsx"
        try:
            office = extract_office_units(content, source, max_characters, max_images=max_pages)
            units = office.units
        except OfficeDocumentLimit as error:
            raise text_limit_error(max_characters, max_pages) from error
        except OfficeDocumentError as error:
            raise SpecificationValidationError("INVALID_OFFICE", "The Office document could not be read") from error
        if office.images:
            available = {index: image for index, image in office.images.items() if image is not None}
            try:
                recognized = ocr_images(available, max_seconds=ocr_seconds) if available else {}
            except OSError:
                recognized = {}
            for index in office.images:
                text = recognized.get(index, "").strip()
                if text:
                    units[index - 1] = text
                    ocr_units.append(index)
                else:
                    unreadable_units.append(index)
            if sum(len(unit) + 1 for unit in units) > max_characters + 1:
                raise text_limit_error(max_characters, max_pages)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        source = "png" if suffix == ".png" else "jpeg" if suffix == ".jpeg" else "jpg"
        try:
            expected = "png" if source == "png" else "jpg"
            if image_kind(content) != expected:
                raise ImageDocumentError("Image type does not match the file extension")
        except ImageDocumentLimit as error:
            raise SpecificationValidationError("REVIEW_TEXT_LIMIT", str(error)) from error
        except ImageDocumentError as error:
            raise SpecificationValidationError("INVALID_IMAGE", str(error)) from error
        try:
            recognized = ocr_images({1: content}, max_seconds=ocr_seconds)
        except OSError:
            recognized = {}
        units = [recognized.get(1, "").strip()]
        if len(units[0]) > max_characters:
            raise text_limit_error(max_characters, max_pages)
        if units[0]:
            ocr_units = [1]
    elif suffix == ".csv":
        source = "csv"
        units = extract_csv_rows(content, max_characters, max_pages)
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SpecificationValidationError(
                "INVALID_TEXT", "Save text files as UTF-8"
            ) from error
        if "\x00" in text:
            raise SpecificationValidationError(
                "INVALID_TEXT", "Text file contains binary data"
            )
        if len(text) > max_characters:
            raise text_limit_error(max_characters, max_pages)
        source = "md" if suffix == ".md" else "txt"
        units = text.splitlines()
    if not any(unit.strip() for unit in units):
        raise SpecificationValidationError(
            "REVIEW_NO_TEXT", "The document has no readable text"
        )
    return Specification(filename, source, units, sha256(content).hexdigest(), ocr_pages, ocr_units, unreadable_units)


def text_limit_error(max_characters: int = MAX_REVIEW_CHARACTERS, max_pages: int = MAX_REVIEW_PAGES) -> SpecificationValidationError:
    return SpecificationValidationError(
        "REVIEW_TEXT_LIMIT",
        f"Use no more than {max_characters} characters and {max_pages} PDF pages",
    )


def extract_csv_rows(content: bytes, max_characters: int, max_pages: int) -> list[str]:
    try:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1251")
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            raise ValueError("CSV contains binary control characters")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        units, size = [], 0
        for row in csv.reader(StringIO(text, newline=""), dialect, strict=True):
            unit = " | ".join(row)
            size += len(unit) + 1
            if size > max_characters + 1:
                raise text_limit_error(max_characters, max_pages)
            units.append(unit)
        return units
    except SpecificationValidationError:
        raise
    except (UnicodeDecodeError, ValueError, csv.Error) as error:
        raise SpecificationValidationError("INVALID_TEXT", "The CSV document could not be read") from error


def _obviously_garbled(text: str) -> bool:
    """Identify severe font/encoding failures, not merely an unfamiliar script."""
    nonspace = sum(not char.isspace() for char in text)
    invalid = sum(
        char == "\ufffd" or category(char) in {"Cc", "Co", "Cs", "Cn"}
        for char in text
        if not char.isspace()
    )
    # Individual private-use bullet glyphs are common in otherwise valid text.
    return nonspace > 0 and invalid * 10 >= nonspace


def _pdf_page_text(page) -> tuple[str, bool]:
    """Read text and detect a dominant painted image without decoding pixels."""
    large_raster = False

    def inspect_image(operator, operands, matrix, _text_matrix):
        nonlocal large_raster
        if large_raster or operator not in {b"Do", b"INLINE IMAGE"}:
            return
        if operator == b"Do":
            resources = page.get("/Resources")
            xobjects = resources.get_object().get("/XObject") if resources else None
            image = xobjects.get_object().get(operands[0]) if xobjects else None
            if image is None or image.get_object().get("/Subtype") != "/Image":
                return
        # The current transformation matrix maps the unit image square into
        # page coordinates. A small logo may have many pixels; its painted
        # size, not its resolution, distinguishes it from a page-sized scan.
        a, b, c, d, e, f = map(float, matrix)
        crop = page.cropbox
        width, height = float(crop.width), float(crop.height)
        if width <= 0 or height <= 0:
            return
        xs = (e, e + a, e + c, e + a + c)
        ys = (f, f + b, f + d, f + b + d)
        visible_width = max(
            0, min(max(xs), float(crop.right)) - max(min(xs), float(crop.left))
        )
        visible_height = max(
            0, min(max(ys), float(crop.top)) - max(min(ys), float(crop.bottom))
        )
        area = min(abs(a * d - b * c), visible_width * visible_height)
        large_raster = area >= width * height / 2

    text = page.extract_text(visitor_operand_before=inspect_image) or ""
    return text, large_raster


def extract_pdf_pages(content: bytes, *, max_pages: int = MAX_REVIEW_PAGES, max_characters: int = MAX_REVIEW_CHARACTERS, ocr_seconds: int = MAX_OCR_SECONDS) -> tuple[list[str], list[int]]:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise SpecificationValidationError(
                "INVALID_PDF", "Remove the PDF password before uploading"
            )
        page_count = len(reader.pages)
    except SpecificationValidationError:
        raise
    except PDF_READ_ERRORS as error:
        # Poppler may still render a PDF with a damaged cross-reference or page
        # tree. Never guess the number of pages from the bytes of the file.
        page_count = probe_pdf_page_count(content)
        if page_count is None:
            raise SpecificationValidationError(
                "INVALID_PDF", "The PDF could not be read; export or repair the original file"
            ) from error
        reader = None
    if page_count > max_pages:
        raise text_limit_error(max_characters, max_pages)

    pages = []
    character_count = 0
    missing = []
    sparse_scan_lengths = {}
    for index in range(page_count):
        try:
            text, large_raster = (
                _pdf_page_text(reader.pages[index]) if reader is not None else ("", False)
            )
        except PDF_READ_ERRORS:
            # An unreadable stream must not discard preceding or following
            # pages; keep an empty slot and let the independent OCR try it.
            text, large_raster = "", False
        character_count += len(text)
        if character_count > max_characters:
            raise text_limit_error(max_characters, max_pages)
        # A scan often has a searchable footer or header but no text layer
        # for its main body. Short native-only pages and small illustrations
        # should not trigger OCR solely because their text is short.
        meaningful = sum(char.isalnum() for char in text)
        garbled = _obviously_garbled(text)
        sparse_scan = large_raster and meaningful <= SPARSE_SCAN_ALNUM_LIMIT
        if not text.strip() or garbled or sparse_scan:
            missing.append(index + 1)
            if sparse_scan and text.strip() and not garbled:
                sparse_scan_lengths[index + 1] = meaningful
            # Do not expose unreliable partial text as a citable page. If OCR
            # fails, this original page slot remains explicitly unreadable.
            text = ""
        pages.append(text)
    character_count = sum(len(text) for text in pages)
    try:
        ocr = ocr_pdf_pages(content, missing, max_seconds=ocr_seconds)
    except OSError:
        # A local OCR I/O failure must not turn a readable partial PDF into a
        # failed request. Empty pages are reflected in report readiness.
        ocr = {}
    recognized = []
    for number in missing:
        text = ocr.get(number, "").strip()
        meaningful = sum(char.isalnum() for char in text)
        if not text or not meaningful or _obviously_garbled(text):
            continue
        # Recognizing the same sparse footer again does not establish that
        # the scan body was recovered. Require additional substantive text.
        if (
            number in sparse_scan_lengths
            and meaningful < sparse_scan_lengths[number] + SCAN_OCR_MIN_ADDITIONAL_ALNUM
        ):
            continue
        character_count += len(text)
        if character_count > max_characters:
            raise text_limit_error(max_characters, max_pages)
        pages[number - 1] = text
        recognized.append(number)
    return pages, recognized
