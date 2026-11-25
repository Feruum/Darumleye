"""Local, bounded OCR for PDF pages and raster image documents."""

import os
import re
import shutil
import subprocess
import struct
import tempfile
import time
from pathlib import Path

MAX_OCR_SECONDS = 180
OCR_IMAGE_EDGE = 2400
MAX_IMAGE_PIXELS = 30_000_000
MAX_IMAGE_EDGE = 16_000
MAX_IMAGE_BYTES = 25 * 1024 * 1024
# On mixed-script scans, prioritize Kazakh for its distinct Cyrillic letters
# and English before Russian to avoid misreading Latin text as Cyrillic.
OCR_LANGUAGES = ("kaz", "eng", "rus")


class ImageDocumentError(ValueError):
    pass


class ImageDocumentLimit(ImageDocumentError):
    pass


def image_kind(content: bytes) -> str:
    """Validate raster type and dimensions before a native OCR decoder sees it."""
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageDocumentLimit("Image exceeds the byte limit")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(content) < 33 or content[8:16] != b"\x00\x00\x00\rIHDR":
            raise ImageDocumentError("Invalid PNG header")
        width, height = struct.unpack_from(">II", content, 16)
        kind = "png"
    elif content.startswith(b"\xff\xd8"):
        # JPEG dimensions occur in a Start Of Frame segment before scan data.
        width = height = 0
        pos = 2
        frames = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while pos < len(content):
            if content[pos] != 0xFF:
                break
            while pos < len(content) and content[pos] == 0xFF:
                pos += 1
            if pos >= len(content):
                break
            marker = content[pos]
            pos += 1
            if marker in {0xDA, 0xD9}:
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            if pos + 2 > len(content):
                break
            length = int.from_bytes(content[pos : pos + 2], "big")
            if length < 2 or pos + length > len(content):
                break
            if marker in frames and length >= 8:
                height, width = struct.unpack_from(">HH", content, pos + 3)
                break
            pos += length
        kind = "jpg"
    else:
        raise ImageDocumentError("Expected a PNG or JPEG image")
    if width <= 0 or height <= 0:
        raise ImageDocumentError("Invalid image dimensions")
    if max(width, height) > MAX_IMAGE_EDGE or width * height > MAX_IMAGE_PIXELS:
        raise ImageDocumentLimit("Image dimensions exceed the OCR limit")
    return kind


def _ocr_configuration():
    engine = shutil.which("tesseract")
    if not engine:
        return None
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        available = subprocess.run(
            [engine, "--list-langs"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            env=environment,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    languages = "+".join(
        language for language in OCR_LANGUAGES if language in available
    )
    return (engine, languages, environment) if languages else None


def _recognize_image(
    image: Path, config, remaining: float, *, page_segmentation: int = 3
) -> str:
    engine, languages, environment = config
    return (
        subprocess.run(
            [
                engine,
                str(image),
                "stdout",
                "-l",
                languages,
                "--psm",
                str(page_segmentation),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=min(30, remaining),
            env=environment,
        )
        .stdout.decode("utf-8")
        .strip()
    )


def ocr_images(
    images: dict[int, bytes], *, max_seconds: int = MAX_OCR_SECONDS
) -> dict[int, str]:
    """OCR uploaded or embedded PNG/JPEG images under one time budget."""
    if not images or not (config := _ocr_configuration()):
        return {}
    deadline = time.monotonic() + max_seconds
    recognized = {}
    with tempfile.TemporaryDirectory(prefix="spec-image-ocr-") as directory:
        for index, content in images.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                kind = image_kind(content)
                path = Path(directory) / f"image-{index}.{kind}"
                path.write_bytes(content)
                # Standalone photos and embedded screenshots are often crops
                # of one text block rather than typeset multi-column pages.
                text = _recognize_image(path, config, remaining, page_segmentation=6)
                remaining = deadline - time.monotonic()
                if not text and remaining > 0:
                    text = _recognize_image(path, config, remaining)
                if text:
                    recognized[index] = text
            except (
                ImageDocumentError,
                OSError,
                subprocess.SubprocessError,
                UnicodeDecodeError,
            ):
                continue
    return recognized


def probe_pdf_page_count(content: bytes) -> int | None:
    """Use Poppler as a second reader when the PDF text parser cannot open it.

    Only accept an unencrypted document with an independently known page count.
    Without a count we cannot preserve the original page coordinates.
    """
    reader = shutil.which("pdfinfo")
    if not reader:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="spec-pdf-probe-") as directory:
            pdf = Path(directory) / "source.pdf"
            pdf.write_bytes(content)
            result = subprocess.run(
                [reader, str(pdf)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
    except (OSError, subprocess.SubprocessError):
        return None
    pages = re.search(r"^Pages:\s+([1-9][0-9]{0,5})\s*$", result.stdout, re.MULTILINE)
    unencrypted = re.search(r"^Encrypted:\s+no\s*$", result.stdout, re.MULTILINE)
    return int(pages.group(1)) if pages and unencrypted else None


def ocr_pdf_pages(
    content: bytes, page_numbers: list[int], *, max_seconds: int = MAX_OCR_SECONDS
) -> dict[int, str]:
    if not page_numbers:
        return {}
    renderer = shutil.which("pdftoppm")
    config = _ocr_configuration()
    if not renderer or not config:
        return {}
    _engine, _languages, environment = config

    deadline = time.monotonic() + max_seconds
    recognized = {}
    with tempfile.TemporaryDirectory(prefix="spec-ocr-") as directory:
        working = Path(directory)
        pdf = working / "source.pdf"
        pdf.write_bytes(content)
        prefix = working / "page"
        image = prefix.with_suffix(".png")
        for number in page_numbers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                subprocess.run(
                    [
                        renderer,
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        "-scale-to",
                        str(OCR_IMAGE_EDGE),
                        "-gray",
                        "-png",
                        str(pdf),
                        str(prefix),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=min(20, remaining),
                    env=environment,
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                text = _recognize_image(image, config, remaining)
                if text:
                    recognized[number] = text
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                # An unsuccessful page remains unreadable; never fabricate text.
                continue
            finally:
                image.unlink(missing_ok=True)
    return recognized
