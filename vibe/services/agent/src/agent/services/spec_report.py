"""Render a complete, human-readable Russian Markdown report from verified data."""

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.spec_review import Evidence, ReviewReport

STATUS = {
    "pass": "Пройдено",
    "fail": "Нарушение",
    "uncertain": "Требует уточнения",
    "not_applicable": "Не применимо",
}
VERDICT = {
    "ready": "Готово",
    "needs_revision": "Нужна доработка",
    "not_ready": "Не готово",
}
SEVERITY = {
    "critical": "Критическая",
    "high": "Высокая",
    "medium": "Средняя",
    "low": "Низкая",
}
KIND = {
    "missing": "Пробел",
    "ambiguous": "Неоднозначность",
    "conflict": "Противоречие",
    "untestable": "Непроверяемость",
    "other": "Другое",
}
DECISION_STATUS = {
    "completed": "Выполнена",
    "unavailable": "Не выполнена",
    "not_applicable": "Не требуется",
}
DECISION_FINDING_STATUS = {
    "supported": "Поддержано",
    "uncertain": "Сомнительно",
    "unsupported": "Не поддержано",
}
REFERENCE_CATEGORY = {
    "quality_standard": "Стандарт качества требований",
    "bank_policy": "Внутренний регламент банка",
    "api_testing": "Стандарты API и тестирования",
    "payments_data": "Платежи и персональные данные",
    "security_regulation": "Информационная безопасность",
}
REFERENCE_APPLICABILITY = {
    "relevant": "Возможно применим",
    "unclear": "Требует уточнения",
    "not_applicable": "Не относится к области ТЗ",
    "not_reviewed": "Не проверен",
}
REFERENCE_OBSERVATION = {
    "potential_discrepancy": "Потенциальное расхождение",
    "missing_evidence": "Недостаточно подтверждений в ТЗ",
    "unclear_applicability": "Неясная применимость",
}


