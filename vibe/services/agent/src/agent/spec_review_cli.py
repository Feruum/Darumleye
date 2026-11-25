"""Run the same specification-review pipeline without HTTP, Docker, or storage."""

import argparse
import asyncio
import json
import sys
from collections import Counter
from hashlib import sha256
from typing import get_args
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from langchain_openai import ChatOpenAI

from agent.config.settings import Settings
from agent.services.spec_eval_checkpoint import (
    RateLimitWaitingChat,
    StageCheckpoint,
    checkpoint_scope,
)
from agent.services.spec_document import (
    MAX_REVIEW_FILE_BYTES,
    SpecificationValidationError,
    Source,
    extract_specification,
)
from agent.services.spec_review import (
    ReviewPipelineError,
    ReviewProviderRateLimit,
    ReviewReport,
    review_specification_configured,
)
from agent.services.spec_review_mode import ACTIVE_REVIEW_MODES, new_review_mode
from agent.services.spec_reference_review import ReferenceDocument, load_reference_files
from agent.services.spec_reference_context import configured_reference_retriever
from agent.spec_cli_progress import ProgressChat, ReviewProgress, elapsed_label
from agent.utils.observability import configure_observability, flush_observability
from agent.utils.review_telemetry import review_run, review_stage


def default_env_file() -> Path:
    current = Path.cwd() / ".env"
    if current.is_file():
        return current
    # Editable checkout: vibe/services/agent/src/agent/spec_review_cli.py.
    root = Path(__file__).resolve().parents[4] / ".env"
    return root if root.is_file() else current


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as temporary:
            temp_name = Path(temporary.name)
            temporary.write(content)
        temp_name.replace(path)
    finally:
        if temp_name is not None:
            temp_name.unlink(missing_ok=True)


def checkpoint_run_id(args: argparse.Namespace, configuration: Settings) -> str | None:
    if args.run_id is not None:
        return str(args.run_id)
    if args.checkpoint_dir is None or args.reference_manifest is not None:
        # Explicit --run-id is used for reference runs so an edited attachment
        # cannot be accidentally correlated as the same logical review.
        return None
    with args.input.open("rb") as document:
        digest = sha256(document.read(MAX_REVIEW_FILE_BYTES + 1)).hexdigest()
    key = sha256(json.dumps([digest, args.review_mode, configuration.llm_model, configuration.llm_base_url, configuration.spec_review_use_nace, configuration.nace_model], separators=(",", ":")).encode()).hexdigest()
    directory = args.checkpoint_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f".session-{key}"
    if path.exists():
        return str(UUID(path.read_text().strip()))
    identifier = str(uuid4())
    write_report(path, identifier + "\n")
    return identifier


async def run(args: argparse.Namespace, progress: ReviewProgress) -> ReviewReport:
    configuration = Settings(_env_file=args.env_file)
    metrics_path = args.metrics_output.resolve() if args.metrics_output else None
    if metrics_path is not None:
        for protected in (args.input.resolve(), args.output.resolve(), args.env_file.resolve()):
            if metrics_path == protected or (metrics_path.exists() and protected.exists() and metrics_path.samefile(protected)):
                raise ValueError("Metrics output must not replace the input or report")
    configure_observability(configuration)
    try:
        with review_run(checkpoint_run_id(args, configuration), review_mode=args.review_mode, source="cli") as trace_run:
            references = []
            if args.reference_manifest:
                progress.set_phase("Загрузка дополнительных источников")
                # Check every output before enabling the failure metrics sidecar.
                outputs = [args.output.resolve()]
                if metrics_path is not None:
                    outputs.append(metrics_path)
                references = load_reference_files(args.reference_manifest.resolve(), *outputs)
            try:
                report = await _run_review(args, progress, configuration, trace_run.run_id, references)
            except BaseException as error:
                if metrics_path is not None:
                    status = "cancelled" if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
                    try:
                        write_report(metrics_path, json.dumps({**trace_run.usage_summary(), "run_id": trace_run.run_id, "status": status}, indent=2) + "\n")
                    except OSError:
                        pass  # Keep the original review failure, never overwrite it with a telemetry failure.
                raise
            trace_run.report(report)
            trace_run.outcome("completed")
            if metrics_path is not None:
                write_report(metrics_path, json.dumps({**trace_run.usage_summary(), "run_id": trace_run.run_id, "status": "completed"}, indent=2) + "\n")
            return report
    finally:
        flush_observability()


