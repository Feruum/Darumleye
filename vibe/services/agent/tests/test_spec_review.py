import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.config.settings import settings
from agent.infrastructure.database import Base, get_session
from agent.server import app
from agent.services.spec_decision_review import NaceFindingVerifier
from agent.services.spec_document import Specification

from agent.services.spec_review import (
    extract_specification,
    review_specification,
)

URL = "/api/v1/spec-reviews"
HEADERS = {"X-API-Key": "spec-review-test", "X-User-ID": "reviewer"}
TEXT = "# Платежи\nPAY-1. После тайм-аута повторить списание.\n\n# Приёмка\nPAY-2. Комиссия указана в истории.\n"
QUOTE = "PAY-1. После тайм-аута повторить списание."
INDIVIDUAL_CRITERIA = (
    "Необходимость",
    "Однозначность",
    "Полнота",
    "Атомарность",
    "Реализуемость",
    "Проверяемость",
    "Корректность",
    "Независимость от реализации",
    "Прослеживаемость",
)
SET_CRITERIA = (
    "Полнота набора",
    "Согласованность",
    "Связность",
    "Достижимость",
    "Проверяемость набора",
    "Отсутствие дублирования",
)


def quote(text=QUOTE, start=2, end=2):
    return {"start": start, "end": end, "quote": text}


def check(criterion, status="pass"):
    return {
        "criterion": criterion,
        "status": status,
        "comment": "Проверено в учебном примере.",
    }


def payloads(first_quote=QUOTE, location=2, include_second=True):
    first = quote(first_quote, location, location)
    extracted = [
        {
            "source_id": "PAY-1" if "PAY-1" in first_quote else None,
            "text": "Повтор платежа",
            "evidence": first,
        }
    ]
    if include_second:
        extracted.append(
            {
                "source_id": "PAY-2",
                "text": "Комиссия в истории",
                "evidence": quote("PAY-2. Комиссия указана в истории.", 5, 5),
            }
        )
    reviews = [
        {
            "id": f"R-{i:03d}",
            "checks": [
                check(c, "fail" if i == 1 and c == "Полнота" else "pass")
                for c in INDIVIDUAL_CRITERIA
            ],
        }
        for i in range(1, len(extracted) + 1)
    ]
    finding = {
        "kind": "missing",
        "severity": "critical",
        "issue": "Не определено поведение при повторе.",
        "recommendation": "Уточнить правила повторной операции и её ожидаемый исход.",
        "evidence": [first],
        "requirement_ids": ["R-001"],
    }
    return [
        {"requirements": extracted},
        {"requirements": reviews, "findings": [finding]},
        {
            "summary": "Учебный пример требует уточнения.",
            "set_checks": [{**check(c), "evidence": [first]} for c in SET_CRITERIA],
            "findings": [],
        },
    ]


def provider(replies, requests):
    core_index = 0
    saved = {}

    async def complete(request):
        nonlocal core_index
        body = json.loads(request.content)
        requests.append(body)
        data = json.loads(body["messages"][1]["content"])
        stage = data["stage"]
        signature = json.dumps(data, sort_keys=True)
        if signature in saved:
            reply = saved[signature]
        elif stage == "reference":
            reply = replies[-1]
        elif stage == "set":
            reply = next(item for item in reversed(replies) if "set_checks" in item)
        else:
            reply = replies[core_index]
            core_index += 1
        saved[signature] = reply
        return httpx.Response(
            200,
            json={
                "id": "review-test",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(reply, ensure_ascii=False),
                        },
                    }
                ],
            },
        )

    return ChatOpenAI(
        model="fixture",
        api_key="local-only",
        base_url="http://test-provider.invalid/v1",
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(complete)),
    )


