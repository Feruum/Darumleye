import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { agentCompletedReviewDetailSchema, agentSpecReadinessLabels, agentSpecReviewSchema } from '@web-app-demo/contracts'

import { SpecReviewReport } from '../src/features/agent/SpecReviewReport'
import { splitReviewLines, isEvidenceLine, pdfCitationUrl, quoteFragments, resolveReviewSourcePresentation, paginateReviewText, findingHeading, buildAnnotations, verifiedReferenceUnits } from '../src/features/agent/review-source'
import { findingDisplayTitle, groupReviewFindings, matchPriorityFinding, reviewFindingsWithDecisions } from '../src/features/agent/review-report'

test('source lines preserve the same one-based positions as the server, including blanks and BOM', () => {
  expect(splitReviewLines('\uFEFFПервое\r\n\r\nТретье\n')).toEqual(['Первое', '', 'Третье'])
  expect(splitReviewLines('Один\u2028Два\rТри')).toEqual(['Один', 'Два', 'Три'])
  expect(splitReviewLines('')).toEqual([])
  expect(isEvidenceLine(2, { start: 2, end: 4 })).toBe(true)
  expect(isEvidenceLine(4, { start: 2, end: 4 })).toBe(true)
  expect(isEvidenceLine(5, { start: 2, end: 4 })).toBe(false)
  expect(isEvidenceLine(2, undefined)).toBe(false)
  expect(pdfCitationUrl('blob:local-document', 4)).toBe('blob:local-document#page=4&view=FitH')
})

test('employee report starts with the original document and actionable findings, not model metadata', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report }))
  expect(markup).toContain('Исходный документ')
  expect(markup).toContain('Замечания')
  expect(markup).toContain('Что исправить')
  expect(markup).toContain('Показать в документе')
  expect(markup).toContain('Итог проверки')
  expect(markup).toContain('Риск двойного списания')
  expect(markup).toContain('Средний приоритет')
  expect(markup).toContain('data-severity="medium"')
  expect(markup).not.toContain('Drex')
  expect(markup).not.toContain('SHA-256')
  expect(markup).not.toContain('Паспорт прогона')
  expect(markup).not.toContain(report.passport.model)
})

test('the report keeps a dedicated issue queue when another report section opens', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report: { ...report, findings: [] } }))
  expect(markup).toContain('data-testid="review-finding-list"')
  expect(markup).toContain('data-testid="review-section-detail"')
  expect(markup).toContain('Исходный документ')
})

test('the report exposes clarification questions and readiness checklist for newly generated reviews', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const evidence = report.findings[0]!.evidence[0]!
  const rows = Object.keys(agentSpecReadinessLabels).map(id => id === 'testability' ? { id, availability: 'present', status: 'fail', comment: 'Не подтверждён срок.', evidence: [evidence], question: null } : { id, availability: 'uncertain', status: 'uncertain', comment: 'Требуется проверка.', evidence: [], question: null })
  const updated = agentSpecReviewSchema.parse({ ...report, clarification_questions: [{ id: 'Q-001', finding_id: 'D-001', checklist_id: null, question: 'Какой срок?', priority: 'high', requirement_ids: report.findings[0]!.requirement_ids, evidence: [evidence] }], readiness_checklist: rows, extraction_gaps: [evidence] })
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report: updated }))
  expect(markup).toContain('Уточняющие вопросы')
  expect(markup).toContain('Чек-лист')
  expect(markup).toContain('Полнота извлечения')
})

test('repeated findings share one review card and source marker without changing the raw report', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const indexed = reviewFindingsWithDecisions(report)
  const grouped = groupReviewFindings(indexed)
  expect(grouped.map(({ members }) => members.map(({ id }) => id))).toEqual([
    [indexed[0]!.id, indexed[2]!.id], [indexed[1]!.id, indexed[3]!.id],
    [indexed[4]!.id, indexed[6]!.id], [indexed[5]!.id, indexed[7]!.id],
  ])
  expect(report.findings).toHaveLength(8)
  const source = await Bun.file(new URL('../src/features/agent/examples/fintech-payment-spec.txt', import.meta.url)).text()
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report, sourceUnits: splitReviewLines(source) }))
  expect(markup.match(/data-testid="review-finding-row"/g)).toHaveLength(4)
  expect(markup).toContain('data-testid="review-finding-list"')
  expect(markup).toContain('data-testid="review-finding-detail"')
  expect(markup).toContain('data-narrow-view="list"')
  expect(markup).toContain('Все замечания')
  expect(markup.match(/data-testid="review-finding-detail"/g)).toHaveLength(1)
  expect(markup).toContain('Открыть документ на весь экран')
  expect(markup.match(/data-testid="review-citation"/g)).toHaveLength(1)
  expect(markup).toContain('data-single-page="true"')
  expect(markup).toContain('Риск двойного списания')
  expect(markup).toContain('Противоречие в лимитах')
  expect(markup).toContain('Не определён срок оплаты')
  expect(markup).toContain('Неясный результат платежа')
  expect(markup).not.toContain('Текст цитаты')
})