async def _run_review(args: argparse.Namespace, progress: ReviewProgress, configuration: Settings,
                      run_id: str, references: list[ReferenceDocument]) -> ReviewReport:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path or (
        output_path.exists() and input_path.samefile(output_path)
    ):
        raise ValueError("Input and output must be different files")
    if output_path.suffix.lower() not in {".md", ".json"}:
        raise ValueError("Output must be a .md or .json file")
    progress.set_phase("Чтение документа и извлечение текста (OCR при необходимости)")
    with review_stage("document-parsing"), input_path.open("rb") as document:
        specification = extract_specification(
            input_path.name,
            "application/octet-stream",
            document.read(MAX_REVIEW_FILE_BYTES + 1),
        )
    progress.line(
        f"Документ принят: {specification.source.upper()} · извлечено единиц: {len(specification.units)}"
    )
    if specification.ocr_pages or getattr(specification, "ocr_units", []):
        progress.line(
            f"OCR: распознано страниц/частей: {len(specification.ocr_pages) + len(getattr(specification, 'ocr_units', []))}"
        )
    progress.line(
        f"Блоков извлечения требований: {len(specification.extraction_chunks())}"
    )
    progress.set_phase("Подготовка AI-проверки")
    if not all(
        (configuration.llm_base_url, configuration.llm_api_key, configuration.llm_model)
    ):
        raise ValueError(
            "Configure AGENT_LLM_BASE_URL, AGENT_LLM_API_KEY and AGENT_LLM_MODEL in the environment or .env file"
        )
    llm = ChatOpenAI(
        model=configuration.llm_model,
        base_url=configuration.llm_base_url,
        api_key=configuration.llm_api_key,
        temperature=0,
        timeout=configuration.llm_timeout_seconds,
        max_retries=configuration.llm_max_retries,
    )

    def client_factory():
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        return chromadb.PersistentClient(
            path=str(args.reference_index.resolve()),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    if args.wait_rate_limit:
        llm = RateLimitWaitingChat(llm)
    llm = ProgressChat(llm, progress)
    checkpoint = (
        StageCheckpoint(
            args.checkpoint_dir, provider_identity=configuration.llm_base_url
        )
        if args.checkpoint_dir
        else None
    )
    progress.set_phase(
        f"AI-анализ · модель: {terminal_text(configuration.llm_model)} · Drex: {'включён' if configuration.spec_review_use_nace else 'выключен'}"
    )
    with checkpoint_scope(checkpoint):
        report = await review_specification_configured(
            specification,
            llm,
            configuration,
            references=references,
            reference_retriever_factory=configured_reference_retriever(
                configuration, "local-cli", client_factory
            ),
            timeout_seconds=args.timeout_seconds,
            review_mode=args.review_mode,
            run_id=run_id,
        )
    progress.set_phase("Сохранение отчёта")
    content = (
        report.report_markdown
        if output_path.suffix.lower() == ".md"
        else report.model_dump_json(indent=2) + "\n"
    )
    with review_stage("archive"):
        write_report(output_path, content)
    return report


def default_output(input_path: Path, extension: str) -> Path:
    """Avoid replacing a previous report when the user didn't choose an output."""
    base = input_path.with_name(f"{input_path.stem}.review.{extension}")
    index = 2
    while base.exists() or base.is_symlink():
        base = input_path.with_name(f"{input_path.stem}.review-{index}.{extension}")
        index += 1
    return base


VERDICTS = {
    "ready": "Готово",
    "needs_revision": "Нужна доработка",
    "not_ready": "Не готово",
}


def terminal_text(value: str) -> str:
    return " ".join(
        "".join(char for char in value if char.isprintable() or char.isspace()).split()
    )


def print_summary(report: ReviewReport, path: Path, seconds: float, *, stream) -> None:
    stats = report.statistics
    severity = Counter(finding.severity for finding in report.findings)
    print("\n" + "─" * 58, file=stream)
    print(f"  SPEC REVIEW   |   {VERDICTS[report.verdict.status]}", file=stream)
    print("─" * 58, file=stream)
    print(f"  Требований:              {stats.requirements}", file=stream)
    print(
        f"  Прошло / к доработке:    {stats.passed} / {stats.needs_revision}",
        file=stream,
    )
    print(f"  Замечаний:               {stats.violations}", file=stream)
    print(
        f"    critical / high:      {severity['critical']} / {severity['high']}",
        file=stream,
    )
    print(
        f"    medium / low:         {severity['medium']} / {severity['low']}",
        file=stream,
    )
    print(f"  Время:                   {elapsed_label(seconds)}", file=stream)
    print(
        f"  Модель:                  {terminal_text(report.passport.model)}",
        file=stream,
    )
    if report.passport.review_mode in ACTIVE_REVIEW_MODES:
        mode = "прямая валидация" if report.passport.review_mode == "multi_agent_direct" else "парная валидация"
        print(f"  Режим:                   мультиагентный ({mode})", file=stream)
        if graph := report.evidence_graph:
            print(f"  Граф доказательств:      {len(graph.nodes)} узлов / {len(graph.edges)} связей", file=stream)
            print(f"  Спорных выводов:         {graph.unresolved_count}", file=stream)
    if report.unreadable_pages:
        print(f"  Нечитаемых страниц PDF:  {len(report.unreadable_pages)}", file=stream)
    if getattr(report, "unreadable_units", []):
        print(f"  Нечитаемых частей:       {len(report.unreadable_units)}", file=stream)
    if report.decision_review is not None:
        status = {
            "completed": "завершена",
            "unavailable": "недоступна",
            "not_applicable": "не требуется",
        }.get(report.decision_review.status, report.decision_review.status)
        print(f"  Проверка Drex:           {status}", file=stream)
    print(f"\n  Итог: {terminal_text(report.verdict.reason)}", file=stream)
    if report.verdict.priorities:
        print("  Приоритеты:", file=stream)
        for index, priority in enumerate(report.verdict.priorities[:3], 1):
            print(f"    {index}. {terminal_text(priority)}", file=stream)
    print(f"\n  Отчёт: {path.resolve()}", file=stream)
    print("─" * 58, file=stream, flush=True)


DOCUMENT_ERRORS = {
    "INVALID_FILENAME": "Некорректное имя файла.",
    "UNSUPPORTED_FILE_TYPE": "Этот формат не поддерживается.",
    "CONTENT_TYPE_MISMATCH": "Формат файла не соответствует его расширению.",
    "FILE_TOO_LARGE": "Превышен допустимый размер файла или дополнительных источников.",
    "EMPTY_FILE": "Файл пуст. Выберите документ с содержимым.",
    "INVALID_PDF": "Не удалось прочитать PDF. Проверьте, что файл не повреждён и не защищён паролем.",
    "INVALID_OFFICE": "Не удалось прочитать Office-документ. Попробуйте сохранить исправную копию.",
    "INVALID_TEXT": "Не удалось прочитать текст. Проверьте кодировку и содержимое файла.",
    "INVALID_IMAGE": "Не удалось распознать изображение. Попробуйте более чёткий скан.",
    "REVIEW_NO_TEXT": "В документе не найден читаемый текст. Проверьте качество скана или исходный файл.",
    "REVIEW_TEXT_LIMIT": "Превышен лимит извлечённого текста или страниц. Разделите документ на части.",
    "INVALID_REFERENCE_MANIFEST": "Проверьте метаданные и пути дополнительных источников.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-spec-review",
        description="Spec Review — AI-проверка технических требований",
        epilog="Пример: agent-spec-review requirements.pdf -o report.md. Без -o отчёт сохраняется рядом с исходным файлом.",
    )
    formats = ", ".join("." + value for value in get_args(Source))
    parser.add_argument(
        "document",
        nargs="?",
        type=Path,
        metavar="ДОКУМЕНТ",
        help=f"Исходный файл ({formats})",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        help="Исходный документ (альтернатива позиционному аргументу)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Путь к отчёту .md или .json; по умолчанию рядом с документом",
    )
    parser.add_argument(
        "--format", choices=("md", "json"), help="Формат отчёта (по умолчанию Markdown)"
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Только путь готового отчёта в stdout, без прогресса",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Напечатать содержимое готового отчёта в stdout; прогресс и сводка в stderr",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=default_env_file(),
        help="Путь к .env с настройками AI",
    )
    parser.add_argument(
        "--reference-manifest",
        type=Path,
        help="JSON-файл с метаданными и путями нормативных источников",
    )
    parser.add_argument(
        "--reference-index",
        type=Path,
        default=Path(".artifacts/reference-chroma"),
        help="Каталог локального индекса Chroma",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Общий тайм-аут AI-проверки, 30–900 секунд",
    )
    parser.add_argument(
        "--review-mode",
        type=new_review_mode,
        choices=ACTIVE_REVIEW_MODES,
        default="multi_agent",
        help="Мультиагентное ревью с парной или прямой валидацией",
    )
    parser.add_argument("--metrics-output", type=Path, help="Отдельный JSON с наблюдаемыми метриками генерации")
    parser.add_argument("--run-id", type=UUID, help="UUID логического прогона для связи повторных попыток (в том числе проверок с регламентами)")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=None,
        help="Каталог контрольных точек для возобновления проверки",
    )
    parser.add_argument(
        "--wait-rate-limit",
        action="store_true",
        help="При HTTP 429 дождаться разрешённой повторной попытки в пределах тайм-аута",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.document is None and args.input is None:
        parser.error("укажите путь к документу")
    if args.document is not None and args.input is not None:
        parser.error(
            "укажите документ один раз: позиционным аргументом или через --input"
        )
    args.input = args.input or args.document
    if not args.input.is_file():
        print(
            "Ошибка: входной файл не найден или не является обычным файлом.",
            file=sys.stderr,
        )
        return 1
    if args.output is not None:
        extension = args.output.suffix.lower().lstrip(".")
        if extension not in {"md", "json"}:
            parser.error("--output должен иметь расширение .md или .json")
        if args.format and args.format != extension:
            parser.error("--format не соответствует расширению --output")
    else:
        args.output = default_output(args.input, args.format or "md")
    if args.timeout_seconds is not None and not 30 <= args.timeout_seconds <= 900:
        parser.error("--timeout-seconds должен быть от 30 до 900")

    progress = ReviewProgress(enabled=not args.quiet)
    try:
        with progress, progress.capture_logs():
            report = asyncio.run(run(args, progress))
    except SpecificationValidationError as error:
        print(
            f"Ошибка: {DOCUMENT_ERRORS.get(error.code, 'Документ не прошёл проверку формата или ограничений.')} ({error.code})",
            file=sys.stderr,
        )
        return 1
    except ReviewProviderRateLimit:
        print(
            "Ошибка: AI-провайдер ограничил запросы. Повторите запуск позже или используйте --wait-rate-limit.",
            file=sys.stderr,
        )
        return 1
    except ReviewPipelineError:
        print(
            "Ошибка: AI-анализ не завершён или модель вернула некорректный результат. Отчёт не записан.",
            file=sys.stderr,
        )
        return 1
    except TimeoutError:
        print(
            "Ошибка: истёк срок проверки. Попробуйте документ меньшего размера или увеличьте --timeout-seconds.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Проверка прервана пользователем.", file=sys.stderr)
        return 130
    except OSError:
        print(
            "Ошибка: не удалось прочитать входной файл или сохранить отчёт. Проверьте путь и права доступа.",
            file=sys.stderr,
        )
        return 1
    except ValueError as error:
        # Never print third-party errors which can contain provider secrets.
        messages = {
            "Input and output must be different files": "Исходный файл и отчёт должны быть разными файлами.",
            "Output must be a .md or .json file": "Отчёт должен иметь расширение .md или .json.",
            "Metrics output must not replace the input or report": "Файл метрик должен отличаться от исходного документа, конфигурации и отчёта.",
            "Configure AGENT_LLM_BASE_URL, AGENT_LLM_API_KEY and AGENT_LLM_MODEL in the environment or .env file": "Настройте AGENT_LLM_BASE_URL, AGENT_LLM_API_KEY и AGENT_LLM_MODEL в .env.",
        }
        print(
            f"Ошибка: {messages.get(str(error), 'Некорректная конфигурация или ответ модели.')}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "Ошибка: не удалось завершить проверку выбранной AI-моделью.",
            file=sys.stderr,
        )
        return 1

    if args.print_report:
        if not args.quiet:
            print_summary(report, args.output, progress.elapsed, stream=sys.stderr)
        content = (
            report.model_dump_json(indent=2) + "\n"
            if args.output.suffix.lower() == ".json"
            else report.report_markdown
        )
        print(content, end="" if content.endswith("\n") else "\n")
    elif args.quiet:
        print(args.output.resolve())
    else:
        print_summary(report, args.output, progress.elapsed, stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
