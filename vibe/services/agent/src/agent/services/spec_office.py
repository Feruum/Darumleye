"""Extract inert OOXML text and spreadsheet rows without executing embedded content."""

import posixpath
from dataclasses import dataclass, field
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from agent.services.spec_ocr import MAX_IMAGE_BYTES

MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DRAW = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
VML = "{urn:schemas-microsoft-com:vml}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


class OfficeDocumentError(ValueError):
    pass


class OfficeDocumentLimit(OfficeDocumentError):
    pass


class OfficeArchive:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        members = archive.infolist()
        if (
            len(members) > MAX_ARCHIVE_MEMBERS
            or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES
        ):
            raise OfficeDocumentLimit("Office archive exceeds the decompression limit")
        self.names = {item.filename for item in members}
        if len(self.names) != len(members):
            raise OfficeDocumentError("Office archive has duplicate parts")

    def read_part(self, name: str, limit: int) -> bytes:
        try:
            info = self.archive.getinfo(name)
            if info.file_size > limit:
                raise OfficeDocumentLimit("Office part exceeds the limit")
            with self.archive.open(info) as stream:
                raw = stream.read(limit + 1)
        except (KeyError, RuntimeError, OSError, BadZipFile, ZlibError) as error:
            raise OfficeDocumentError("Office document part cannot be read") from error
        if len(raw) > limit:
            raise OfficeDocumentLimit("Office part exceeds the limit")
        return raw

    def xml(self, name: str) -> ET.Element:
        raw = self.read_part(name, MAX_XML_BYTES)
        # DTDs and entity definitions are not required by OOXML. Checking NUL-
        # stripped bytes also handles XML encoded as UTF-16 or UTF-32.
        markup = raw.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
            raise OfficeDocumentError("Office XML contains a prohibited declaration")
        try:
            return ET.fromstring(raw)
        except ET.ParseError as error:
            raise OfficeDocumentError("Office XML is invalid") from error


@dataclass
class WordBlock:
    text: str
    images: list[str | None] = field(default_factory=list)


def paragraph_events(element: ET.Element):
    """Yield text and image references in the element's displayed order."""
    if element.tag == WORD + "t":
        yield "text", element.text or ""
    elif element.tag == WORD + "tab":
        yield "text", "\t"
    elif element.tag in {WORD + "br", WORD + "cr"}:
        yield "text", "\n"
    elif element.tag == DRAW + "blip":
        yield "image", element.get(REL + "embed")
    elif element.tag == VML + "imagedata":
        yield "image", element.get(REL + "id")
    elif element.tag == MC + "AlternateContent":
        # Alternate renderings describe the same displayed object.
        choice = element.find(MC + "Choice")
        if choice is None:
            choice = element.find(MC + "Fallback")
        if choice is not None:
            yield from paragraph_events(choice)
    else:
        for child in element:
            yield from paragraph_events(child)


def paragraph_blocks(paragraph: ET.Element):
    pieces = []
    has_image = False
    for kind, value in paragraph_events(paragraph):
        if kind == "text":
            pieces.append(value or "")
            continue
        has_image = True
        text = "".join(pieces).strip()
        if text:
            yield WordBlock(text)
        pieces.clear()
        yield WordBlock("", [value])
    text = "".join(pieces).strip()
    if text or not has_image:
        yield WordBlock(text)


def word_blocks(container: ET.Element):
    for block in container:
        if block.tag == WORD + "p":
            yield from paragraph_blocks(block)
        elif block.tag == WORD + "tbl":
            for row in block.findall(WORD + "tr"):
                cells = [list(word_blocks(cell)) for cell in row.findall(WORD + "tc")]
                if any(part.images for cell in cells for part in cell):
                    # Preserve cell order, and never join text across a picture.
                    for cell in cells:
                        yield from (part for part in cell if part.text or part.images)
                else:
                    values = [
                        "\n".join(part.text for part in cell).strip() for cell in cells
                    ]
                    if any(values):
                        yield WordBlock(" | ".join(values))
        elif block.tag in {
            WORD + "sdt",
            WORD + "sdtContent",
            WORD + "customXml",
            WORD + "ins",
        }:
            yield from word_blocks(block)