@pytest.fixture
def review_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "api_key", "spec-review-test")
    monkeypatch.setattr(settings, "spec_review_use_nace", False)
    from agent.utils.rate_limit import limiter

    # Each fixture has an isolated database; isolate its rate-limit counters too.
    limiter.reset()
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from agent.routes import spec_review as review_route

    monkeypatch.setattr(
        review_route,
        "get_chroma_client",
        lambda: chromadb.EphemeralClient(ChromaSettings(anonymized_telemetry=False)),
        raising=False,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def session_dependency():
        async with sessions() as session:
            yield session

    asyncio.run(create_tables())
    monkeypatch.setitem(app.dependency_overrides, get_session, session_dependency)
    try:
        yield TestClient(app)
    finally:
        asyncio.run(engine.dispose())


def configure_provider(monkeypatch, replies, requests):
    monkeypatch.setattr(
        app.state,
        "agents",
        {"rag": SimpleNamespace(llm=provider(replies, requests))},
        raising=False,
    )


@pytest.mark.parametrize("extension,mime", [
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("csv", "text/csv"),
])
def test_office_and_csv_uploads_archive_exact_source_units(review_client, monkeypatch, extension, mime):
    if extension == "csv":
        content = f"Раздел\n{QUOTE}\n".encode()
    else:
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            if extension == "docx":
                archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Раздел</w:t></w:r></w:p><w:p><w:r><w:t>{QUOTE}</w:t></w:r></w:p></w:body></w:document>')
            else:
                archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="ТЗ" sheetId="1" r:id="rId1"/></sheets></workbook>')
                archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
                archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Раздел</t></is></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>{QUOTE}</t></is></c></row></sheetData></worksheet>')
        content = stream.getvalue()
    configure_provider(monkeypatch, payloads(include_second=False), [])
    response = review_client.post(URL, headers=HEADERS, files={"file": (f"requirements.{extension}", content, mime)}, data={"review_mode": "legacy"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["source"] == extension
    assert report["requirements"][0]["evidence"] == quote()
    stored = review_client.get(f"{URL}/{report['passport']['run_id']}", headers=HEADERS).json()
    assert QUOTE in stored["source_units"][1]
    if extension == "xlsx":
        assert "строка 4" in stored["source_units"][1]


def test_only_trusted_proxy_can_submit_specifications(review_client):
    response = review_client.post(URL, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "extension,mime", [("txt", "text/plain"), ("md", "text/markdown")]
)
def test_review_sends_all_lines_and_returns_complete_report(
    review_client, monkeypatch, extension, mime
):
    requests = []
    configure_provider(monkeypatch, payloads(), requests)
    response = review_client.post(
        URL, headers=HEADERS, files={"file": (f"spec.{extension}", TEXT, mime)}, data={"review_mode": "legacy"}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["source"] == extension
    assert report["passport"]["model"] == "fixture"
    assert report["statistics"] == {
        "requirements": 2,
        "violations": 1,
        "critical": 1,
        "passed": 1,
        "needs_revision": 1,
    }
    assert report["verdict"]["status"] == "not_ready"
    assert [item["source_id"] for item in report["requirements"]] == ["PAY-1", "PAY-2"]
    assert len(report["requirements"][0]["checks"]) == len(INDIVIDUAL_CRITERIA)
    assert len(report["set_checks"]) == len(SET_CRITERIA)
    assert "PAY-1" in report["report_markdown"]
    assert report["findings"][0]["evidence"][0]["quote"] == QUOTE
    stored = review_client.get(URL, headers=HEADERS).json()["reviews"]
    assert stored[0]["id"] == report["passport"]["run_id"]
    assert stored[0]["filename"] == f"spec.{extension}"
    assert review_client.get(URL, headers={**HEADERS, "X-User-ID": "other"}).json() == {"reviews": []}
    chat = {"module_id": "review_chat", "review_id": stored[0]["id"],
            "messages": [{"type": "human", "content": "What should I fix?"}]}
    assert review_client.post('/api/v1/chat/stream', headers={**HEADERS, "X-User-ID": "other"}, json=chat).status_code == 404
    assert review_client.post('/api/v1/chat/stream', headers=HEADERS, json={**chat, "thread_id": "other-review-thread"}).status_code == 422
    assert len(requests) == 3
    stages = [json.loads(req["messages"][1]["content"])["stage"] for req in requests]
    assert stages[0] == "extraction"
    assert set(stages[1:]) == {"individual", "set"}
    assert [
        unit["text"]
        for unit in json.loads(requests[0]["messages"][1]["content"])["units"]
    ] == TEXT.splitlines()
    assert all(
        req["response_format"] == {"type": "json_object"} and "tools" not in req
        for req in requests
    )


@pytest.mark.parametrize("stage", ["extraction", "individual", "set"])
async def test_invalid_evidence_rejected_at_every_stage(stage):
    replies = payloads()
    if stage == "extraction":
        replies[0]["requirements"][0]["evidence"] = quote("Выдуманная цитата")
    elif stage == "individual":
        replies[1]["findings"][0]["evidence"] = [quote("Выдуманная цитата")]
    else:
        replies[2]["set_checks"][0]["evidence"] = [quote("Выдуманная цитата")]
    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    if stage == "individual":
        report = await review_specification(specification, provider(replies, []), review_mode="legacy")
        assert not report.findings
        assert any(item.status == "uncertain" for item in report.requirements[0].checks)
        assert report.verdict.status != "ready"
        assert "Выдуманная цитата" not in report.report_markdown
    else:
        with pytest.raises(ValueError):
            await review_specification(specification, provider(replies, []), review_mode="legacy")


async def test_missing_individual_review_cannot_be_marked_ready():
    replies = payloads()
    replies[1]["requirements"].pop()
    with pytest.raises(ValueError):
        await review_specification(
            extract_specification("spec.txt", "text/plain", TEXT.encode()),
            provider(replies, []), review_mode="legacy",
        )


@pytest.mark.parametrize(
    "name,content,mime,code",
    [
        ("spec.txt", " ", "text/plain", "REVIEW_NO_TEXT"),
        ("spec.txt", b"\xff\xfe", "text/plain", "INVALID_TEXT"),
        ("spec.txt", "a" * 100_001, "text/plain", "REVIEW_TEXT_LIMIT"),
        ("broken.pdf", b"%PDF-invalid", "application/pdf", "INVALID_PDF"),
        ("spec.md", "hello", "application/pdf", "CONTENT_TYPE_MISMATCH"),
    ],
)
def test_invalid_documents_are_rejected_before_the_model(
    review_client, monkeypatch, name, content, mime, code
):
    requests = []
    configure_provider(monkeypatch, payloads(), requests)
    result = review_client.post(
        URL, headers=HEADERS, files={"file": (name, content, mime)}, data={"review_mode": "legacy"}
    )
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == code
    assert requests == []


def test_pdf_preserves_original_page_numbers_and_reports_unreadable_pages(
    review_client, monkeypatch
):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    pdf = PdfWriter()
    pdf.add_blank_page(width=612, height=792)
    page = pdf.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 720 Td (Retry the debit after a timeout.) Tj ET")
    page[NameObject("/Contents")] = pdf._add_object(stream)
    contents = BytesIO()
    pdf.write(contents)
    configure_provider(
        monkeypatch, payloads("Retry the debit after a timeout.", 2, False), []
    )
    response = review_client.post(
        URL,
        headers=HEADERS,
        files={"file": ("spec.pdf", contents.getvalue(), "application/pdf")}, data={"review_mode": "legacy"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "pdf"
    assert response.json()["unreadable_pages"] == [1]
    assert response.json()["requirements"][0]["evidence"]["start"] == 2


async def test_two_separate_quotes_are_required_for_a_contradiction():
    replies = payloads()
    finding = replies[1]["findings"][0]
    finding["kind"] = "conflict"
    finding["evidence"] *= 2
    report = await review_specification(
        extract_specification("spec.txt", "text/plain", TEXT.encode()),
        provider(replies, []), review_mode="legacy",
    )
    assert not any(item.kind == "conflict" for item in report.findings)
    assert report.verdict.status != "ready"
    assert any(item.status == "uncertain" for item in report.requirements[0].checks)


def test_review_requires_a_configured_chat_model(review_client, monkeypatch):
    monkeypatch.setattr(app.state, "agents", {}, raising=False)
    response = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LLM_NOT_CONFIGURED"


async def test_all_extraction_chunks_and_review_batches_are_processed():
    from agent.services.spec_document import EXTRACTION_CHUNK_CHARACTERS

    source = [f"ITEM-{i}. Правило {i}. " + "а" * 2500 for i in range(1, 8)]
    specification = extract_specification(
        "large.md", "text/markdown", "\n".join(source).encode()
    )
    requests = []
    replies = []
    for index in range(1, 8):
        replies.append(
            {
                "requirements": [
                    {
                        "source_id": f"ITEM-{index}",
                        "text": f"Правило {index}",
                        "evidence": quote(
                            f"ITEM-{index}. Правило {index}.", index, index
                        ),
                    }
                ]
            }
        )
    for start, end in [(1, 6), (7, 7)]:
        replies.append(
            {
                "requirements": [
                    {
                        "id": f"R-{i:03d}",
                        "checks": [check(c) for c in INDIVIDUAL_CRITERIA],
                    }
                    for i in range(start, end + 1)
                ],
                "findings": [],
            }
        )
    replies.append(
        {
            "summary": "Все учебные требования проверены.",
            "set_checks": [{**check(c), "evidence": []} for c in SET_CRITERIA],
            "findings": [],
        }
    )
    report = await review_specification(specification, provider(replies, requests), review_mode="legacy")
    assert EXTRACTION_CHUNK_CHARACTERS == 5_000
    assert report.statistics.requirements == report.statistics.passed == 7
    # All core checks pass, but an unassessed readiness checklist must not
    # silently yield a positive whole-document verdict.
    assert report.verdict.status == "needs_revision"
    stages = [json.loads(req["messages"][1]["content"]) for req in requests]
    assert [
        (stage["primary_start"], stage["primary_end"])
        for stage in stages
        if stage["stage"] == "extraction"
    ] == [(i, i) for i in range(1, 8)]
    assert [
        len(stage["requirements"]) for stage in stages if stage["stage"] == "individual"
    ] == [6, 1]
    assert [unit["text"] for unit in stages[-1]["units"]] == source


@pytest.mark.parametrize(
    "count,combined_result,expected_batches",
    [
        (7, "valid", [7]),
        (7, "invalid", [7, 6, 1]),
        (7, "rate_limit", [7]),
        (8, "valid", [6, 2]),
    ],
)
async def test_short_review_avoids_singleton_batch_without_skipping_checks(
    count, combined_result, expected_batches
):
    from langchain_core.messages import AIMessage
    from openai import RateLimitError
    from agent.services.spec_review import ReviewProviderRateLimit

    source = [f"ITEM-{i}. Правило {i}." for i in range(1, count + 1)]
    specification = extract_specification(
        "short.txt", "text/plain", "\n".join(source).encode()
    )
    batches = []
    finding = {
        "kind": "ambiguous",
        "severity": "medium",
        "issue": "Не указан ожидаемый результат.",
        "recommendation": "Уточнить ожидаемый результат.",
        "evidence": [quote(source[0], 1, 1)],
        "requirement_ids": ["R-001"],
    }

    async def invocation(messages, **kwargs):
        data = json.loads(messages[1].content)
        assert [unit["text"] for unit in data["units"]] == source
        if data["stage"] == "extraction":
            reply = {
                "requirements": [
                    {
                        "source_id": f"ITEM-{i}",
                        "text": f"Правило {i}",
                        "evidence": quote(line, i, i),
                    }
                    for i, line in enumerate(source, 1)
                ]
            }
        elif data["stage"] == "set":
            assert data["criteria"] == list(SET_CRITERIA)
            reply = {
                "summary": "Проверены все требования.",
                "set_checks": [{**check(c), "evidence": []} for c in SET_CRITERIA],
                "findings": [],
            }
        else:
            assert data["criteria"] == list(INDIVIDUAL_CRITERIA)
            ids = [item["id"] for item in data["requirements"]]
            batches.append(len(ids))
            if combined_result == "rate_limit" and len(ids) == 7:
                raise RateLimitError(
                    "Provider rate limit",
                    response=httpx.Response(
                        429,
                        request=httpx.Request("POST", "http://test-provider.invalid"),
                    ),
                    body=None,
                )
            reply = {
                "requirements": [
                    {
                        "id": id_,
                        "checks": [
                            check(
                                c,
                                "fail"
                                if id_ == "R-001" and c == "Однозначность"
                                else "pass",
                            )
                            for c in INDIVIDUAL_CRITERIA
                        ],
                    }
                    for id_ in ids
                ],
                "findings": [finding] if "R-001" in ids else [],
            }
            if combined_result == "invalid" and len(ids) == 7:
                reply["requirements"][0]["checks"].pop()
        return AIMessage(content=json.dumps(reply, ensure_ascii=False))

    llm = SimpleNamespace(model_name="fixture", ainvoke=invocation)
    if combined_result == "rate_limit":
        with pytest.raises(ReviewProviderRateLimit):
            await review_specification(specification, llm, concurrency=2, review_mode="legacy")
        assert batches == expected_batches
        return

    report = await review_specification(
        specification,
        llm,
        concurrency=2, review_mode="legacy",
    )
    assert batches == expected_batches
    assert [item.id for item in report.requirements] == [
        f"R-{i:03d}" for i in range(1, count + 1)
    ]
    assert all(
        [check.criterion for check in item.checks] == list(INDIVIDUAL_CRITERIA)
        for item in report.requirements
    )
    assert [check.criterion for check in report.set_checks] == list(SET_CRITERIA)
    assert report.requirements[0].checks[1].status == "fail"
    assert report.findings[0].evidence[0].quote == source[0]
    assert report.statistics.violations == 1


@pytest.mark.parametrize("extension", ["md", "json"])
def test_cli_writes_complete_report_with_controlled_model(
    tmp_path, monkeypatch, extension
):
    from agent import spec_review_cli

    requests = []
    monkeypatch.setattr(
        spec_review_cli, "ChatOpenAI", lambda **kwargs: provider(payloads(), requests)
    )
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-only")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://test-provider.invalid/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "fixture")
    source = tmp_path / "source.md"
    output = tmp_path / f"report.{extension}"
    source.write_text(TEXT, encoding="utf-8")
    assert (
        spec_review_cli.main(
            ["--review-mode", "legacy", 
                "--input",
                str(source),
                "--output",
                str(output),
                "--env-file",
                str(tmp_path / ".env"),
            ]
        )
        == 0
    )
    document = output.read_text(encoding="utf-8")
    if extension == "json":
        result = json.loads(document)
        assert result["statistics"]["requirements"] == 2
        document = result["report_markdown"]
    assert "## Паспорт прогона" in document
    assert "## Проверка каждого требования" in document
    assert "## Проверка набора требований" in document
    assert "## Итог" in document
    assert len(requests) == 3


def test_cli_rejects_overwriting_input(tmp_path):
    from agent import spec_review_cli

    source = tmp_path / "source.md"
    source.write_text(TEXT, encoding="utf-8")
    assert spec_review_cli.main(["--review-mode", "legacy", "--input", str(source), "--output", str(source)]) != 0
    assert source.read_text(encoding="utf-8") == TEXT


async def test_no_requirements_cannot_produce_ready_report():
    replies = [
        {"requirements": []},
        {
            "summary": "Нет проверяемых требований.",
            "set_checks": [{**check(c), "evidence": []} for c in SET_CRITERIA],
            "findings": [],
        },
    ]
    report = await review_specification(
        extract_specification("intro.txt", "text/plain", b"Intro only"),
        provider(replies, []), review_mode="legacy",
    )
    assert report.verdict.status == "not_ready"
    assert report.statistics.violations >= 1
    assert report.set_checks[0].status == "fail"


async def test_linked_missing_requirements_retain_verified_source_context():
    replies = payloads()
    replies[1]["findings"][0]["evidence"] = []
    report = await review_specification(
        extract_specification("spec.md", "text/markdown", TEXT.encode()),
        provider(replies, []), review_mode="legacy",
    )
    assert report.findings[0].evidence == [report.requirements[0].evidence]
    assert "строка 2" in report.report_markdown


async def test_report_contains_prioritized_questions_and_evidence_backed_readiness_checklist():
    replies = payloads()
    replies[1]["findings"][0]["criteria"] = ["Полнота"]
    replies[1]["findings"][0]["clarification_question"] = "Какой результат должен иметь повторный платёж после тайм-аута?"
    replies[2]["readiness_checklist"] = [{
        "id": "testability", "availability": "present", "status": "fail",
        "comment": "Не задан ожидаемый результат повторной операции.",
        "evidence": [quote()],
        "question": "Как проверить результат повторной операции?",
    }]
    report = await review_specification(
        extract_specification("spec.md", "text/markdown", TEXT.encode()),
        provider(replies, []), review_mode="legacy",
    )
    assert report.clarification_questions[0].finding_id == "D-001"
    assert report.clarification_questions[0].priority == "critical"
    assert report.clarification_questions[0].question == replies[1]["findings"][0]["clarification_question"]
    assert report.clarification_questions[0].evidence == report.findings[0].evidence
    assert len(report.readiness_checklist) == 15
    by_id = {item.id: item for item in report.readiness_checklist}
    assert by_id["testability"].status == "fail"
    assert by_id["rollback"].status == "uncertain"
    assert by_id["rollback"].availability == "uncertain"
    assert "## Уточняющие вопросы" in report.report_markdown
    assert "## Чек-лист готовности ТЗ" in report.report_markdown
    assert any(item.question == "Как проверить результат повторной операции?" for item in report.clarification_questions)


async def test_unlinked_failed_criterion_is_uncertain_after_one_correction():
    replies = payloads()
    replies[1]["requirements"][0]["checks"][1]["status"] = "fail"
    replies[1]["findings"][0]["criteria"] = ["Полнота"]
    requests = []
    report = await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(replies, requests), review_mode="legacy")
    checks = {item.criterion: item.status for item in report.requirements[0].checks}
    assert checks["Полнота"] == "fail"
    assert checks["Однозначность"] == "uncertain"
    assert sum(json.loads(request["messages"][1]["content"])["stage"] == "individual" for request in requests) == 2


async def test_individual_finding_retains_verified_requirement_context_after_correction():
    replies = payloads()
    replies[1]["findings"][0]["kind"] = "ambiguous"
    replies[1]["findings"][0]["evidence"] = [quote("PAY-2. Комиссия указана в истории.", 5, 5)]
    report = await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(replies, []), review_mode="legacy")
    assert report.requirements[0].evidence in report.findings[0].evidence
    assert report.requirements[1].evidence in report.findings[0].evidence


async def test_readiness_positive_status_requires_original_evidence():
    replies = payloads()
    replies[2]["readiness_checklist"] = [{
        "id": "approvals", "availability": "present", "status": "pass",
        "comment": "Документ согласован.", "evidence": [], "question": None,
    }]
    with pytest.raises(ValueError, match="Checklist confirmation requires source evidence"):
        await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(replies, []), review_mode="legacy")


async def test_extraction_gap_and_blocking_findings_are_reported():
    replies = payloads(include_second=False)
    replies[1]["findings"][0]["severity"] = "high"
    report = await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(replies, []), review_mode="legacy")
    assert [item.quote for item in report.extraction_gaps] == ["PAY-2. Комиссия указана в истории."]
    assert report.verdict.status == "not_ready"
    assert "## Непокрытые кандидаты на требования" in report.report_markdown
    critical_section = report.report_markdown.split("## Критические дефекты", 1)[1].split("##", 1)[0]
    assert "D-001" in critical_section
    assert "Высокая" in critical_section


async def test_optional_job_id_is_preserved_in_generated_report():
    run_id = "0196f6f8-6600-7000-8000-000000000001"
    report = await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(payloads(), []), run_id=run_id, review_mode="legacy")
    assert report.passport.run_id == run_id
    with pytest.raises(ValueError):
        await review_specification(extract_specification("spec.md", "text/markdown", TEXT.encode()), provider(payloads(), []), run_id="invalid", review_mode="legacy")


def test_explicit_blank_version_and_approval_block_cannot_be_confirmed():
    from agent.services.spec_review import checked_readiness

    source = Specification("spec.md", "md", [
        "Документ (версия/дата) в источнике не заполнен.",
        "### Блок согласующих", "| Подразделение | ФИО | Подпись |",
        "| :--- | :--- | :--- |", "| | | |",
    ])
    rows = {item.id: item for item in checked_readiness(source, [])}
    for id_ in ("versioning", "approvals"):
        assert rows[id_].availability == "missing"
        assert rows[id_].status == "fail"
        assert rows[id_].question
        for evidence in rows[id_].evidence:
            source.verify_quote(evidence.start, evidence.end, evidence.quote)


def test_uncovered_technical_constraints_are_detected_not_invented_as_requirements():
    from agent.services.spec_review import uncovered_requirement_candidates

    source = Specification("api.md", "md", [
        "#### Path-параметры", "Не используются.",
        "#### Request body", "* **Content-Type:** `application/json` .",
        "### Примечания", "Не используются.",
    ])
    assert [item.start for item in uncovered_requirement_candidates(source, [])] == [2, 4]


def test_repeated_explicit_identifier_does_not_hide_second_obligation_on_same_page():
    from agent.services.spec_review import Evidence, Requirement, uncovered_requirement_candidates
    source = Specification("spec.pdf", "pdf", ["PAY-1. Сохранить сумму платежа.\nPAY-1. Отправить чек клиенту."])
    first = Requirement(id="R-001", source_id="PAY-1", text="Сохранить сумму платежа", evidence=Evidence(start=1, end=1, quote="PAY-1. Сохранить сумму платежа."), checks=[])
    assert [item.quote for item in uncovered_requirement_candidates(source, [first])] == ["PAY-1. Отправить чек клиенту."]


def test_questions_with_identical_wording_keep_distinct_source_links():
    from agent.services.spec_review import Evidence, Finding, clarification_questions
    findings = [Finding(kind="missing", severity="medium", issue="Не задан срок.", recommendation="Уточнить срок.", clarification_question="Какой срок?", evidence=[Evidence(start=index, end=index, quote=f"Операция {index}")], requirement_ids=[f"R-{index:03d}"]) for index in (1, 2)]
    assert [item.finding_id for item in clarification_questions(findings, [])] == ["D-001", "D-002"]


def test_readiness_not_applicable_requires_consistent_source_evidence():
    from agent.services.spec_review import ReadinessCheck, checked_readiness
    source = Specification("spec.md", "md", ["Требование касается платежей."])
    row = ReadinessCheck(id="testability", availability="missing", status="not_applicable", comment="Нет данных.")
    with pytest.raises(ValueError, match="Checklist confirmation requires source evidence"):
        checked_readiness(source, [row])


def test_cli_progress_reports_only_safe_provider_failure_metadata():
    from io import StringIO
    import structlog
    from agent.spec_cli_progress import ReviewProgress

    stream = StringIO()
    progress = ReviewProgress(stream=stream)
    with pytest.raises(structlog.DropEvent):
        progress._log_event(None, "warning", {"event": "spec_stage_failed", "stage": "individual", "error_type": "APIStatusError", "http_status": 502, "raw": "private-model-response"})
    assert "502" in stream.getvalue()
    assert "APIStatusError" in stream.getvalue()
    assert "private-model-response" not in stream.getvalue()


async def test_set_failure_cannot_be_hidden_by_an_unrelated_individual_finding():
    replies = payloads()
    replies[2]["set_checks"][1]["status"] = "fail"
    with pytest.raises(ValueError, match="Failed set check has no associated finding"):
        await review_specification(
            extract_specification("spec.md", "text/markdown", TEXT.encode()),
            provider(replies, []), review_mode="legacy",
        )


def nace_provider(answers, requests, *, key="fixture-only", timeout=8, status=200):
    async def complete(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "model": "drex-max-finance",
                "answers": answers,
            },
        )

    return NaceFindingVerifier(
        api_key=key,
        model="drex-latest",
        timeout=timeout,
        transport=httpx.MockTransport(complete),
    )


