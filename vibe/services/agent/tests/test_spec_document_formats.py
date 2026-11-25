from io import BytesIO
import re
import shutil
import subprocess
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from zipfile import ZipFile

import pytest
from pypdf import PdfWriter

from agent.services import spec_document
from agent.services.spec_document import (
    SpecificationValidationError,
    extract_specification,
)


def office_zip(parts: dict[str, str]) -> bytes:
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        for path, xml in parts.items():
            archive.writestr(path, xml)
    return result.getvalue()


def test_docx_extracts_paragraphs_and_table_cells_in_source_order():
    content = office_zip(
        {
            "word/document.xml": """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Требования к платежам</w:t></w:r></w:p>
      <w:p><w:r><w:t>REQ-1. </w:t></w:r><w:r><w:t>Сумма должна быть указана.</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>REQ-2</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Показывать комиссию</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>"""
        }
    )
    result = extract_specification(
        "requirements.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content,
    )
    assert result.source == "docx"
    assert result.units == [
        "Требования к платежам",
        "REQ-1. Сумма должна быть указана.",
        "REQ-2 | Показывать комиссию",
    ]
    result.verify_quote(3, 3, "Показывать комиссию")


def test_xlsx_uses_workbook_order_and_actual_sheet_row_coordinates():
    content = office_zip(
        {
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Платежи" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>REQ-1</t></si></sst>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="5"><c r="A5" t="s"><v>0</v></c><c r="B5" t="inlineStr"><is><t>Указать лимит</t></is></c><c r="C5"><f>SUM(1,2)</f><v>3</v></c></row></sheetData></worksheet>',
        }
    )
    result = extract_specification(
        "matrix.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content,
    )
    assert result.source == "xlsx"
    assert len(result.units) == 1
    assert "Платежи" in result.units[0] and "строка 5" in result.units[0]
    assert "A5=REQ-1" in result.units[0] and "B5=Указать лимит" in result.units[0]
    assert "=SUM(1,2)" in result.units[0]
    result.verify_quote(1, 1, "Указать лимит")


def test_csv_accepts_utf8_bom_and_quoted_semicolon_fields():
    result = extract_specification(
        "matrix.CSV",
        "text/csv",
        '\ufeffID;Требование\nREQ-1;"Показать комиссию; без округления"\n'.encode(),
    )
    assert result.source == "csv"
    assert result.units == [
        "ID | Требование",
        "REQ-1 | Показать комиссию; без округления",
    ]
    result.verify_quote(2, 2, "Показать комиссию; без округления")


def test_office_corruption_and_xml_entities_are_rejected():
    for content in [
        b"not a zip",
        office_zip(
            {"word/document.xml": '<!DOCTYPE a [<!ENTITY b "text">]><a>&b;</a>'}
        ),
    ]:
        with pytest.raises(SpecificationValidationError):
            extract_specification("bad.docx", "application/octet-stream", content)


def test_scanned_pdf_uses_ocr_and_retains_original_page_coordinates(monkeypatch):
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    writer.write(stream)
    monkeypatch.setattr(
        spec_document,
        "ocr_pdf_pages",
        lambda content, pages, **kwargs: {2: "Сумма должна отображаться."},
    )
    result = extract_specification("scan.pdf", "application/pdf", stream.getvalue())
    assert result.ocr_pages == [2]
    assert result.unreadable_pages == [1]
    result.verify_quote(2, 2, "Сумма должна отображаться.")


def test_approved_primary_limits_accept_large_text_and_reject_above_budget():
    assert spec_document.MAX_REVIEW_FILE_BYTES == 25 * 1024 * 1024
    assert spec_document.MAX_REVIEW_PAGES == 150
    assert spec_document.MAX_REVIEW_CHARACTERS == 100_000
    result = extract_specification(
        "large.txt", "text/plain", ("Строка\n" * 10_000).encode()
    )
    assert len(result.units) == 10_000
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("large.txt", "text/plain", b"x" * 100_001)
    assert error.value.code == "REVIEW_TEXT_LIMIT"


def test_archive_format_migration_preserves_existing_records():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = (
        Path(__file__).parents[1]
        / "migrations/versions/20261125_0006_review_document_formats.py"
    )
    spec = spec_from_file_location("review_format_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE agent_completed_reviews (id VARCHAR(36) PRIMARY KEY, source_format VARCHAR(3) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO agent_completed_reviews VALUES ('old', 'pdf')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        columns = inspect(connection).get_columns("agent_completed_reviews")
        assert (
            next(item for item in columns if item["name"] == "source_format")[
                "type"
            ].length
            == 8
        )
        assert (
            connection.execute(
                text("SELECT source_format FROM agent_completed_reviews WHERE id='old'")
            ).scalar_one()
            == "pdf"
        )
        connection.execute(
            text("INSERT INTO agent_completed_reviews VALUES ('new', 'docx')")
        )
        with (
            Operations.context(MigrationContext.configure(connection)),
            pytest.raises(RuntimeError),
        ):
            migration.downgrade()
    engine.dispose()


@pytest.mark.skipif(
    not shutil.which("tesseract") or not shutil.which("pdftoppm"),
    reason="Local OCR binaries are not installed",
)
def test_real_ocr_reads_an_image_only_pdf(tmp_path):
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    # Render a known text page into pixels, then construct a second PDF with
    # only that raster image. The second PDF has no embedded text to extract.
    source = PdfWriter()
    page = source.add_blank_page(width=400, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    # A monospaced base font remains readable when the PDF renderer falls
    # back to a local font; this is a clean, synthetic image-only document.
    stream.set_data(b"BT /F1 24 Tf 20 120 Td (Payment limit 1000) Tj ET")
    page[NameObject("/Contents")] = source._add_object(stream)
    path = tmp_path / "source.pdf"
    source.write(path)
    prefix = tmp_path / "raster"
    subprocess.run(
        ["pdftoppm", "-gray", "-singlefile", "-r", "160", str(path), str(prefix)],
        check=True,
        timeout=20,
    )
    pgm = prefix.with_suffix(".pgm").read_bytes()
    header = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\r?\n", pgm)
    assert header
    width, height = map(int, header.groups())
    pixels = pgm[header.end() :]
    assert len(pixels) == width * height
    scan = PdfWriter()
    page = scan.add_blank_page(width=400, height=200)
    image = DecodedStreamObject()
    image.set_data(pixels)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im0"): scan._add_object(image)}
            )
        }
    )
    paint = DecodedStreamObject()
    paint.set_data(b"q 400 0 0 200 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = scan._add_object(paint)
    content = BytesIO()
    scan.write(content)
    from pypdf import PdfReader

    assert not (
        PdfReader(BytesIO(content.getvalue())).pages[0].extract_text() or ""
    ).strip()
    result = extract_specification("scan.pdf", "application/pdf", content.getvalue())
    assert result.ocr_pages == [1]
    assert "PAYMENT LIMIT" in result.units[0].upper()
    assert "1000" in result.units[0]