def local_word_relationships(
    archive: OfficeArchive, part: str
) -> dict[str, tuple[str, str]]:
    base, filename = posixpath.split(part)
    path = posixpath.join(base, "_rels", filename + ".rels")
    if path not in archive.names:
        return {}
    try:
        relationships = archive.xml(path)
    except OfficeDocumentLimit:
        raise
    except OfficeDocumentError:
        return {}
    targets = {}
    for relation in relationships:
        if relation.get("TargetMode", "").casefold() == "external":
            continue
        target = relation.get("Target", "")
        resolved = posixpath.normpath(
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.join(base, target)
        )
        identifier = relation.get("Id")
        if identifier and resolved.startswith("word/") and resolved in archive.names:
            targets[identifier] = (relation.get("Type", ""), resolved)
    return targets


def word_image_targets(archive: OfficeArchive, part: str) -> dict[str, str]:
    # Only local image media; never fetch linked pictures or inspect OLE.
    return {
        identifier: path
        for identifier, (kind, path) in local_word_relationships(archive, part).items()
        if kind.endswith("/image")
        and path.startswith("word/media/")
        and posixpath.splitext(path)[1].lower() in {".png", ".jpg", ".jpeg"}
    }


def docx_units(archive: OfficeArchive):
    main = "word/document.xml"
    root = archive.xml(main)
    body = root.find(WORD + "body")
    if body is None:
        raise OfficeDocumentError("Word document body is missing")
    yield from ((main, block) for block in word_blocks(body))
    for part, tag in (("footnotes", "footnote"), ("endnotes", "endnote")):
        path = f"word/{part}.xml"
        if path not in archive.names:
            continue
        for note in archive.xml(path).findall(WORD + tag):
            if note.get(WORD + "type") in {"separator", "continuationSeparator"}:
                continue
            yield from ((path, block) for block in word_blocks(note))

    # Header/footer parts may contain a scanned requirement, so include each
    # referenced local part once rather than silently skipping its pictures.
    sections = [
        element
        for element in root.iter()
        if element.tag in {WORD + "headerReference", WORD + "footerReference"}
    ]
    if sections:
        targets = local_word_relationships(archive, main)
        seen = set()
        for element in sections:
            identifier = element.get(REL + "id")
            expected = (
                "/header" if element.tag == WORD + "headerReference" else "/footer"
            )
            target = targets.get(identifier)
            if (
                target is None
                or not target[0].endswith(expected)
                or not target[1].lower().endswith(".xml")
            ):
                if (identifier, expected) not in seen:
                    seen.add((identifier, expected))
                    yield main, WordBlock("", [None])
                continue
            path = target[1]
            if path in seen:
                continue
            seen.add(path)
            try:
                section = archive.xml(path)
            except OfficeDocumentLimit:
                raise
            except OfficeDocumentError:
                yield main, WordBlock("", [None])
                continue
            if section.tag != WORD + ("hdr" if expected == "/header" else "ftr"):
                yield main, WordBlock("", [None])
                continue
            yield from ((path, block) for block in word_blocks(section))


@dataclass
class OfficeExtraction:
    units: list[str]
    # A missing value is a numbered image block that could not be read.
    images: dict[int, bytes | None] = field(default_factory=dict)


def spreadsheet_text(node: ET.Element) -> str:
    # Exclude phonetic annotations; rich-text runs are part of the cell value.
    return "".join(
        part.text or ""
        for child in node
        if child.tag in {SHEET + "t", SHEET + "r"}
        for part in child.iter(SHEET + "t")
    )