def noul(value):
    return {"type": "noul", "noul": value}


async def test_independent_verifier_checks_every_finding_in_final_order():
    replies = payloads()
    replies[2]["findings"] = [
        {
            "kind": "missing",
            "severity": "high",
            "issue": "Отсутствует описание роли оператора.",
            "recommendation": "Уточнить роль.",
            "requirement_ids": [],
            # Section-wide omissions still need a literal contextual anchor.
            "evidence": [quote()],
        },
        {
            "kind": "ambiguous",
            "severity": "medium",
            "issue": "Неоднозначная история комиссии.",
            "recommendation": "Уточнить описание истории.",
            "requirement_ids": ["R-002"],
            "evidence": [quote("PAY-2. Комиссия указана в истории.", 5, 5)],
        },
    ]
    requests = []
    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    report = await review_specification(
        specification,
        provider(replies, []),
        verifier=nace_provider(
            {"D-003": noul(0.39), "D-001": noul(0.8), "D-002": noul(0.4)},
            requests,
        ), review_mode="legacy",
    )
    baseline = await review_specification(specification, provider(replies, []), review_mode="legacy")
    assert report.findings == baseline.findings
    assert report.statistics == baseline.statistics
    assert report.verdict == baseline.verdict
    assert baseline.decision_review is None
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://console.nace.ai/v1/systemone"
    assert request.headers["Authorization"] == "Bearer fixture-only"
    payload = json.loads(request.content)
    assert payload["model"] == "drex-latest"
    assert payload["state"]["units"] == specification.numbered_units()
    assert payload["state"]["unreadable_pages"] == []
    assert list(payload["questions"]) == ["D-001", "D-002", "D-003"]
    assert payload["state"]["findings"][1]["evidence"] == [quote()]
    assert payload["state"]["findings"][0]["evidence"][0]["quote"] == QUOTE
    audit = report.decision_review
    assert audit.status == "completed"
    assert audit.provider == "nace"
    assert audit.model == "drex-max-finance"
    assert [(item.id, item.probability, item.status) for item in audit.findings] == [
        ("D-001", 0.8, "supported"),
        ("D-002", 0.4, "uncertain"),
        ("D-003", 0.39, "unsupported"),
    ]
    assert "## Независимая проверка Drex" in report.report_markdown
    assert "D-003" in report.report_markdown
    assert "39" in report.report_markdown