test('different issues at the same citation and findings with different evidence are not merged', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const first = reviewFindingsWithDecisions(report)[0]!
  const unrelated = { ...first, id: 'unrelated', finding: { ...first.finding, issue: 'Не указано, какой сервис обязан уведомить клиента о списании.', recommendation: 'Назначить сервис отправки уведомлений.' } }
  const otherSource = { ...first, id: 'other-source', finding: { ...first.finding, evidence: [{ ...first.finding.evidence[0]!, start: 8, end: 8 }] } }
  expect(groupReviewFindings([first, unrelated, otherSource])).toHaveLength(3)
})

test('a missing timeout recovery rule gets a meaningful title rather than a generic category', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const finding = { ...report.findings[0]!, kind: 'missing' as const, issue: 'Не установлены правила повтора при тайм-ауте и неизвестном исходе списания; повтор может дублировать операцию.' }
  expect(findingDisplayTitle(finding)).toBe('Риск двойного списания')
})

test('the example report cites the exact locally bundled source used by the document pane', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const text = await Bun.file(new URL('../src/features/agent/examples/fintech-payment-spec.txt', import.meta.url)).text()
  const units = splitReviewLines(text)
  const normalize = (value: string) => value.trim().replace(/\s+/g, ' ')
  const evidence = [...report.requirements.map((requirement) => requirement.evidence), ...report.findings.flatMap((finding) => finding.evidence)]
  for (const citation of evidence) expect(normalize(units.slice(citation.start - 1, citation.end).join(' '))).toContain(normalize(citation.quote))
})

test('quote highlights preserve exact original offsets across text runs and reject a different quote', () => {
  expect(quoteFragments(['Начало. После тайм-', 'аута повторить ', 'запрос.'], 'После тайм-аута повторить запрос')).toEqual([
    { index: 0, start: 8, end: 19 }, { index: 1, start: 0, end: 14 }, { index: 2, start: 0, end: 6 },
  ])
  expect(quoteFragments(['Сумма 100 000'], 'Сумма 150 000')).toEqual([])
})

test('text navigation preserves actual one-based lines and stable issue markers', async () => {
  const lines = Array.from({ length: 40 }, (_, i) => `Строка ${i + 1}`)
  const parts = paginateReviewText(lines)
  expect(parts.map(({ start, end }) => [start, end])).toEqual([[1, 32], [33, 40]])
  expect(parts.flatMap(({ lines }) => lines)).toEqual(lines)
  expect(findingHeading('Не указан срок оплаты. Нужно уточнить срок.')).toBe('Не указан срок оплаты')
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const markers = buildAnnotations(report.findings)
  expect(markers[0]?.number).toBe(1)
  expect(markers[0]?.evidence).toEqual(report.findings[0]?.evidence[0])
})

test('overview actions link to the exact finding, never an ambiguous or invented match', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const indexed = reviewFindingsWithDecisions(report)
  const first = indexed[0]!
  const priority = first.finding.recommendation
  expect(matchPriorityFinding(priority, indexed)).toEqual(first)
  expect(matchPriorityFinding('Неизвестная рекомендация', indexed)).toBeUndefined()
  expect(matchPriorityFinding(priority, [first, { ...first, id: 'another' }])).toBeUndefined()
})


test('archived reports render their saved source and recommendations without requiring the uploaded file', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const source = await Bun.file(new URL('../src/features/agent/examples/fintech-payment-spec.txt', import.meta.url)).text()
  const id = report.passport.run_id ?? '0196f6f8-6600-7000-8000-000000000002'
  const detail = agentCompletedReviewDetailSchema.parse({ id, report, source_units: splitReviewLines(source) })
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report: detail.report, sourceUnits: detail.source_units }))
  expect(markup).toContain('Исходный текст')
  expect(markup).toContain('Что исправить')
  expect(markup).not.toContain('Исходный файл недоступен для просмотра.')
  expect(markup).toContain(detail.source_units.find(unit => unit.trim())!)
})

test('a live Office report renders the extracted text rather than the binary source file', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const raw = await Bun.file(new URL('../src/features/agent/examples/fintech-payment-spec.txt', import.meta.url)).text()
  const file = new File([new Uint8Array([80, 75, 3, 4])], 'requirements.docx')
  const markup = renderToStaticMarkup(createElement(SpecReviewReport, { report: { ...report, source: 'docx', filename: file.name }, sourceFile: file, sourceUnits: splitReviewLines(raw) }))
  expect(markup).toContain('Извлечённый текст DOCX')
  expect(markup).toContain('После тайм-аута')
  expect(markup).toContain('Блок 5')
  expect(markup).not.toContain('Не удалось открыть извлечённый текст')
})


