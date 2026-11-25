"""Recover readable PDF pages without inventing or hiding missing content."""

from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
)

from agent.services import spec_document
from agent.services.spec_document import (
    Specification,
    SpecificationValidationError,
    extract_specification,
)


def make_damaged_pdf() -> bytes:
    """A benign PDF with one undecodable content stream between readable pages."""
    writer = PdfWriter()
    for number, content in enumerate(
        (b"FIRST REQUIREMENT", None, b"THIRD REQUIREMENT"), 1
    ):
        page = writer.add_blank_page(width=400, height=200)
        if content is None:
            stream = EncodedStreamObject()
            stream[NameObject("/Filter")] = NameObject("/UnknownFilter")
            stream._data = b"damaged stream"
        else:
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {
                            NameObject("/F1"): DictionaryObject(
                                {
                                    NameObject("/Type"): NameObject("/Font"),
                                    NameObject("/Subtype"): NameObject("/Type1"),
                                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                                }
                            ),
                        }
                    ),
                }
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 100 Td (" + content + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def add_native_page(writer, text, *, garbled=False):
    page = writer.add_blank_page(width=810, height=450)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    if garbled:
        # The visible A glyphs remain ordinary text, but PDF text extraction
        # decodes them to private-use characters with no reliable meaning.
        cmap = DecodedStreamObject()
        cmap.set_data(b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfchar
<41> <E000>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end""")
        font[NameObject("/ToUnicode")] = writer._add_object(cmap)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 15 Td (" + text + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def pdf_with_partial_text(*, image_scale=1.0, garbled=False):
    writer = PdfWriter()
    add_native_page(writer, b"FIRST REQUIREMENT")
    page = add_native_page(writer, b"AAAAAA" if garbled else b"2", garbled=garbled)
    if not garbled:
        raster = PdfReader(
            Path(__file__).with_name("fixtures") / "spec-scan-multilingual.pdf"
        )
        # The scan is a real, raster-only synthetic requirements page. Its
        # native footer alone must not pass as complete page text.
        page.merge_scaled_page(raster.pages[0], image_scale, over=False)
    add_native_page(writer, b"THIRD REQUIREMENT")
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def test_raster_pdf_with_native_footer_uses_ocr_to_read_the_scan(monkeypatch):
    content = pdf_with_partial_text()
    assert PdfReader(BytesIO(content)).pages[1].extract_text().strip() == "2"
    captured = []

    def ocr(content, pages, **kwargs):
        captured.extend(pages)
        return {2: "PAYMENT LIMIT 3000\n2"}

    monkeypatch.setattr(spec_document, "ocr_pdf_pages", ocr)
    result = extract_specification("partial.pdf", "application/pdf", content)
    assert captured == [2]
    assert result.ocr_pages == [2]
    assert result.unreadable_pages == []
    result.verify_quote(2, 2, "PAYMENT LIMIT 3000")
    result.verify_quote(3, 3, "THIRD REQUIREMENT")


@pytest.mark.parametrize(
    "ocr_result", [{}, {2: "2"}, {2: "Page 2"}, {2: "\ufffd\ufffd\ufffd"}]
)
def test_partial_scan_remains_unreadable_if_ocr_cannot_recover_body(
    monkeypatch, ocr_result
):
    monkeypatch.setattr(
        spec_document, "ocr_pdf_pages", lambda *args, **kwargs: ocr_result
    )
    result = extract_specification(
        "partial.pdf", "application/pdf", pdf_with_partial_text()
    )
    assert len(result.units) == 3
    assert result.units[1] == ""
    assert result.ocr_pages == []
    assert result.unreadable_pages == [2]
    with pytest.raises(ValueError, match="unreadable"):
        result.verify_quote(1, 3, "FIRST REQUIREMENT THIRD REQUIREMENT")


def test_small_raster_illustration_does_not_trigger_ocr_for_native_text(monkeypatch):
    captured = []

    def ocr(content, pages, **kwargs):
        captured.extend(pages)
        return {}

    monkeypatch.setattr(spec_document, "ocr_pdf_pages", ocr)
    result = extract_specification(
        "native.pdf", "application/pdf", pdf_with_partial_text(image_scale=0.25)
    )
    assert captured == []
    assert result.unreadable_pages == []
    assert result.units[1].strip() == "2"


def test_garbled_nonempty_pdf_text_layer_uses_ocr(monkeypatch):
    content = pdf_with_partial_text(garbled=True)
    assert PdfReader(BytesIO(content)).pages[1].extract_text().strip() == "\ue000" * 6
    captured = []

    def ocr(content, pages, **kwargs):
        captured.extend(pages)
        return {2: "AAAAAA"}

    monkeypatch.setattr(spec_document, "ocr_pdf_pages", ocr)
    result = extract_specification("garbled.pdf", "application/pdf", content)
    assert captured == [2]
    assert result.ocr_pages == [2]
    assert result.unreadable_pages == []
    result.verify_quote(2, 2, "AAAAAA")


def test_broken_page_does_not_discard_readable_pages(monkeypatch):
    captured = []

    def ocr(content, page_numbers, **kwargs):
        captured.extend(page_numbers)
        return {}

    monkeypatch.setattr(spec_document, "ocr_pdf_pages", ocr)
    result = extract_specification("damaged.pdf", "application/pdf", make_damaged_pdf())
    assert result.units[0].strip() == "FIRST REQUIREMENT"
    assert result.units[2].strip() == "THIRD REQUIREMENT"
    assert result.unreadable_pages == [2]
    assert result.ocr_pages == []
    assert captured == [2]
    result.verify_quote(3, 3, "THIRD REQUIREMENT")


def test_ocr_recovers_damaged_text_stream_at_original_page(monkeypatch):
    monkeypatch.setattr(
        spec_document,
        "ocr_pdf_pages",
        lambda content, pages, **kwargs: {2: "SECOND REQUIREMENT"}
        if pages == [2]
        else {},
    )
    result = extract_specification("damaged.pdf", "application/pdf", make_damaged_pdf())
    assert result.ocr_pages == [2]
    assert result.unreadable_pages == []
    result.verify_quote(2, 2, "SECOND REQUIREMENT")


def test_unreadable_page_cannot_be_bridged_by_a_continuous_quote():
    spec = Specification("damaged.pdf", "pdf", ["FIRST", "", "THIRD"])
    with pytest.raises(ValueError, match="unreadable"):
        spec.verify_quote(1, 3, "FIRST THIRD")


def test_independent_pdf_probe_allows_ocr_if_text_parser_cannot_open(monkeypatch):
    def failed_reader(content):
        raise PdfReadError("unreadable cross-reference")

    monkeypatch.setattr(spec_document, "PdfReader", failed_reader)
    monkeypatch.setattr(
        spec_document, "probe_pdf_page_count", lambda content: 3, raising=False
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_pdf_pages",
        lambda content, pages, **kwargs: {
            1: "FIRST REQUIREMENT",
            3: "THIRD REQUIREMENT",
        }
        if pages == [1, 2, 3]
        else {},
    )
    result = extract_specification("damaged.pdf", "application/pdf", b"%PDF-1.7\n")
    assert result.ocr_pages == [1, 3]
    assert result.unreadable_pages == [2]
    assert len(result.units) == 3
    result.verify_quote(3, 3, "THIRD REQUIREMENT")


def test_probe_page_limit_is_checked_before_ocr(monkeypatch):
    def failed_reader(content):
        raise PdfReadError("broken page tree")

    monkeypatch.setattr(spec_document, "PdfReader", failed_reader)
    monkeypatch.setattr(
        spec_document, "probe_pdf_page_count", lambda content: 151, raising=False
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_pdf_pages",
        lambda *args, **kwargs: pytest.fail("Over-limit PDF must not run OCR"),
    )
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("large.pdf", "application/pdf", b"%PDF-1.7\n")
    assert error.value.code == "REVIEW_TEXT_LIMIT"


def test_fully_unreadable_pdf_is_rejected(monkeypatch):
    def failed_reader(content):
        raise PdfReadError("invalid PDF")

    monkeypatch.setattr(spec_document, "PdfReader", failed_reader)
    monkeypatch.setattr(
        spec_document, "probe_pdf_page_count", lambda content: None, raising=False
    )
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("damaged.pdf", "application/pdf", b"%PDF-1.7\n")
    assert error.value.code == "INVALID_PDF"


def test_password_protected_pdf_is_not_passed_to_recovery(monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("test-password")
    content = BytesIO()
    writer.write(content)
    monkeypatch.setattr(
        spec_document,
        "probe_pdf_page_count",
        lambda *args: pytest.fail("Encrypted PDFs must not be recovered"),
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_pdf_pages",
        lambda *args, **kwargs: pytest.fail("Encrypted PDFs must not run OCR"),
    )
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("encrypted.pdf", "application/pdf", content.getvalue())
    assert error.value.code == "INVALID_PDF"


def test_partial_pdf_cannot_receive_ready_verdict():
    from agent.services.spec_review import (
        Check,
        Evidence,
        INDIVIDUAL_CRITERIA,
        Requirement,
        SET_CRITERIA,
        SetCheck,
        summarize,
    )

    req = Requirement(
        id="R-001",
        source_id=None,
        text="FIRST REQUIREMENT",
        evidence=Evidence(start=1, end=1, quote="FIRST REQUIREMENT"),
        checks=[
            Check(criterion=c, status="pass", comment="Verified")
            for c in INDIVIDUAL_CRITERIA
        ],
    )
    set_checks = [
        SetCheck(criterion=c, status="pass", comment="Verified", evidence=[])
        for c in SET_CRITERIA
    ]
    _, verdict = summarize([req], [], set_checks, [2])
    assert verdict.status == "not_ready"
    assert verdict.priorities


def test_corrupt_compressed_office_data_is_a_validation_error(monkeypatch):
    from zipfile import ZIP_DEFLATED, ZipExtFile, ZipFile
    from zlib import error as ZlibError

    content = BytesIO()
    with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Document</w:t></w:r></w:p></w:body></w:document>',
        )

    def damaged_stream(self, *args, **kwargs):
        raise ZlibError("Damaged compressed data")

    monkeypatch.setattr(ZipExtFile, "read", damaged_stream)
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification(
            "corrupt.docx", "application/octet-stream", content.getvalue()
        )
    assert error.value.code == "INVALID_OFFICE"


def test_poppler_probe_preserves_count_on_real_pdf_with_damaged_stream():
    import shutil

    from agent.services.spec_ocr import probe_pdf_page_count

    if not shutil.which("pdfinfo"):
        pytest.skip("Poppler metadata reader is not installed")
    assert probe_pdf_page_count(make_damaged_pdf()) == 3


def test_pdf_with_no_recoverable_text_rejects_instead_of_calling_model(monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    content = BytesIO()
    writer.write(content)
    monkeypatch.setattr(spec_document, "ocr_pdf_pages", lambda *args, **kwargs: {})
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("scan.pdf", "application/pdf", content.getvalue())
    assert error.value.code == "REVIEW_NO_TEXT"


@pytest.mark.parametrize("has_native_footer", [False, True])
def test_real_multilingual_photo_pdf(has_native_footer):
    """A synthetic raster-only PDF with clean Russian, Kazakh and English text."""
    import shutil
    import subprocess

    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        pytest.skip("Local OCR binaries are not installed")
    available = subprocess.run(
        ["tesseract", "--list-langs"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.splitlines()
    if not {"kaz", "rus", "eng"}.issubset(available):
        pytest.skip("All three OCR language packs are required")
    fixture = Path(__file__).with_name("fixtures") / "spec-scan-multilingual.pdf"
    if has_native_footer:
        content, page_number = pdf_with_partial_text(), 2
        assert PdfReader(BytesIO(content)).pages[1].extract_text().strip() == "2"
    else:
        content, page_number = fixture.read_bytes(), 1
        assert not (PdfReader(BytesIO(content)).pages[0].extract_text() or "").strip()
    result = extract_specification(fixture.name, "application/pdf", content)
    assert result.ocr_pages == [page_number]
    assert result.unreadable_pages == []
    recognized = result.units[page_number - 1]
    assert "Платежный лимит 1000 тенге" in recognized
    assert "Төлем сомасы 2000 теңге" in recognized
    assert "PAYMENT LIMIT 3000" in recognized