@pytest.mark.parametrize(
    "answers,status",
    [
        ({}, 200),
        ({"D-001": noul(True)}, 200),
        ({"D-001": noul(1.1)}, 200),
        ({"D-001": noul(0.8), "D-002": noul(0.5)}, 200),
        ({}, 503),
    ],
)
async def test_independent_verifier_keeps_baseline_for_invalid_provider(
    answers, status
):
    report = await review_specification(
        extract_specification("spec.md", "text/markdown", TEXT.encode()),
        provider(payloads(), []),
        verifier=nace_provider(answers, [], status=status), review_mode="legacy",
    )
    assert report.statistics.violations == 1
    assert report.verdict.status == "not_ready"
    assert report.decision_review.status == "unavailable"
    assert report.decision_review.findings == []


async def test_independent_verifier_passes_unreadable_pages_and_skips_if_unconfigured():
    specification = Specification("spec.pdf", "pdf", ["", "Some rule"])
    calls = []
    finding = {
        "kind": "missing",
        "severity": "high",
        "issue": "Missing rule",
        "recommendation": "Clarify",
        "requirement_ids": [],
        "evidence": [],
    }
    from agent.services.spec_review import Finding

    findings = [Finding.model_validate(finding)]
    audit = await nace_provider({"D-001": noul(0.6)}, calls).verify(
        specification, findings
    )
    assert audit.status == "completed"
    payload = json.loads(calls[0].content)
    assert payload["state"]["unreadable_pages"] == [1]
    assert payload["state"]["units"] == specification.numbered_units()
    empty_calls = []
    audit = await nace_provider({}, empty_calls, key="").verify(specification, findings)
    assert audit.status == "unavailable"
    audit = await nace_provider({}, empty_calls).verify(specification, [])
    assert audit.status == "not_applicable"
    assert empty_calls == []