test('photo review shows extracted image text and warns about unreadable Word pictures', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  const photo = new File(['photo'], 'scan.png', { type: 'image/png' })
  const photoMarkup = renderToStaticMarkup(createElement(SpecReviewReport, { report: { ...report, source: 'png', filename: photo.name, ocr_units: [1] }, sourceFile: photo, sourceUnits: ['OCR: лимит 1000'] }))
  expect(photoMarkup).toContain('Извлечённый текст PNG')
  expect(photoMarkup).toContain('OCR: лимит 1000')
  expect(photoMarkup).toContain('Показать исходную фотографию')
  const wordMarkup = renderToStaticMarkup(createElement(SpecReviewReport, { report: { ...report, source: 'docx', ocr_units: [1], unreadable_units: [2] }, sourceUnits: ['OCR: лимит 1000', ''] }))
  expect(wordMarkup).toContain('Проверка документа неполная')
  expect(wordMarkup).toContain('Изображение не распознано')
})

test('the graph tab is available for multi-agent reports without changing archived legacy ones', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  expect(renderToStaticMarkup(createElement(SpecReviewReport, { report }))).not.toContain('Граф доказательств')
  const nodes = [
    ...report.requirements.map(item => ({ id: item.id, kind: 'requirement', requirement_id: item.id, title: item.text })),
    ...report.findings.map((item, index) => ({ id: `D-${String(index + 1).padStart(3, '0')}`, kind: 'finding', finding_id: `D-${String(index + 1).padStart(3, '0')}`, title: item.issue })),
    ...(report.clarification_questions ?? []).map(item => ({ id: item.id, kind: 'question', question_id: item.id, title: item.question })),
  ]
  const updated = agentSpecReviewSchema.parse({ ...report, passport: { ...report.passport, review_mode: 'multi_agent' }, evidence_graph: { schema_version: 1, nodes, edges: [], experts: [], finalization_status: 'completed', unresolved_count: 0 } })
  expect(renderToStaticMarkup(createElement(SpecReviewReport, { report: updated }))).toContain('Граф доказательств')
})

test('a graph citation on OCR uses extracted text and a manual display choice is valid only until the next citation jump', () => {
  const file = new File(['document'], 'scan.pdf')
  const photo = new File(['document'], 'scan.png')
  const citation = { start: 2, end: 2, quote: 'OCR quote' }
  const manualPdf = { file, jump: 1, extracted: false }
  expect(resolveReviewSourcePresentation('pdf', file, [2], citation, 1, manualPdf)).toBe(false)
  expect(resolveReviewSourcePresentation('pdf', file, [2], citation, 2, manualPdf)).toBe(true)
  expect(resolveReviewSourcePresentation('pdf', file, [2], { ...citation, start: 1, end: 1 }, 2, manualPdf)).toBe(false)
  const manualPhoto = { file: photo, jump: 1, extracted: false }
  expect(resolveReviewSourcePresentation('png', photo, [], citation, 1, manualPhoto)).toBe(false)
  expect(resolveReviewSourcePresentation('png', photo, [], citation, 2, manualPhoto)).toBe(true)
})


test('reference reader accepts only the full source snapshot that matches the source ID, digest and cited location', () => {
  const source = { id: 'S-001', sha256: 'a'.repeat(64), unreadable_pages: [], unreadable_units: [], source: 'txt' as const, filename: 'rules.txt',
    category: 'bank_policy' as const, title: 'Rules', issuer: 'Bank', version: '1', scope: 'Payments', source_url: null, applicability: 'relevant' as const, summary: 'Rules' }
  const snapshot = { source_id: source.id, sha256: source.sha256, source_units: ['Context before', 'Exact quote.', 'Context after'] }
  const evidence = { start: 2, end: 2, quote: 'Exact quote.' }
  expect(verifiedReferenceUnits(snapshot, source, evidence)).toEqual(snapshot.source_units)
  expect(verifiedReferenceUnits({ ...snapshot, source_id: 'S-002' }, source, evidence)).toBeUndefined()
  expect(verifiedReferenceUnits({ ...snapshot, sha256: 'b'.repeat(64) }, source, evidence)).toBeUndefined()
  expect(verifiedReferenceUnits({ ...snapshot, source_units: ['Exact quote.'] }, source, evidence)).toBeUndefined()
  expect(verifiedReferenceUnits(snapshot, source, { ...evidence, quote: 'Invented quote' })).toBeUndefined()
  expect(verifiedReferenceUnits(snapshot, { ...source, unreadable_units: [2] }, evidence)).toBeUndefined()
})