def workbook_cell(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    value = cell.findtext(SHEET + "v", default="")
    if kind == "s":
        try:
            index = int(value)
            if index < 0:
                raise IndexError
            value = shared[index]
        except (ValueError, IndexError) as error:
            raise OfficeDocumentError("Spreadsheet shared string is invalid") from error
    elif kind == "inlineStr":
        inline = cell.find(SHEET + "is")
        value = spreadsheet_text(inline) if inline is not None else ""
    elif kind == "b":
        value = {"0": "FALSE", "1": "TRUE"}.get(value, value)
    formula = cell.findtext(SHEET + "f")
    if formula:
        return f"={formula}" + (f" [значение: {value}]" if value else "")
    return value


def xlsx_units(archive: OfficeArchive):
    workbook = archive.xml("xl/workbook.xml")
    relationships = archive.xml("xl/_rels/workbook.xml.rels")
    targets = {}
    for relation in relationships:
        if relation.get("TargetMode") == "External" or not (
            relation.get("Type") or ""
        ).endswith("/worksheet"):
            continue
        target = relation.get("Target", "")
        path = posixpath.normpath(
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.join("xl", target)
        )
        if not path.startswith("xl/") or path not in archive.names:
            raise OfficeDocumentError("Spreadsheet worksheet path is invalid")
        targets[relation.get("Id")] = path
    shared = []
    if "xl/sharedStrings.xml" in archive.names:
        shared = [
            spreadsheet_text(item)
            for item in archive.xml("xl/sharedStrings.xml").findall(SHEET + "si")
        ]
    for sheet in workbook.findall(f"{SHEET}sheets/{SHEET}sheet"):
        target = targets.get(sheet.get(REL + "id"))
        if not target:
            raise OfficeDocumentError("Spreadsheet worksheet is missing")
        name = sheet.get("name", "")
        data = archive.xml(target).find(SHEET + "sheetData")
        if data is None:
            continue
        for row_index, row in enumerate(data.findall(SHEET + "row"), 1):
            row_number = row.get("r") or str(row_index)
            values = []
            for column_index, cell in enumerate(row.findall(SHEET + "c"), 1):
                value = workbook_cell(cell, shared)
                if value.strip():
                    address = cell.get("r") or f"{column_index}:{row_number}"
                    values.append(f"{address}={value}")
            if values:
                yield f"Лист «{name}», строка {row_number}: " + " | ".join(values)


def extract_office_units(
    content: bytes, source: str, max_characters: int, *, max_images: int = 150
) -> OfficeExtraction:
    try:
        with ZipFile(BytesIO(content)) as package:
            archive = OfficeArchive(package)
            result = OfficeExtraction([])
            size = 0

            def append(unit: str) -> None:
                nonlocal size
                size += len(unit) + 1
                if size > max_characters + 1:
                    raise OfficeDocumentLimit(
                        "Extracted Office text exceeds the character limit"
                    )
                result.units.append(unit)

            if source == "docx":
                targets_by_part = {}
                cached_images = {}
                for part, block in docx_units(archive):
                    if block.text or not block.images:
                        append(block.text)
                    for identifier in block.images:
                        if len(result.images) >= max_images:
                            raise OfficeDocumentLimit(
                                "Word document contains too many images"
                            )
                        if part not in targets_by_part:
                            targets_by_part[part] = word_image_targets(archive, part)
                        target = targets_by_part[part].get(identifier)
                        if target and target not in cached_images:
                            try:
                                cached_images[target] = archive.read_part(
                                    target, MAX_IMAGE_BYTES
                                )
                            except OfficeDocumentError:
                                cached_images[target] = None
                        append("")
                        result.images[len(result.units)] = cached_images.get(target)
            else:
                for unit in xlsx_units(archive):
                    append(unit)
            return result
    except (
        BadZipFile,
        OSError,
        RuntimeError,
        RecursionError,
        NotImplementedError,
    ) as error:
        raise OfficeDocumentError("Office document archive is invalid") from error