async def test_independent_verifier_respects_remaining_budget_and_timeout():
    requests = []

    async def delayed(request):
        requests.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    verifier = NaceFindingVerifier(
        api_key="fixture-only",
        model="drex-latest",
        timeout=0.01,
        transport=httpx.MockTransport(delayed),
    )
    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    report = await review_specification(specification, provider(payloads(), []), review_mode="legacy")
    result = await verifier.verify(
        specification, report.findings, deadline=asyncio.get_running_loop().time()
    )
    assert result.status == "unavailable"
    assert not requests
    result = await verifier.verify(specification, report.findings)
    assert result.status == "unavailable"
    assert len(requests) == 1
    reviewed = await review_specification(
        specification, provider(payloads(), []), verifier=verifier, review_mode="legacy"
    )
    assert reviewed.decision_review.status == "unavailable"
    assert reviewed.findings == report.findings
    assert reviewed.statistics == report.statistics
    assert reviewed.verdict == report.verdict


async def test_independent_verifier_skips_over_limit_without_partial_checks(
    monkeypatch,
):
    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    baseline = await review_specification(specification, provider(payloads(), []), review_mode="legacy")
    requests = []
    verifier = nace_provider({}, requests)
    result = await verifier.verify(specification, baseline.findings * 129)
    assert result.status == "unavailable"
    assert result.findings == []
    monkeypatch.setattr("agent.services.spec_decision_review.MAX_REQUEST_BYTES", 1)
    result = await verifier.verify(specification, baseline.findings)
    assert result.status == "unavailable"
    assert result.findings == []
    assert requests == []


@pytest.mark.parametrize("enabled", [False, True])
def test_http_review_uses_independent_verification_only_when_enabled(
    review_client, monkeypatch, enabled
):
    configure_provider(monkeypatch, payloads(), [])
    monkeypatch.setattr(settings, "spec_review_use_nace", enabled)
    monkeypatch.setattr(settings, "nace_api_key", "fixture-key")
    monkeypatch.setattr(settings, "nace_model", "drex-max-finance")
    monkeypatch.setattr(settings, "nace_timeout_seconds", 7)
    calls = []
    constructors = []

    def make_verifier(**kwargs):
        constructors.append(kwargs)
        return nace_provider({"D-001": noul(0.95)}, calls)

    monkeypatch.setattr("agent.services.spec_review.NaceFindingVerifier", make_verifier)
    response = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.md", TEXT, "text/markdown")}, data={"review_mode": "legacy"}
    )
    assert response.status_code == 200, response.text
    audit = response.json()["decision_review"]
    if enabled:
        assert constructors == [
            {"api_key": "fixture-key", "model": "drex-max-finance", "timeout": 7}
        ]
        assert len(calls) == 1
        assert audit["status"] == "completed"
        assert audit["findings"] == [
            {"id": "D-001", "probability": 0.95, "status": "supported"}
        ]
    else:
        assert constructors == []
        assert calls == []
        assert audit is None