def safe_text(value: str) -> str:
    """Display model/source text as data rather than active Markdown or HTML."""
    clean = html.escape(" ".join(value.split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", clean)


def evidence_text(evidence: "Evidence", source: str) -> str:
    label = (
        "стр."
        if source == "pdf"
        else ("изображение" if evidence.start == evidence.end else "изображения")
        if source in {"jpg", "jpeg", "png"}
        else ("блок" if evidence.start == evidence.end else "блоки")
        if source == "docx"
        else ("строка выгрузки" if evidence.start == evidence.end else "строки выгрузки")
        if source == "xlsx"
        else "строка"
        if evidence.start == evidence.end
        else "строки"
    )
    location = str(evidence.start) + (
        f"–{evidence.end}" if evidence.start != evidence.end else ""
    )
    return f"{label} {location}: «{safe_text(evidence.quote)}»"


def render_markdown(report: "ReviewReport") -> str:
    from agent.services.spec_review import READINESS_CRITERIA

    p, s, v = report.passport, report.statistics, report.verdict
    graph = getattr(report, "evidence_graph", None)
    graph_nodes = {node.id: node for node in graph.nodes} if graph else {}
    experts_by_finding: dict[str, set[str]] = {}
    if graph:
        for edge in graph.edges:
            if edge.kind != "contributes_to":
                continue
            observation, finding = graph_nodes.get(edge.source), graph_nodes.get(edge.target)
            if observation and finding and observation.role and finding.finding_id:
                experts_by_finding.setdefault(finding.finding_id, set()).add(observation.role)
    lines = [
        "# Отчёт о проверке требований",
        "",
        "## Паспорт прогона",
        "",
        f"- Документ: {safe_text(p.document_title)}",
        f"- Дата и время: {safe_text(p.run_at)}",
        f"- Модель: {safe_text(p.model)}",
        f"- Версия агента: {safe_text(p.agent_version)}",
        *(["- Режим: мультиагентное ревью (Evidence-Driven)"] if getattr(p, "review_mode", "legacy") == "multi_agent" else []),
        *([f"- ID запуска: `{p.run_id}`"] if p.run_id else []),
        *([f"- SHA-256 ТЗ: `{p.document_sha256}`"] if p.document_sha256 else []),
        "- Метод: статический анализ качества требований на основе характеристик ISO/IEC/IEEE 29148.",
        "",
        "## Сводка",
        "",
        safe_text(report.summary),
        "",
        "| Показатель | Значение |",
        "|---|---:|",
        f"| Требований | {s.requirements} |",
        f"| Нарушений | {s.violations} |",
        f"| Критических нарушений | {s.critical} |",
        f"| Требований без замечаний | {s.passed} |",
        f"| Требований к доработке | {s.needs_revision} |",
        "",
    ]
    if report.unreadable_pages:
        lines.extend(
            [
                "**Ограничение:** отсутствует извлечённый текст страниц "
                + ", ".join(map(str, report.unreadable_pages))
                + ".",
                "",
            ]
        )
    if report.ocr_pages:
        lines.extend(["**OCR:** распознан текст страниц " + ", ".join(map(str, report.ocr_pages)) + ". Цитаты следует сверить с исходным изображением.", ""])
    if report.unreadable_units:
        lines.extend(["**Ограничение:** не удалось распознать изображения в блоках " + ", ".join(map(str, report.unreadable_units)) + ". Проверка документа неполная.", ""])
    if report.ocr_units:
        lines.extend(["**OCR:** распознан текст изображений в блоках " + ", ".join(map(str, report.ocr_units)) + ". Цитаты следует сверить с оригинальными фотографиями.", ""])
    if graph:
        lines.extend([
            "## Независимый экспертный анализ", "",
            f"- Граф доказательств: {len(graph.nodes)} узлов, {len(graph.edges)} связей.",
            f"- Финализация: {'завершена' if graph.finalization_status == 'completed' else 'детерминированная агрегация (резервный режим)' }.",
            f"- Неразрешённых разногласий: {graph.unresolved_count}.", "",
            "| Эксперт | Статус | Наблюдения |", "|---|---|---:|",
        ])
        status_labels = {"completed": "Завершён", "unavailable": "Недоступен", "skipped": "Не запускался"}
        for expert in graph.experts:
            lines.append(f"| {safe_text(expert.role)} | {status_labels[expert.status]} | {expert.observations} |")
        lines.extend(["", "Полный граф с оригинальными связями и моделируемыми сценариями доступен в JSON и интерактивном отчёте.", ""])
    lines.extend(
        [
            "## Выделенные требования",
            "",
            "| ID отчёта | ID документа | Формулировка | Источник |",
            "|---|---|---|---|",
        ]
    )
    for req in report.requirements:
        lines.append(
            f"| {req.id} | {safe_text(req.source_id or '—')} | {safe_text(req.text)} | {evidence_text(req.evidence, report.source)} |"
        )
    if not report.requirements:
        lines.append("| — | — | Не выделено проверяемых требований | — |")
    if report.extraction_gaps:
        lines.extend(["", "## Непокрытые кандидаты на требования", "", "Программная проверка обнаружила явные формулировки без подтверждённой записи в реестре. Это кандидаты для сверки, а не автоматически установленные нарушения.", ""])
        lines.extend(f"- {evidence_text(item, report.source)}" for item in report.extraction_gaps)
    lines.extend(["", "## Проверка каждого требования", ""])
    for req in report.requirements:
        lines.extend(
            [
                f"### {req.id} — {safe_text(req.text)}",
                "",
                "| Критерий | Результат | Комментарий |",
                "|---|---|---|",
            ]
        )
        for check in req.checks:
            lines.append(
                f"| {safe_text(check.criterion)} | {STATUS[check.status]} | {safe_text(check.comment)} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Проверка набора требований",
            "",
            "| Критерий | Результат | Комментарий и источник |",
            "|---|---|---|",
        ]
    )
    for check in report.set_checks:
        comment = safe_text(check.comment)
        sources = "; ".join(
            evidence_text(item, report.source) for item in check.evidence
        )
        lines.append(
            f"| {safe_text(check.criterion)} | {STATUS[check.status]} | {comment + ('; ' + sources if sources else '')} |"
        )
    lines.extend(["", "## Выявленные нарушения", ""])
    if not report.findings:
        lines.extend(["Существенные нарушения не обнаружены в прочитанном тексте.", ""])
    for index, finding in enumerate(report.findings, 1):
        lines.extend(
            [
                f"### D-{index:03d} — {KIND[finding.kind]}",
                "",
                f"- **Критичность:** {SEVERITY[finding.severity]}",
                f"- **Требования:** {', '.join(finding.requirement_ids) or 'Набор в целом'}",
                f"- **Нарушение:** {safe_text(finding.issue)}",
                f"- **Что исправить:** {safe_text(finding.recommendation)}",
            ]
        )
        if roles := experts_by_finding.get(f"D-{index:03d}"):
            lines.append(f"- **Экспертные источники:** {', '.join(sorted(roles))}")
        if finding.evidence:
            lines.extend(
                f"- **Источник:** {evidence_text(item, report.source)}"
                for item in finding.evidence
            )
        else:
            lines.append(
                "- **Источник:** пробел в документе; соответствующее требование отсутствует."
            )
        lines.append("")
    lines.extend(["## Критические дефекты", ""])
    critical = [
        (i, f) for i, f in enumerate(report.findings, 1) if f.severity in {"critical", "high"}
    ]
    if critical:
        lines.append("Блокирующие замечания критического и высокого приоритета. Счётчик критических нарушений в сводке учитывает только уровень Critical.")
        lines.extend(f"- D-{i:03d} ({SEVERITY[f.severity]}): {safe_text(f.issue)}" for i, f in critical)
    else:
        lines.append("Замечания критического или высокого приоритета не выявлены.")
    if report.clarification_questions:
        lines.extend(["", "## Уточняющие вопросы", "", "Вопросы упорядочены по приоритету и не заменяют решения владельца требований.", ""])
        for item in report.clarification_questions:
            anchor = item.finding_id or READINESS_CRITERIA.get(item.checklist_id or "", "Чек-лист")
            lines.append(f"- **{item.id} · {SEVERITY[item.priority]} · {safe_text(anchor)}:** {safe_text(item.question)}")
            if item.requirement_ids:
                lines.append(f"  - Требования: {', '.join(item.requirement_ids)}")
            lines.extend(f"  - Источник: {evidence_text(e, report.source)}" for e in item.evidence)
    if report.readiness_checklist:
        availability = {"present": "Присутствует", "missing": "Отсутствует", "uncertain": "Не подтверждено", "not_applicable": "Не применимо"}
        lines.extend(["", "## Чек-лист готовности ТЗ", "", "Оценка содержания документа, а не подтверждение подписей, юридического или регуляторного соответствия.", "", "| Проверка | Наличие | Результат | Примечание и доказательства |", "|---|---|---|---|"])
        for row in report.readiness_checklist:
            evidence = "; ".join(evidence_text(item, report.source) for item in row.evidence)
            detail = safe_text(row.comment) + (f"; {evidence}" if evidence else "")
            lines.append(f"| {safe_text(READINESS_CRITERIA[row.id])} | {availability[row.availability]} | {STATUS[row.status]} | {detail} |")
    if report.decision_review is not None:
        decision = report.decision_review
        lines.extend(
            [
                "",
                "## Независимая проверка Drex",
                "",
                f"- Статус: {DECISION_STATUS[decision.status]}",
                f"- Модель: {safe_text(decision.model)}",
                "",
                safe_text(decision.explanation),
                "",
                "Независимая оценка не изменяет исходные замечания, статистику и итоговый вывод.",
            ]
        )
        if decision.findings:
            lines.extend(
                [
                    "",
                    "| Замечание | Вероятность обоснованности | Оценка |",
                    "|---|---:|---|",
                ]
            )
            lines.extend(
                f"| {item.id} | {item.probability:.2%} | {DECISION_FINDING_STATUS[item.status]} |"
                for item in decision.findings
            )
    if report.reference_review is not None:
        reference = report.reference_review
        lines.extend(
            [
                "",
                "## Сопоставление с регламентами и стандартами",
                "",
                f"- Статус: {'Выполнено' if reference.status == 'completed' else 'Не выполнено'}",
                "",
                safe_text(reference.explanation),
                "",
                "Результаты сопоставления не изменяют исходные замечания, статистику и итоговый вывод по качеству ТЗ.",
                "",
                "### Реестр предоставленных источников",
                "",
            ]
        )
        coverage_by_id = {}
        if reference.retrieval is not None:
            coverage_by_id = {
                item.source_id: item for item in reference.retrieval.sources
            }
            mode = (
                "ChromaDB · BM25 + векторный поиск по настроенной модели"
                if reference.retrieval.mode == "chroma_hybrid_configured_embeddings"
                else "ChromaDB · BM25 + локальные лексические признаки (не семантическая embedding-модель)"
            )
            lines.extend(
                [
                    f"- Поиск: {mode}",
                    "",
                    "Неполный охват означает, что проверена только выборка фрагментов.",
                    "",
                ]
            )
        by_id = {source.id: source for source in reference.sources}
        for source in reference.sources:
            lines.extend(
                [
                    f"#### {source.id} — {safe_text(source.title)}",
                    "",
                    f"- Категория: {REFERENCE_CATEGORY[source.category]}",
                    f"- Издатель / владелец (указан пользователем): {safe_text(source.issuer)}",
                    f"- Версия (указана пользователем): {safe_text(source.version)}",
                    f"- Область применения (указана пользователем): {safe_text(source.scope)}",
                    f"- Ссылка: {safe_text(source.source_url or '—')}",
                    f"- Файл: {safe_text(source.filename)}",
                    f"- SHA-256: `{source.sha256}`",
                    f"- Применимость: {REFERENCE_APPLICABILITY[source.applicability]}",
                    f"- Сводка: {safe_text(source.summary)}",
                ]
            )
            coverage = coverage_by_id.get(source.id)
            if coverage is not None:
                lines.append(
                    f"- Охват поиска: {coverage.selected_chunks}/{coverage.total_chunks} фрагментов; "
                    f"{coverage.selected_characters}/{coverage.total_characters} знаков. "
                    + (
                        "Частичный."
                        if coverage.partial
                        else "Полный по индексированному тексту."
                    )
                )
            if source.unreadable_pages:
                lines.append(
                    "- Ограничение: отсутствует текст страниц "
                    + ", ".join(map(str, source.unreadable_pages))
                    + "."
                )
            if source.ocr_pages:
                lines.append("- OCR: распознаны страницы " + ", ".join(map(str, source.ocr_pages)) + ". Сверяйте цитаты с оригиналом.")
            if source.unreadable_units:
                lines.append("- Не распознаны изображения в блоках " + ", ".join(map(str, source.unreadable_units)) + ".")
            if source.ocr_units:
                lines.append("- OCR изображений: блоки " + ", ".join(map(str, source.ocr_units)) + ". Сверяйте цитаты с оригиналом.")
            lines.append("")
        lines.extend(["### Наблюдения для уточнения", ""])
        if not reference.observations:
            lines.extend(
                [
                    "Наблюдения не сформированы. Это не означает соответствие всем требованиям.",
                    "",
                ]
            )
        for observation in reference.observations:
            source = by_id[observation.source_id]
            lines.extend(
                [
                    f"#### {observation.id} — {REFERENCE_OBSERVATION[observation.status]}",
                    "",
                    f"- **Источник:** {source.id} — {safe_text(source.title)}",
                    f"- **Требования:** {', '.join(observation.requirement_ids) or 'ТЗ в целом'}",
                    f"- **Наблюдение:** {safe_text(observation.issue)}",
                    f"- **Что проверить:** {safe_text(observation.recommendation)}",
                ]
            )
            lines.extend(
                f"- **Цитата из ТЗ:** {evidence_text(evidence, report.source)}"
                for evidence in observation.specification_evidence
            )
            lines.extend(
                f"- **Цитата из {source.id}:** {evidence_text(evidence, source.source)}"
                for evidence in observation.reference_evidence
            )
            lines.append("")
        if reference.decision_review is not None:
            decision = reference.decision_review
            lines.extend(
                [
                    "### Независимая проверка нормативных замечаний Drex",
                    "",
                    f"- Статус: {DECISION_STATUS[decision.status]}",
                    f"- Модель: {safe_text(decision.model)}",
                    "",
                    safe_text(decision.explanation),
                    "",
                ]
            )
            if decision.findings:
                lines.extend(
                    [
                        "| Замечание | Вероятность обоснованности | Оценка |",
                        "|---|---:|---|",
                    ]
                )
                lines.extend(
                    f"| {item.id} | {item.probability:.2%} | {DECISION_FINDING_STATUS[item.status]} |"
                    for item in decision.findings
                )
    lines.extend(
        ["", "## Итог", "", f"**{VERDICT[v.status]}**", "", safe_text(v.reason), ""]
    )
    if v.priorities:
        lines.extend(["### Приоритетные исправления", ""])
        lines.extend(
            f"{i}. {safe_text(item)}" for i, item in enumerate(v.priorities, 1)
        )
    lines.extend(
        [
            "",
            "---",
            "",
            "Отчёт сформирован автоматически. Выводы проверяются владельцем требований перед утверждением.",
            "",
        ]
    )
    return "\n".join(lines)
