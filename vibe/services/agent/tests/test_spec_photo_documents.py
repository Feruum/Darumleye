"""Photo documents share the same evidence-grounded review pipeline as PDFs."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from agent.services import spec_document
from agent.services.spec_document import (
    SpecificationValidationError,
    extract_specification,
)

FIXTURES = Path(__file__).with_name("fixtures")
PNG = (FIXTURES / "spec-photo.png").read_bytes()
JPG = (FIXTURES / "spec-photo.jpg").read_bytes()
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def photo_docx(blocks: str, relationships: str, images: dict[str, bytes]) -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{WORD}" xmlns:a="{DRAW}" xmlns:r="{REL}"><w:body>{blocks}</w:body></w:document>',
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>',
        )
        for part, content in images.items():
            archive.writestr(part, content)
    return data.getvalue()


def image_relation(identifier: str, target: str, mode: str = "") -> str:
    return (
        f'<Relationship Id="{identifier}" Type="{REL}/image" Target="{target}" {mode}/>'
    )


def picture(identifier: str) -> str:
    return (
        f'<w:p><w:r><w:drawing><a:blip r:embed="{identifier}"/></w:drawing></w:r></w:p>'
    )


@pytest.mark.parametrize(
    ("filename", "mime", "data"),
    [
        ("scan.png", "image/png", PNG),
        ("scan.jpg", "image/jpeg", JPG),
        ("scan.jpeg", "image/jpeg", JPG),
    ],
    ids=["png", "jpg", "jpeg"],
)
def test_standalone_photo_is_reviewed_as_one_ocr_unit(
    monkeypatch, filename, mime, data
):
    called = []

    def ocr(images, **kwargs):
        called.append(images)
        return {1: "Проверить лимит 1000"}

    monkeypatch.setattr(spec_document, "ocr_images", ocr, raising=False)
    result = extract_specification(filename, mime, data)
    assert result.source == filename.split(".")[-1]
    assert result.units == ["Проверить лимит 1000"]
    assert result.ocr_units == [1] and result.unreadable_units == []
    assert result.ocr_pages == []
    assert called == [{1: data}]
    result.verify_quote(1, 1, "лимит 1000")
    assert result.location_text(1, 1) == "изображение 1"


def test_photo_extension_must_match_real_image(monkeypatch):
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda *args, **kwargs: pytest.fail("Invalid image must not reach OCR"),
        raising=False,
    )
    for content in (b"not an image", JPG):
        with pytest.raises(SpecificationValidationError) as error:
            extract_specification("scan.png", "image/png", content)
        assert error.value.code == "INVALID_IMAGE"


def test_empty_image_ocr_is_rejected(monkeypatch):
    monkeypatch.setattr(
        spec_document, "ocr_images", lambda *args, **kwargs: {}, raising=False
    )
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("scan.jpg", "image/jpeg", JPG)
    assert error.value.code == "REVIEW_NO_TEXT"


def test_docx_photos_are_numbered_in_source_order_and_failed_images_are_visible(
    monkeypatch,
):
    content = photo_docx(
        "<w:p><w:r><w:t>Первое требование</w:t></w:r></w:p>"
        + picture("rId1")
        + "<w:p><w:r><w:t>Последнее требование</w:t></w:r></w:p>"
        + picture("rId2"),
        image_relation("rId1", "media/photo.png")
        + image_relation("rId2", "media/photo.jpg"),
        {"word/media/photo.png": PNG, "word/media/photo.jpg": JPG},
    )

    def ocr(images, **kwargs):
        assert images == {2: PNG, 4: JPG}
        return {2: "Распознанное требование"}

    monkeypatch.setattr(spec_document, "ocr_images", ocr, raising=False)
    result = extract_specification(
        "requirements.docx", "application/octet-stream", content
    )
    assert result.units == [
        "Первое требование",
        "Распознанное требование",
        "Последнее требование",
        "",
    ]
    assert result.ocr_units == [2] and result.unreadable_units == [4]
    result.verify_quote(2, 2, "Распознанное требование")
    with pytest.raises(ValueError, match="unreadable"):
        result.verify_quote(3, 4, "Последнее требование")


def test_image_only_docx_uses_ocr_without_native_text(monkeypatch):
    content = photo_docx(
        picture("photo"),
        image_relation("photo", "media/scan.png"),
        {"word/media/scan.png": PNG},
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: {1: "Уведомление должно отправляться"},
        raising=False,
    )
    result = extract_specification("scan.docx", "application/octet-stream", content)
    assert result.units == ["Уведомление должно отправляться"]
    assert result.ocr_units == [1]


def test_docx_linked_external_photo_is_never_fetched(monkeypatch):
    content = photo_docx(
        "<w:p><w:r><w:t>Текст</w:t></w:r></w:p>" + picture("photo"),
        image_relation(
            "photo", "https://example.com/scan.jpg", 'TargetMode="External"'
        ),
        {},
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: pytest.fail("External image must not be loaded"),
        raising=False,
    )
    result = extract_specification("external.docx", "application/octet-stream", content)
    assert result.units == ["Текст", ""]
    assert result.unreadable_units == [2]


def test_unreadable_docx_block_prevents_ready():
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
        text="First",
        evidence=Evidence(start=1, end=1, quote="First"),
        checks=[
            Check(criterion=c, status="pass", comment="Verified")
            for c in INDIVIDUAL_CRITERIA
        ],
    )
    checks = [
        SetCheck(criterion=c, status="pass", comment="Verified", evidence=[])
        for c in SET_CRITERIA
    ]
    _, verdict = summarize([req], [], checks, [], unreadable_units=[2])
    assert verdict.status == "not_ready"


def test_image_pixel_budget_is_checked_before_ocr(monkeypatch):
    oversized = bytearray(PNG)
    oversized[16:20] = (16001).to_bytes(4, "big")
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda *args, **kwargs: pytest.fail("Oversized image must not reach OCR"),
    )
    with pytest.raises(SpecificationValidationError) as error:
        extract_specification("large.png", "image/png", bytes(oversized))
    assert error.value.code == "REVIEW_TEXT_LIMIT"


def test_docx_photo_inside_table_is_extracted(monkeypatch):
    content = photo_docx(
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Таблица</w:t></w:r></w:p></w:tc><w:tc>"
        + picture("photo")
        + "</w:tc></w:tr></w:tbl>",
        image_relation("photo", "media/photo.jpg"),
        {"word/media/photo.jpg": JPG},
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: {2: "Текст изображения"} if 2 in images else {},
    )
    result = extract_specification("table.docx", "application/octet-stream", content)
    assert result.units[0].startswith("Таблица")
    assert result.units[1] == "Текст изображения"
    assert result.ocr_units == [2]


@pytest.mark.parametrize("source", ["png", "jpg", "docx"])
def test_real_ocr_photo_documents(source):
    import shutil
    import subprocess

    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is not installed")
    available = subprocess.run(
        ["tesseract", "--list-langs"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.splitlines()
    if not {"kaz", "rus", "eng"}.issubset(available):
        pytest.skip("All three OCR language packs are required")
    if source == "docx":
        content = photo_docx(
            picture("photo"),
            image_relation("photo", "media/scan.png"),
            {"word/media/scan.png": PNG},
        )
    else:
        content = PNG if source == "png" else JPG
    result = extract_specification(
        f"scan.{source}", "application/octet-stream", content
    )
    assert result.ocr_units == [1] and result.unreadable_units == []
    assert "Платежный лимит 1000 тенге" in result.units[0]
    assert "Төлем сомасы 2000 теңге" in result.units[0]
    assert "PAYMENT LIMIT 3000" in result.units[0]


@pytest.mark.parametrize("source", ["png", "docx"])
async def test_photo_ocr_metadata_propagates_to_the_completed_report(
    monkeypatch, source
):
    from types import SimpleNamespace
    from agent.services import spec_review as review

    if source == "docx":
        raw = photo_docx(
            picture("ok") + picture("failed"),
            image_relation("ok", "media/scan.png")
            + image_relation("failed", "media/failed.jpg"),
            {"word/media/scan.png": PNG, "word/media/failed.jpg": JPG},
        )
    else:
        raw = PNG
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: {1: "Установить лимит 1000"},
    )
    spec = extract_specification(
        f"requirements.{source}", "application/octet-stream", raw
    )

    def checks(criteria):
        return [
            {"criterion": criterion, "status": "pass", "comment": "Проверено"}
            for criterion in criteria
        ]

    async def invoke(llm, stage, instructions, data, output):
        if stage == "extraction":
            assert data["units"][0]["ocr"] is True
            return output.model_validate(
                {
                    "requirements": [
                        {
                            "source_id": None,
                            "text": spec.units[0],
                            "evidence": {"start": 1, "end": 1, "quote": spec.units[0]},
                        }
                    ]
                }
            )
        if stage == "individual":
            return output.model_validate(
                {
                    "requirements": [
                        {"id": "R-001", "checks": checks(review.INDIVIDUAL_CRITERIA)}
                    ],
                    "findings": [],
                }
            )
        assert stage == "set"
        return output.model_validate(
            {
                "summary": "Проверено",
                "set_checks": [
                    {**item, "evidence": []} for item in checks(review.SET_CRITERIA)
                ],
                "findings": [],
            }
        )

    monkeypatch.setattr(review, "invoke_stage", invoke)
    report = await review.review_specification(
        spec, SimpleNamespace(model_name="fixture"), review_mode="legacy"
    )
    assert report.ocr_units == [1]
    assert report.unreadable_units == ([2] if source == "docx" else [])
    # The controlled model does not assess readiness-checklist rows.
    assert report.verdict.status == ("not_ready" if source == "docx" else "needs_revision")
    assert "OCR" in report.report_markdown
    assert ("блок 1" if source == "docx" else "изображение 1") in report.report_markdown
    if source == "docx":
        assert "не удалось распознать изображения" in report.report_markdown


def test_docx_inline_picture_preserves_text_before_and_after(monkeypatch):
    blocks = '<w:p><w:r><w:t>До фото</w:t></w:r><w:r><w:drawing><a:blip r:embed="photo"/></w:drawing></w:r><w:r><w:t>После фото</w:t></w:r></w:p>'
    content = photo_docx(
        blocks, image_relation("photo", "media/scan.png"), {"word/media/scan.png": PNG}
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: {2: "Фото требования"} if 2 in images else {},
    )
    result = extract_specification("inline.docx", "application/octet-stream", content)
    assert result.units == ["До фото", "Фото требования", "После фото"]
    assert result.ocr_units == [2]


def test_referenced_header_and_footer_photographs_are_not_silently_omitted(monkeypatch):
    blocks = '<w:p><w:r><w:t>Текст документа</w:t></w:r></w:p><w:sectPr><w:headerReference r:id="header"/><w:footerReference r:id="footer"/></w:sectPr>'
    relationships = f'<Relationship Id="header" Type="{REL}/header" Target="header1.xml"/><Relationship Id="footer" Type="{REL}/footer" Target="footer1.xml"/>'
    header = f'<w:hdr xmlns:w="{WORD}" xmlns:a="{DRAW}" xmlns:r="{REL}">{picture("photo")}</w:hdr>'.encode()
    footer = f'<w:ftr xmlns:w="{WORD}" xmlns:a="{DRAW}" xmlns:r="{REL}">{picture("missing")}</w:ftr>'.encode()
    header_rel = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{image_relation("photo", "media/scan.png")}</Relationships>'.encode()
    content = photo_docx(
        blocks,
        relationships,
        {
            "word/header1.xml": header,
            "word/footer1.xml": footer,
            "word/_rels/header1.xml.rels": header_rel,
            "word/media/scan.png": PNG,
        },
    )
    monkeypatch.setattr(
        spec_document,
        "ocr_images",
        lambda images, **kwargs: {2: "Текст фото"} if 2 in images else {},
    )
    result = extract_specification("headers.docx", "application/octet-stream", content)
    assert result.units == ["Текст документа", "Текст фото", ""]
    assert result.ocr_units == [2] and result.unreadable_units == [3]


def test_unreadable_reference_image_prevents_negative_applicability():
    from agent.services.spec_document import Specification
    from agent.services.spec_reference_review import (
        ReferenceDocument,
        ReferenceDraft,
        ReferenceSource,
        checked_reference_review,
    )
    from agent.services.spec_reference_rag import RetrievedReferences

    reference = ReferenceDocument(
        ReferenceSource(
            id="S-001",
            category="bank_policy",
            title="Policy",
            issuer="Test",
            version="1",
            scope="Example",
            source_url=None,
            filename="policy.docx",
            source="docx",
            sha256="a" * 64,
            unreadable_pages=[],
            unreadable_units=[2],
            applicability="not_reviewed",
            summary="Pending",
        ),
        Specification(
            "policy.docx", "docx", ["Readable rule", ""], unreadable_units=[2]
        ),
    )
    source = Specification("spec.txt", "txt", ["Requirement"])
    retrieved = RetrievedReferences(
        passages={"S-001": [{"start": 1, "end": 1, "text": "Readable rule"}]},
        coverage={
            "S-001": {
                "total_chunks": 1,
                "selected_chunks": 1,
                "total_characters": 13,
                "selected_characters": 13,
                "partial": False,
            }
        },
        mode="fixture",
    )
    draft = ReferenceDraft.model_validate(
        {
            "source_reviews": [
                {
                    "source_id": "S-001",
                    "applicability": "not_applicable",
                    "summary": "Not relevant",
                }
            ],
            "observations": [],
        }
    )
    report = checked_reference_review(source, [reference], set(), draft, retrieved)
    assert report.sources[0].applicability == "unclear"