def test_cli_uses_independent_verification_from_env_file(tmp_path, monkeypatch):
    from agent import spec_review_cli

    monkeypatch.setattr(
        spec_review_cli, "ChatOpenAI", lambda **kwargs: provider(payloads(), [])
    )
    for env_name in [
        "AGENT_SPEC_REVIEW_USE_NACE",
        "NACE_API_KEY",
        "AGENT_NACE_API_KEY",
        "AGENT_NACE_MODEL",
        "AGENT_NACE_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(env_name, raising=False)
    environment = tmp_path / ".env"
    environment.write_text(
        "AGENT_LLM_API_KEY=fixture-only\nAGENT_LLM_BASE_URL=http://test-provider.invalid/v1\n"
        "AGENT_LLM_MODEL=fixture\nAGENT_SPEC_REVIEW_USE_NACE=true\nNACE_API_KEY=nace-fixture\n"
        "AGENT_NACE_MODEL=drex-max-finance\nAGENT_NACE_TIMEOUT_SECONDS=6\n"
    )
    source = tmp_path / "source.md"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "report.json"
    constructors = []
    calls = []

    def make_verifier(**kwargs):
        constructors.append(kwargs)
        return nace_provider({"D-001": noul(0.25)}, calls)

    monkeypatch.setattr("agent.services.spec_review.NaceFindingVerifier", make_verifier)
    assert (
        spec_review_cli.main(
            ["--review-mode", "legacy", 
                "--input",
                str(source),
                "--output",
                str(output),
                "--env-file",
                str(environment),
            ]
        )
        == 0
    )
    assert constructors == [
        {"api_key": "nace-fixture", "model": "drex-max-finance", "timeout": 6.0}
    ]
    assert len(calls) == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["decision_review"]["status"] == "completed"
    assert result["decision_review"]["findings"] == [
        {"id": "D-001", "probability": 0.25, "status": "unsupported"}
    ]
    assert result["statistics"]["violations"] == 1
    assert result["verdict"]["status"] == "not_ready"
    assert "## Независимая проверка Drex" in result["report_markdown"]


REFERENCE_CATEGORIES = (
    "quality_standard",
    "bank_policy",
    "api_testing",
    "payments_data",
    "security_regulation",
)
REFERENCE_QUOTE = "Правило 1. Указать исход повторной операции."


def reference_manifest(category="bank_policy", **overrides):
    return {
        "category": category,
        "title": "Учебное руководство",
        "issuer": "Учебный владелец",
        "version": "1.0",
        "scope": "Учебные платежи",
        "source_url": None,
        **overrides,
    }


def reference_payload(count=1, applicability="relevant"):
    return {
        "source_reviews": [
            {
                "source_id": f"S-{i:03d}",
                "applicability": applicability,
                "summary": "Требуется подтверждение применимости владельцем.",
            }
            for i in range(1, count + 1)
        ],
        "observations": [
            {
                "source_id": "S-001",
                "status": "potential_discrepancy",
                "issue": "Не определён результат повтора.",
                "recommendation": "Уточнить ожидаемый исход.",
                "requirement_ids": ["R-001"],
                "specification_evidence": [quote()],
                "reference_evidence": [quote(REFERENCE_QUOTE, 1, 1)],
            }
        ],
    }


def test_reference_review_separate_and_all_five_categories(review_client, monkeypatch):
    import hashlib

    requests = []
    configure_provider(monkeypatch, payloads() + [reference_payload(5)], requests)
    manifest = [reference_manifest(category) for category in REFERENCE_CATEGORIES]
    files = [("file", ("spec.md", TEXT, "text/markdown"))]
    for number in range(5):
        files.append(
            ("reference_files", (f"ref-{number}.txt", REFERENCE_QUOTE, "text/plain"))
        )
    response = review_client.post(
        URL,
        headers=HEADERS,
        files=files,
        data={"review_mode": "legacy", "reference_manifest": json.dumps(manifest)},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    reference = report["reference_review"]
    assert report["statistics"]["violations"] == 1
    assert report["verdict"]["status"] == "not_ready"
    assert reference["status"] == "completed"
    assert [item["category"] for item in reference["sources"]] == list(
        REFERENCE_CATEGORIES
    )
    assert [item["id"] for item in reference["sources"]] == [
        f"S-{i:03d}" for i in range(1, 6)
    ]
    assert (
        reference["sources"][0]["sha256"]
        == hashlib.sha256(REFERENCE_QUOTE.encode()).hexdigest()
    )
    assert reference["observations"][0]["id"] == "RF-001"
    assert "RF-001" in report["report_markdown"]
    assert REFERENCE_QUOTE in report["report_markdown"]
    assert QUOTE in report["report_markdown"]
    assert len(requests) == 4
    data = next(
        json.loads(item["messages"][1]["content"])
        for item in requests
        if json.loads(item["messages"][1]["content"])["stage"] == "reference"
    )
    assert data["stage"] == "reference"
    assert len(data["references"]) == 5
    assert data["references"][0]["passages"] == [
        {"start": 1, "end": 1, "text": REFERENCE_QUOTE}
    ]
    assert reference["retrieval"]["mode"] == "chroma_hybrid_local_features"
    assert len(reference["retrieval"]["sources"]) == 5
    assert reference["retrieval"]["sources"][0]["partial"] is False
    assert (
        report["passport"]["document_sha256"]
        == hashlib.sha256(TEXT.encode()).hexdigest()
    )
    from uuid import UUID

    assert str(UUID(report["passport"]["run_id"])) == report["passport"]["run_id"]
    assert [u["text"] for u in data["specification"]["units"]] == TEXT.splitlines()


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        "[]",
        "bad json",
        json.dumps([reference_manifest(title=" ")]),
        json.dumps([reference_manifest(source_url="file:///secret")]),
        json.dumps([reference_manifest(extra="unknown")]),
    ],
)
def test_reference_manifest_rejected_before_provider(
    review_client, monkeypatch, manifest
):
    requests = []
    configure_provider(monkeypatch, payloads(), requests)
    files = [
        ("file", ("spec.md", TEXT, "text/markdown")),
        ("reference_files", ("ref.txt", REFERENCE_QUOTE, "text/plain")),
    ]
    response = review_client.post(
        URL,
        headers=HEADERS,
        files=files,
        data={**({"reference_manifest": manifest} if manifest is not None else {}), "review_mode": "legacy"},
    )
    assert response.status_code == 422
    assert not requests


@pytest.mark.parametrize(
    "invalid",
    [
        "reference_evidence",
        "specification_evidence",
        "unknown_source",
        "unknown_requirement",
        "missing_source",
        "not_applicable",
        "unclear",
    ],
)
def test_reference_review_invalid_draft_preserves_baseline(
    review_client, monkeypatch, invalid
):
    draft = reference_payload()
    obs = draft["observations"][0]
    if invalid in {"reference_evidence", "specification_evidence"}:
        obs[invalid] = [quote("Выдуманная цитата", 1, 1)]
    elif invalid == "unknown_source":
        obs["source_id"] = "S-999"
    elif invalid == "unknown_requirement":
        obs["requirement_ids"] = ["R-999"]
    elif invalid == "missing_source":
        draft["source_reviews"] = []
    else:
        draft["source_reviews"][0]["applicability"] = invalid
    configure_provider(monkeypatch, payloads() + [draft], [])
    response = review_client.post(
        URL,
        headers=HEADERS,
        files=[
            ("file", ("spec.md", TEXT, "text/markdown")),
            ("reference_files", ("ref.txt", REFERENCE_QUOTE, "text/plain")),
        ],
        data={"review_mode": "legacy", "reference_manifest": json.dumps([reference_manifest()])},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["reference_review"]["status"] == "unavailable"
    assert report["reference_review"]["observations"] == []
    assert report["reference_review"]["sources"][0]["applicability"] == "not_reviewed"
    assert report["statistics"]["violations"] == 1
    assert report["verdict"]["status"] == "not_ready"


def test_cli_reference_manifest_relative_to_its_file(tmp_path, monkeypatch):
    from agent import spec_review_cli

    requests = []
    monkeypatch.setattr(
        spec_review_cli,
        "ChatOpenAI",
        lambda **kwargs: provider(payloads() + [reference_payload()], requests),
    )
    monkeypatch.setenv("AGENT_LLM_API_KEY", "fixture-only")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://test-provider.invalid/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "fixture")
    monkeypatch.setenv("AGENT_SPEC_REVIEW_USE_NACE", "false")
    source = tmp_path / "source.md"
    output = tmp_path / "report.json"
    source.write_text(TEXT, encoding="utf-8")
    reference_dir = tmp_path / "references"
    reference_dir.mkdir()
    (reference_dir / "policy.txt").write_text(REFERENCE_QUOTE, encoding="utf-8")
    manifest = reference_dir / "manifest.json"
    manifest.write_text(
        json.dumps([{**reference_manifest(), "path": "policy.txt"}]), encoding="utf-8"
    )
    assert (
        spec_review_cli.main(
            ["--review-mode", "legacy", 
                "--input",
                str(source),
                "--output",
                str(output),
                "--env-file",
                str(tmp_path / ".env"),
                "--reference-manifest",
                str(manifest),
                "--reference-index",
                str(tmp_path / "chroma"),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["reference_review"]["status"] == "completed"
    assert (
        report["reference_review"]["retrieval"]["mode"]
        == "chroma_hybrid_local_features"
    )
    assert (tmp_path / "chroma").is_dir()
    assert len(requests) == 4


def test_reference_input_limits(monkeypatch):
    from agent.services import spec_reference_review as reference

    entry = reference.ReferenceManifestEntry.model_validate(reference_manifest())
    files = [("ref.txt", "text/plain", b"Readable reference")]
    with pytest.raises(reference.SpecificationValidationError) as error:
        reference.parse_reference_manifest(json.dumps([reference_manifest()]), 0)
    assert error.value.code == "INVALID_REFERENCE_MANIFEST"
    with pytest.raises(reference.SpecificationValidationError) as error:
        reference.prepare_references([entry] * 9, files * 9)
    assert error.value.code == "INVALID_REFERENCE_MANIFEST"
    monkeypatch.setattr(reference, "MAX_REFERENCE_TOTAL_BYTES", 30)
    with pytest.raises(reference.SpecificationValidationError) as error:
        reference.prepare_references([entry] * 2, files * 2)
    assert error.value.code == "FILE_TOO_LARGE"
    monkeypatch.setattr(reference, "MAX_REFERENCE_TOTAL_BYTES", 100)
    monkeypatch.setattr(reference, "MAX_REFERENCE_CHARACTERS", 30)
    with pytest.raises(reference.SpecificationValidationError) as error:
        reference.prepare_references([entry] * 2, files * 2)
    assert error.value.code == "REVIEW_TEXT_LIMIT"
    monkeypatch.setattr(reference, "MAX_REFERENCE_FILE_BYTES", 10)
    with pytest.raises(reference.SpecificationValidationError) as error:
        reference.prepare_references([entry], files)
    assert error.value.code == "FILE_TOO_LARGE"


async def test_reference_timeout_and_no_remaining_budget_preserve_baseline(monkeypatch):
    from agent.services.spec_reference_review import (
        ReferenceManifestEntry,
        prepare_references,
    )
    from agent.services.spec_review import compare_references

    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    references = prepare_references(
        [
            ReferenceManifestEntry.model_validate(
                reference_manifest(source_url="https://reference.invalid/private")
            )
        ],
        [("ref.txt", "text/plain", REFERENCE_QUOTE.encode())],
    )
    baseline = await review_specification(specification, provider(payloads(), []), review_mode="legacy")
    assert baseline.reference_review is None
    llm = provider(payloads(), [])
    reference_requests = []

    async def invoke(messages, **kwargs):
        data = json.loads(messages[1].content)
        if data["stage"] == "reference":
            reference_requests.append(data)
            await asyncio.sleep(10)
        return await llm.ainvoke(messages, **kwargs)

    delayed = SimpleNamespace(model_name=llm.model_name, ainvoke=invoke)
    result = await compare_references(
        specification,
        baseline.requirements,
        references,
        delayed,
        deadline=asyncio.get_running_loop().time(),
    )
    assert result.status == "unavailable"
    assert not reference_requests
    monkeypatch.setattr("agent.services.spec_review.MAX_REFERENCE_SECONDS", 1.0)
    report = await review_specification(specification, delayed, references=references, review_mode="legacy")
    assert report.reference_review.status == "unavailable"
    assert not report.reference_review.observations
    assert report.reference_review.sources[0].applicability == "not_reviewed"
    assert report.reference_review.sources[0].source_url == (
        "https://reference.invalid/private"
    )
    assert report.findings == baseline.findings
    assert report.statistics == baseline.statistics
    assert report.verdict == baseline.verdict
    assert len(reference_requests) == 1
    assert "https://reference.invalid/private" not in json.dumps(reference_requests)


def test_reference_evidence_cannot_include_unreadable_pages():
    from agent.services.spec_reference_review import (
        ReferenceEvidence,
        verify_reference_evidence,
    )

    specification = Specification("source.pdf", "pdf", ["readable text", "", "other"])
    with pytest.raises(ValueError, match="unreadable page"):
        verify_reference_evidence(
            specification, ReferenceEvidence(start=1, end=2, quote="readable text")
        )


async def test_reference_index_failure_does_not_send_full_reference_or_erase_report():
    from agent.services.spec_reference_review import (
        ReferenceManifestEntry,
        prepare_references,
    )

    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    references = prepare_references(
        [ReferenceManifestEntry.model_validate(reference_manifest())],
        [("ref.txt", "text/plain", REFERENCE_QUOTE.encode())],
    )

    def unavailable_index():
        raise ConnectionError("Unreachable Chroma")

    requests = []
    report = await review_specification(
        specification,
        provider(payloads(), requests),
        references=references,
        reference_retriever_factory=unavailable_index, review_mode="legacy",
    )
    assert report.reference_review.status == "unavailable"
    assert report.reference_review.retrieval is None
    assert report.statistics.violations == 1
    assert report.verdict.status == "not_ready"
    assert len(requests) == 3


async def test_reference_review_uses_only_selected_passages_and_marks_partial_coverage():
    from agent.services.spec_reference_review import (
        ReferenceManifestEntry,
        prepare_references,
    )
    from agent.services.spec_reference_rag import RetrievedReferences

    specification = extract_specification("spec.md", "text/markdown", TEXT.encode())
    references = prepare_references(
        [ReferenceManifestEntry.model_validate(reference_manifest())],
        [
            (
                "ref.txt",
                "text/plain",
                (REFERENCE_QUOTE + "\nНевидимое правило 2.").encode(),
            )
        ],
    )
    retrieved = RetrievedReferences(
        passages={"S-001": [{"start": 1, "end": 1, "text": REFERENCE_QUOTE}]},
        coverage={
            "S-001": {
                "total_chunks": 2,
                "selected_chunks": 1,
                "total_characters": 60,
                "selected_characters": 42,
                "partial": True,
            }
        },
        mode="chroma_hybrid_local_features",
    )

    def factory():
        return SimpleNamespace(retrieve=lambda *_: retrieved)

    # A true original quote outside retrieved passages cannot become a citation.
    bad = reference_payload()
    bad["observations"][0]["reference_evidence"] = [quote("Невидимое правило 2.", 2, 2)]
    requests = []
    report = await review_specification(
        specification,
        provider(payloads() + [bad], requests),
        references=references,
        reference_retriever_factory=factory, review_mode="legacy",
    )
    assert report.reference_review.status == "unavailable"
    assert report.reference_review.retrieval.sources[0].partial
    item = next(
        json.loads(req["messages"][1]["content"])
        for req in requests
        if json.loads(req["messages"][1]["content"])["stage"] == "reference"
    )["references"][0]
    assert item["passages"] == retrieved.passages["S-001"]
    assert "Невидимое правило" not in json.dumps(item, ensure_ascii=False)
    # A sampled reference cannot be declared inapplicable from absent retrieval.
    draft = reference_payload(applicability="not_applicable")
    draft["observations"] = []
    report = await review_specification(
        specification,
        provider(payloads() + [draft], []),
        references=references,
        reference_retriever_factory=factory, review_mode="legacy",
    )
    assert report.reference_review.status == "completed"
    assert report.reference_review.sources[0].applicability == "unclear"
    assert report.reference_review.retrieval.sources[0].partial


async def test_normative_comparison_runs_while_individual_batches_are_in_progress():
    from agent.services.spec_reference_review import (
        ReferenceManifestEntry,
        prepare_references,
    )
    from agent.services.spec_reference_rag import RetrievedReferences

    spec = extract_specification("spec.md", "text/markdown", TEXT.encode())
    refs = prepare_references(
        [ReferenceManifestEntry.model_validate(reference_manifest())],
        [("ref.txt", "text/plain", REFERENCE_QUOTE.encode())],
    )
    fixture = RetrievedReferences(
        passages={"S-001": [{"start": 1, "end": 1, "text": REFERENCE_QUOTE}]},
        coverage={
            "S-001": {
                "total_chunks": 1,
                "selected_chunks": 1,
                "total_characters": len(REFERENCE_QUOTE),
                "selected_characters": len(REFERENCE_QUOTE),
                "partial": False,
            }
        },
        mode="chroma_hybrid_local_features",
    )
    normative_started = asyncio.Event()
    llm = provider(payloads() + [reference_payload()], [])

    async def invocation(messages, **kwargs):
        stage = json.loads(messages[1].content)["stage"]
        if stage == "individual":
            await asyncio.wait_for(normative_started.wait(), timeout=1)
        elif stage == "reference":
            normative_started.set()
        return await llm.ainvoke(messages, **kwargs)

    report = await review_specification(
        spec,
        SimpleNamespace(model_name="fixture", ainvoke=invocation),
        references=refs,
        reference_retriever_factory=lambda: SimpleNamespace(
            retrieve=lambda *_: fixture
        ), review_mode="legacy",
    )
    assert report.reference_review.status == "completed"
    assert report.statistics.violations == 1


async def test_whole_set_and_individual_checks_are_independent():
    set_started = asyncio.Event()
    individual_started = asyncio.Event()
    llm = provider(payloads(), [])

    async def invocation(messages, **kwargs):
        data = json.loads(messages[1].content)
        if data["stage"] == "individual":
            individual_started.set()
            await asyncio.wait_for(set_started.wait(), timeout=1)
        elif data["stage"] == "set":
            assert "individual_findings" not in data
            set_started.set()
            await asyncio.wait_for(individual_started.wait(), timeout=1)
        return await llm.ainvoke(messages, **kwargs)

    spec = extract_specification("spec.md", "text/markdown", TEXT.encode())
    report = await review_specification(
        spec, SimpleNamespace(model_name="fixture", ainvoke=invocation), review_mode="legacy"
    )
    assert report.statistics.violations == 1


def test_provider_rate_limit_is_returned_as_rate_limited(review_client, monkeypatch):
    async def limited(_request):
        return httpx.Response(
            429,
            json={"error": {"message": "Provider limit", "type": "rate_limit_error"}},
        )

    llm = ChatOpenAI(
        model="fixture",
        api_key="fixture-only",
        base_url="http://test-provider.invalid/v1",
        max_retries=0,
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(limited)),
    )
    monkeypatch.setattr(
        app.state, "agents", {"rag": SimpleNamespace(llm=llm)}, raising=False
    )
    response = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.md", TEXT, "text/markdown")}, data={"review_mode": "legacy"}
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "RATE_LIMITED"


def test_cli_explicit_eval_deadline_does_not_change_web_default(tmp_path, monkeypatch):
    from agent import spec_review_cli

    requests = []
    captured = []
    original = spec_review_cli.review_specification_configured

    async def review_with_deadline(specification, llm, configuration, **kwargs):
        captured.append(
            (configuration.spec_review_timeout_seconds, kwargs.get("timeout_seconds"))
        )
        return await original(specification, llm, configuration, **kwargs)

    monkeypatch.setattr(
        spec_review_cli, "review_specification_configured", review_with_deadline
    )
    monkeypatch.setattr(
        spec_review_cli, "ChatOpenAI", lambda **kwargs: provider(payloads(), requests)
    )
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-only")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://test-provider.invalid/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "fixture")
    monkeypatch.setenv("AGENT_SPEC_REVIEW_USE_NACE", "false")
    monkeypatch.setenv("AGENT_SPEC_REVIEW_TIMEOUT_SECONDS", "230")
    source = tmp_path / "spec.md"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "eval.json"
    assert (
        spec_review_cli.main(
            ["--review-mode", "legacy", 
                "--input",
                str(source),
                "--output",
                str(output),
                "--env-file",
                str(tmp_path / ".env"),
                "--timeout-seconds",
                "720",
            ]
        )
        == 0
    )
    assert captured == [(230, 720)]
    assert json.loads(output.read_text())["statistics"]["requirements"] == 2


def test_fix_proposal_is_owner_scoped_and_does_not_modify_original(
    review_client, monkeypatch
):
    requests = []
    configure_provider(monkeypatch, payloads(), requests)
    original = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"}
    ).json()
    review_id = original["passport"]["run_id"]
    fix_url = f"{URL}/{review_id}/fix"
    draft = {
        "status": "patch",
        "explanation": "Уточнено правило.",
        "question": None,
        "edits": [{"unit": 2, "before": QUOTE, "after": "PAY-1. Согласованное правило повторной операции."}],
    }
    requests = []
    configure_provider(monkeypatch, [draft], requests)
    body = {"finding_id": "D-001", "clarification": "Согласованное правило"}
    assert review_client.post(fix_url, json=body).status_code == 401
    assert review_client.post(fix_url, headers={**HEADERS, "X-User-ID": "another"}, json=body).status_code == 404
    assert requests == []
    response = review_client.post(fix_url, headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == draft
    correction_input = json.loads(requests[0]["messages"][1]["content"])
    assert "requirement_ids" not in correction_input["finding"]
    assert all("id" not in requirement for requirement in correction_input["affected_requirements"])
    assert correction_input["affected_requirements"][0]["source_id"] == "PAY-1"
    archived = review_client.get(f"{URL}/{review_id}", headers=HEADERS).json()
    assert archived["report"] == original
    assert archived["source_units"][1] == QUOTE
    replacement = draft["edits"][0]["after"]
    revised_text = TEXT.replace(QUOTE, replacement)
    revised_payloads = payloads(first_quote=replacement)
    revised_payloads[1]["findings"] = []
    for requirement in revised_payloads[1]["requirements"]:
        for criterion in requirement["checks"]:
            criterion["status"] = "pass"
    configure_provider(monkeypatch, revised_payloads, [])
    result = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec-revised.txt", revised_text, "text/plain")}, data={"review_mode": "legacy"}
    )
    assert result.status_code == 200, result.text
    revised = result.json()
    assert revised["statistics"]["violations"] == 0
    assert revised["passport"]["run_id"] != review_id
    assert revised["passport"]["document_sha256"] != original["passport"]["document_sha256"]
    assert review_client.get(f"{URL}/{review_id}", headers=HEADERS).json() == archived


def test_fix_proposal_rejects_fabricated_anchor(review_client, monkeypatch):
    configure_provider(monkeypatch, payloads(), [])
    original = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"}
    ).json()
    configure_provider(monkeypatch, [{
        "status": "patch",
        "explanation": "Уточнение.",
        "question": None,
        "edits": [{"unit": 2, "before": "Нет такой цитаты", "after": "Правка"}],
    }], [])
    response = review_client.post(
        f"{URL}/{original['passport']['run_id']}/fix",
        headers=HEADERS, json={"finding_id": "D-001", "clarification": ""},
    )
    assert response.status_code == 502


def test_fix_proposal_does_not_insert_report_only_identifiers(review_client, monkeypatch):
    configure_provider(monkeypatch, payloads(), [])
    original = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"}
    ).json()
    configure_provider(monkeypatch, [{
        "status": "patch",
        "explanation": "Уточнение.",
        "question": None,
        "edits": [{"unit": 2, "before": QUOTE, "after": QUOTE + " (R-001)"}],
    }], [])
    response = review_client.post(
        f"{URL}/{original['passport']['run_id']}/fix",
        headers=HEADERS, json={"finding_id": "D-001", "clarification": ""},
    )
    assert response.status_code == 502


def test_fix_can_request_missing_business_decision(review_client, monkeypatch):
    configure_provider(monkeypatch, payloads(), [])
    original = review_client.post(
        URL, headers=HEADERS, files={"file": ("spec.txt", TEXT, "text/plain")}, data={"review_mode": "legacy"}
    ).json()
    draft = {
        "status": "clarification",
        "explanation": "Не хватает правила.",
        "question": "Что считать успешной операцией?",
        "edits": [],
    }
    configure_provider(monkeypatch, [draft], [])
    response = review_client.post(
        f"{URL}/{original['passport']['run_id']}/fix",
        headers=HEADERS, json={"finding_id": "D-001", "clarification": ""},
    )
    assert response.status_code == 200
    assert response.json() == draft


def test_http_review_defaults_to_multi_agent(review_client, monkeypatch):
    from test_spec_multi_agent import ExpertChat, SOURCE
    from types import SimpleNamespace

    monkeypatch.setattr(app.state, "agents", {"rag": SimpleNamespace(llm=ExpertChat())}, raising=False)
    response = review_client.post(URL, headers=HEADERS, files={"file": ("requirements.txt", SOURCE, "text/plain")})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["passport"]["review_mode"] == "multi_agent"
    assert report["evidence_graph"]["nodes"]
