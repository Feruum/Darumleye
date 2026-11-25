import { describe, expect, test } from 'bun:test'

import { agentCompletedReviewDetailSchema, agentChatRequestSchema, agentDocumentSchema, agentDocumentsResponseSchema, agentMemoryInputSchema, agentMemorySchema, agentReferenceManifestSchema, agentReviewFixInputSchema, agentReviewFixSchema, agentReviewJobSchema, agentSpecReadinessLabels, agentSpecReviewSchema, type AgentReferenceReview, type AgentSpecDecisionReview, type AgentSpecReview } from './agents'

test('correction proposals preserve exact source whitespace and require a patch or a question', () => {
  const patch = { status: 'patch' as const, explanation: 'Удалить дубликат', question: null, edits: [{ unit: 2, before: '  Duplicate. ', after: '' }] }
  expect(agentReviewFixSchema.parse(patch)).toEqual(patch)
  expect(agentReviewFixSchema.safeParse({ ...patch, question: 'Unknown rule?' }).success).toBe(false)
  expect(agentReviewFixSchema.safeParse({ ...patch, edits: [] }).success).toBe(false)
  expect(agentReviewFixSchema.safeParse({ status: 'clarification', explanation: 'Unknown rule', question: 'What is the desired value?', edits: [] }).success).toBe(true)
  expect(agentReviewFixInputSchema.safeParse({ finding_id: 'D-001', clarification: '', owner_id: 'other' }).success).toBe(false)
})

test('review job status has an exclusive completed report or failure payload', () => {
  const job = { id: '0196f6f8-6600-7000-8000-000000000001', filename: 'requirements.md', started_at: '2026-11-25T12:00:00+00:00' }
  expect(agentReviewJobSchema.safeParse({ ...job, status: 'running' }).success).toBe(true)
  expect(agentReviewJobSchema.safeParse({ ...job, status: 'completed', review_id: job.id }).success).toBe(true)
  expect(agentReviewJobSchema.safeParse({ ...job, status: 'failed', error: { code: 'REVIEW_FAILED', message: 'Ошибка проверки' } }).success).toBe(true)
  expect(agentReviewJobSchema.safeParse({ ...job, status: 'completed' }).success).toBe(false)
  expect(agentReviewJobSchema.safeParse({ ...job, status: 'running', error: { code: 'REVIEW_FAILED', message: 'Ошибка' } }).success).toBe(false)
})

describe('agent chat contract', () => {
  test('review chat requires exactly one selected completed review ID', () => {
    const messages = [{ type: 'human', content: 'Что исправить?' }]
    const review_id = '0196f6f8-6600-7000-8000-000000000001'
    expect(agentChatRequestSchema.parse({ messages, module_id: 'review_chat', review_id }).review_id).toBe(review_id)
    expect(agentChatRequestSchema.safeParse({ messages, module_id: 'review_chat' }).success).toBe(false)
    expect(agentChatRequestSchema.safeParse({ messages, module_id: 'rag', review_id }).success).toBe(false)
  })
  test('allows the backend to create a thread when the client omits thread_id', () => {
    expect(agentChatRequestSchema.parse({
      messages: [{ type: 'human', content: 'Answer from my files' }],
      module_id: 'rag',
    })).toEqual({
      messages: [{ type: 'human', content: 'Answer from my files' }],
      module_id: 'rag',
    })
  })

  test('accepts a caller-selected module and thread', () => {
    expect(agentChatRequestSchema.parse({
      messages: [{ type: 'human', content: 'Hello' }],
      thread_id: 'thread-1',
      module_id: 'rag',
    })).toMatchObject({ thread_id: 'thread-1', module_id: 'rag' })
  })

  test('rejects invalid module identifiers and thread identifiers', () => {
    expect(agentChatRequestSchema.safeParse({
      messages: [{ type: 'human', content: 'Hello' }],
      module_id: '../rag',
    }).success).toBe(false)
    expect(agentChatRequestSchema.safeParse({
      messages: [{ type: 'human', content: 'Hello' }],
      thread_id: '',
    }).success).toBe(false)
  })
})

describe('explicit agent memory contract', () => {
  test('trims note content and rejects blank, oversized, or forged-owner writes', () => {
    expect(agentMemoryInputSchema.parse({ content: '  Prefer Russian.  ' })).toEqual({ content: 'Prefer Russian.' })
    for (const input of [{ content: ' ' }, { content: 'x'.repeat(2001) }, { content: 'private', user_id: 'someone-else' }]) {
      expect(agentMemoryInputSchema.safeParse(input).success).toBe(false)
    }
  })

  test('accepts the Python response timestamp and never exposes an owner field', () => {
    const note = { id: '0196f6f8-6600-7000-8000-000000000001', content: 'Prefer Russian.', created_at: '2026-10-16T10:00:00+00:00' }
    expect(agentMemorySchema.parse(note)).toEqual(note)
    expect(agentMemorySchema.safeParse({ ...note, user_id: 'private-owner' }).success).toBe(false)
  })
})

describe('agent document timestamps', () => {
  const document = {
    id: '0196f6f8-6600-7000-8000-000000000001', job_id: '0196f6f8-6600-7000-8000-000000000002',
    filename: 'notes.txt', status: 'COMPLETED' as const, content_hash: 'test', progress: 100,
    created_at: '2026-11-25T12:00:00.123456+00:00', updated_at: '2026-11-25T17:00:01+05:00',
    started_at: '2026-11-25T12:00:00+00:00', completed_at: '2026-11-25T12:00:01Z',
    error_code: null, error_message: null,
  }

  test('accepts timezone-aware Python timestamps for upload and document listing', () => {
    expect(agentDocumentSchema.parse(document)).toEqual(document)
    expect(agentDocumentsResponseSchema.parse({ documents: [document] })).toEqual({ documents: [document] })
  })

  test('rejects missing timezones and invalid timestamps', () => {
    for (const created_at of ['2026-11-25T12:00:00', 'not a timestamp']) {
      expect(agentDocumentSchema.safeParse({ ...document, created_at }).success).toBe(false)
    }
  })
})

describe('specification review contract', () => {
  const evidence = { start: 4, end: 4, quote: 'Клиент получает уведомление.' }
  const finding: AgentSpecReview['findings'][number] = {
    kind: 'other', severity: 'critical', issue: 'Не определён срок уведомления.',
    recommendation: 'Указать срок.', evidence: [evidence], requirement_ids: ['REQ-001'],
  }
  const review: AgentSpecReview = {
    filename: 'requirements.md', source: 'md', unreadable_pages: [],
    summary: 'Требования нуждаются в уточнении.', findings: Array.from({ length: 11 }, () => finding),
    passport: { document_title: 'Уведомления', run_at: '2026-11-25T10:00:00+00:00', model: 'fixture', agent_version: 'test-commit' },
    requirements: [{ id: 'REQ-001', source_id: null, text: evidence.quote, evidence, checks: [{ criterion: 'verifiable', status: 'fail', comment: 'Не определён срок.' }] }],
    set_checks: [{ criterion: 'consistency', status: 'uncertain', comment: 'Нужно уточнение.', evidence: [evidence] }],
    statistics: { requirements: 1, violations: 11, critical: 11, passed: 0, needs_revision: 1 },
    verdict: { status: 'not_ready', reason: 'Критичные замечания.', priorities: ['Уточнить срок.'] },
    report_markdown: '# Отчёт\nНайдено 11 замечаний.',
  }

  test('accepts an exhaustive Markdown review and traceable findings beyond ten', () => {
    expect(agentSpecReviewSchema.parse(review)).toEqual(review)
  })

  test('multi-agent graph preserves provenance and rejects dangling or duplicate graph identities', () => {
    const graph: NonNullable<AgentSpecReview['evidence_graph']> = {
      schema_version: 1,
      nodes: [
        { id: 'REQ-001', kind: 'requirement', title: 'Требование', requirement_id: 'REQ-001' },
        { id: 'E-001', kind: 'evidence', title: 'Цитата', evidence, requirement_id: null },
        { id: 'O-001', kind: 'observation', title: 'Вывод', role: 'logic_inspector', severity: 'critical' },
        ...review.findings.map((item, i) => ({ id: `D-${String(i + 1).padStart(3, '0')}`, kind: 'finding' as const, title: item.issue, finding_id: `D-${String(i + 1).padStart(3, '0')}` })),
      ],
      edges: [{ source: 'O-001', target: 'E-001', kind: 'cites' }, { source: 'O-001', target: 'D-001', kind: 'contributes_to' }],
      experts: [{ role: 'logic_inspector', status: 'completed', observations: 1 }],
      finalization_status: 'completed', unresolved_count: 0,
    }
    const updated: AgentSpecReview = { ...review, passport: { ...review.passport, review_mode: 'multi_agent' }, evidence_graph: graph }
    expect(agentSpecReviewSchema.parse(updated)).toEqual(updated)
    const detail = { id: '0196f6f8-6600-7000-8000-000000000001', report: updated, source_units: ['', '', '', evidence.quote] }
    expect(agentCompletedReviewDetailSchema.safeParse(detail).success).toBe(true)
    expect(agentCompletedReviewDetailSchema.safeParse({ ...detail, source_units: ['', '', '', 'wrong source'] }).success).toBe(false)
    expect(agentCompletedReviewDetailSchema.safeParse({ ...detail, source_units: [evidence.quote] }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: null }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, nodes: graph.nodes.filter(node => node.id !== 'D-011') } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, nodes: graph.nodes.filter(node => node.id !== 'REQ-001').map(node => node.kind === 'observation' ? { ...node, requirement_id: 'REQ-001' } : node) } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, nodes: graph.nodes.map(node => node.kind === 'evidence' ? { ...node, source_id: 'primary' } : node) } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, edges: [...graph.edges, { source: 'O-001', target: 'missing', kind: 'cites' }] } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, nodes: [...graph.nodes, graph.nodes[0]] } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, evidence_graph: { ...graph, nodes: [...graph.nodes, { id: 'foreign', kind: 'finding', title: 'Unknown', finding_id: 'D-999' }] } }).success).toBe(false)
  })

  test('accepts evidence-linked questions and checklist while rejecting unsupported or foreign links', () => {
    const question: NonNullable<AgentSpecReview['clarification_questions']>[number] = { id: 'Q-001', finding_id: 'D-001', checklist_id: null, question: 'В какой срок?', priority: 'critical', requirement_ids: ['REQ-001'], evidence: [evidence] }
    const row: NonNullable<AgentSpecReview['readiness_checklist']>[number] = { id: 'testability', availability: 'present', status: 'fail', comment: 'Нужно уточнить срок.', evidence: [evidence], question: 'Как измерить срок?' }
    const rows: NonNullable<AgentSpecReview['readiness_checklist']> = (Object.keys(agentSpecReadinessLabels) as (keyof typeof agentSpecReadinessLabels)[]).map(id => id === row.id ? row : { id, availability: 'uncertain', status: 'uncertain', comment: 'Требуется проверка.', evidence: [], question: null })
    const updated: AgentSpecReview = { ...review, clarification_questions: [question], readiness_checklist: rows, extraction_gaps: [evidence] }
    expect(agentSpecReviewSchema.parse(updated)).toEqual(updated)
    expect(agentSpecReviewSchema.safeParse({ ...updated, clarification_questions: [{ ...question, finding_id: 'D-999' }] }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, readiness_checklist: rows.map(item => item.id === row.id ? { ...row, status: 'pass', evidence: [] } : item) }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, readiness_checklist: [row, ...rows.slice(0, -1)] }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...updated, readiness_checklist: [row] }).success).toBe(false)
  })

  test('accepts Office, CSV and up to 150 PDF pages with optional OCR metadata', () => {
    for (const source of ['docx', 'xlsx', 'csv', 'jpg', 'jpeg', 'png'] as const) {
      expect(agentSpecReviewSchema.parse({ ...review, source }).source).toBe(source)
    }
    const photo = { ...review, source: 'docx', ocr_units: [2], unreadable_units: [4], verdict: { ...review.verdict, status: 'not_ready' } }
    expect(agentSpecReviewSchema.parse(photo).ocr_units).toEqual([2])
    expect(agentSpecReviewSchema.parse(photo).unreadable_units).toEqual([4])
    expect(agentSpecReviewSchema.safeParse({ ...photo, verdict: { ...photo.verdict, status: 'ready' } }).success).toBe(false)
    const scanned = { ...review, source: 'pdf', unreadable_pages: [150], ocr_pages: [1, 149] }
    expect(agentSpecReviewSchema.parse(scanned).ocr_pages).toEqual([1, 149])
    expect(agentSpecReviewSchema.safeParse({ ...review, source: 'docm' }).success).toBe(false)
  })

  test('requires passport, requirement checks, set checks, statistics, verdict and Markdown export', () => {
    for (const key of ['passport', 'requirements', 'set_checks', 'statistics', 'verdict', 'report_markdown'] as const) {
      expect(agentSpecReviewSchema.safeParse({ ...review, [key]: undefined }).success).toBe(false)
    }
  })

  test('rejects invalid evidence, status, and statistics', () => {
    expect(agentSpecReviewSchema.safeParse({ ...review, findings: [{ ...finding, evidence: [{ ...evidence, end: 3 }] }] }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...review, requirements: [{ ...review.requirements[0], checks: [{ criterion: 'verifiable', status: 'unknown', comment: 'Check' }] }] }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...review, statistics: { ...review.statistics, critical: -1 } }).success).toBe(false)
  })

  const assessed: AgentSpecDecisionReview = {
    provider: 'nace', status: 'completed', model: 'drex-max-finance', explanation: 'Независимая оценка обоснованности.',
    findings: Array.from({ length: 11 }, (_, index) => ({
      id: `D-${String(index + 1).padStart(3, '0')}`,
      probability: index === 0 ? 0.4 : index === 10 ? 0.3999 : 0.8,
      status: index === 0 ? 'uncertain' : index === 10 ? 'unsupported' : 'supported',
    })),
  }

  test('allows a disabled or complete independent check without changing the original report', () => {
    expect(agentSpecReviewSchema.parse({ ...review, decision_review: null })).toEqual({ ...review, decision_review: null })
    expect(agentSpecReviewSchema.parse({ ...review, decision_review: assessed })).toEqual({ ...review, decision_review: assessed })
    const unordered = { ...assessed, findings: [...assessed.findings].reverse() }
    expect(agentSpecReviewSchema.parse({ ...review, decision_review: unordered })).toEqual({ ...review, decision_review: unordered })
  })

  test('rejects an incomplete, duplicate, foreign, or inconsistent independent check', () => {
    const invalidFindings = [
      assessed.findings.slice(1),
      [...assessed.findings.slice(1), assessed.findings[1]],
      [...assessed.findings.slice(1), { ...assessed.findings[0], id: 'D-012' }],
      [{ ...assessed.findings[0], probability: 0.8 }, ...assessed.findings.slice(1)],
      [{ ...assessed.findings[0], probability: -0.1 }, ...assessed.findings.slice(1)],
      [{ ...assessed.findings[0], probability: true }, ...assessed.findings.slice(1)],
    ]
    for (const findings of invalidFindings) {
      expect(agentSpecReviewSchema.safeParse({ ...review, decision_review: { ...assessed, findings } }).success).toBe(false)
    }
  })

  test('reports an unavailable or inapplicable independent check without invented scores', () => {
    const unavailable = { ...assessed, status: 'unavailable', findings: [] }
    const inapplicable = { ...assessed, status: 'not_applicable', findings: [] }
    expect(agentSpecReviewSchema.safeParse({ ...review, decision_review: unavailable }).success).toBe(true)
    expect(agentSpecReviewSchema.safeParse({ ...review, findings: [], decision_review: inapplicable }).success).toBe(true)
    expect(agentSpecReviewSchema.safeParse({ ...review, decision_review: inapplicable }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...review, decision_review: { ...assessed, status: 'unavailable' } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...review, decision_review: { ...unavailable, explanation: ' ' } }).success).toBe(false)
  })

  const metadata = {
    category: 'bank_policy' as const, title: 'Политика уведомлений', issuer: 'Владелец правил', version: '1.0',
    scope: 'Уведомления о расходах', source_url: 'https://example.com/policy',
  }
  const referenceReview: AgentReferenceReview = {
    status: 'completed', explanation: 'Отдельная проверка по указанному источнику.',
    sources: [{
      ...metadata, id: 'S-001', filename: 'policy.pdf', source: 'pdf', sha256: 'a'.repeat(64),
      unreadable_pages: [], applicability: 'relevant', summary: 'Источник касается уведомлений.',
    }],
    observations: [{
      id: 'RF-001', source_id: 'S-001', status: 'potential_discrepancy', issue: 'Срок неизвестен.',
      recommendation: 'Сверить срок.', requirement_ids: ['REQ-001'], specification_evidence: [evidence],
      reference_evidence: [{ start: 8, end: 8, quote: 'Уведомление отправляется в срок.' }],
    }],
  }

  test('normalizes reference metadata and rejects invalid categories, URLs, lengths, and extra fields', () => {
    expect(agentReferenceManifestSchema.parse([{ ...metadata, title: ' Политика уведомлений ' }])).toEqual([metadata])
    for (const invalid of [
      { ...metadata, category: 'unsupported' },
      { ...metadata, source_url: 'file:///tmp/policy.txt' },
      { ...metadata, source_url: 'javascript:alert(1)' },
      { ...metadata, source_url: 'https://user:password@example.com/policy' },
      { ...metadata, source_url: 'https://example.com/with space' },
      { ...metadata, version: '' },
      { ...metadata, scope: 'x'.repeat(501) },
      { ...metadata, user_id: 'forged' },
    ]) expect(agentReferenceManifestSchema.safeParse([invalid]).success).toBe(false)
    expect(agentReferenceManifestSchema.safeParse(Array.from({ length: 9 }, () => metadata)).success).toBe(false)
  })

  test('accepts a separate reference review without modifying the primary assessment', () => {
    expect(agentSpecReviewSchema.parse({ ...review, reference_review: null })).toEqual({ ...review, reference_review: null })
    expect(agentSpecReviewSchema.parse({ ...review, reference_review: referenceReview })).toEqual({ ...review, reference_review: referenceReview })
  })

  test('accepts traceable run registration without requiring it on older reports', () => {
    const passport = { ...review.passport, run_id: '0196f6f8-6600-7000-8000-000000000001', document_sha256: 'a'.repeat(64) }
    expect(agentSpecReviewSchema.parse({ ...review, passport })).toEqual({ ...review, passport })
    expect(agentSpecReviewSchema.safeParse({ ...review, passport: { ...passport, run_id: 'invalid' } }).success).toBe(false)
    expect(agentSpecReviewSchema.safeParse({ ...review, passport: { ...passport, document_sha256: 'unhashed' } }).success).toBe(false)
  })

  const referenceRetrieval = {
    mode: 'chroma_hybrid_local_features',
    sources: [{ source_id: 'S-001', total_chunks: 5, selected_chunks: 2, total_characters: 300, selected_characters: 120, partial: true }],
  }
  const referenceAudit: NonNullable<AgentReferenceReview['decision_review']> = {
    provider: 'nace', model: 'drex-model', status: 'completed', explanation: 'Оценка модели, не юридическое заключение.',
    findings: [{ id: 'RF-001', probability: 0.8, status: 'supported' }],
  }

  test('reports partial retrieval coverage and separate RF-* assessments', () => {
    const reference_review = { ...referenceReview, retrieval: referenceRetrieval, decision_review: referenceAudit }
    expect(agentSpecReviewSchema.parse({ ...review, reference_review })).toEqual({ ...review, reference_review })
    const nullable = { ...referenceReview, retrieval: null, decision_review: null }
    expect(agentSpecReviewSchema.parse({ ...review, reference_review: nullable })).toEqual({ ...review, reference_review: nullable })
  })

  test('rejects inconsistent or missing per-source retrieval coverage', () => {
    const coverage = referenceRetrieval.sources[0]!
    for (const sources of [
      [], [coverage, coverage], [{ ...coverage, source_id: 'S-002' }], [{ ...coverage, total_chunks: -1 }],
      [{ ...coverage, selected_chunks: coverage.total_chunks + 1 }], [{ ...coverage, selected_characters: coverage.total_characters + 1 }],
      [{ ...coverage, partial: false }],
    ]) {
      const reference_review = { ...referenceReview, retrieval: { ...referenceRetrieval, sources } }
      expect(agentSpecReviewSchema.safeParse({ ...review, reference_review }).success).toBe(false)
    }
  })

  test('requires the separate audit to assess every observation exactly once', () => {
    const decision = referenceAudit.findings[0]!
    for (const decision_review of [
      { ...referenceAudit, findings: [] },
      { ...referenceAudit, findings: [decision, decision] },
      { ...referenceAudit, findings: [{ ...decision, id: 'D-001' }] },
      { ...referenceAudit, findings: [{ ...decision, id: 'RF-002' }] },
      { ...referenceAudit, findings: [{ ...decision, probability: 0.79 }] },
      { ...referenceAudit, status: 'unavailable' },
      { ...referenceAudit, findings: [], status: 'not_applicable' },
    ]) {
      expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: { ...referenceReview, decision_review } }).success).toBe(false)
    }
    const unavailable = { ...referenceAudit, findings: [], status: 'unavailable' }
    expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: { ...referenceReview, decision_review: unavailable } }).success).toBe(true)
    const empty = { ...referenceReview, observations: [], decision_review: { ...referenceAudit, findings: [], status: 'not_applicable' } }
    expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: empty }).success).toBe(true)
    expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: { ...empty, decision_review: referenceAudit } }).success).toBe(false)
  })

  test('requires source and spec citations, valid identifiers and consistent applicability', () => {
    const source = referenceReview.sources[0]!
    const observation = referenceReview.observations[0]!
    for (const invalid of [
      { ...referenceReview, sources: [] },
      { ...referenceReview, sources: [{ ...source, id: 'S-005' }] },
      { ...referenceReview, sources: [{ ...source, sha256: 'not-hash' }] },
      { ...referenceReview, sources: [{ ...source, applicability: 'not_reviewed' }] },
      { ...referenceReview, sources: [{ ...source, applicability: 'not_applicable' }] },
      { ...referenceReview, sources: [{ ...source, applicability: 'unclear' }] },
      { ...referenceReview, observations: [{ ...observation, source_id: 'S-004' }] },
      { ...referenceReview, observations: [{ ...observation, id: 'RF-002' }] },
      { ...referenceReview, observations: [{ ...observation, requirement_ids: ['unknown'] }] },
      { ...referenceReview, observations: [{ ...observation, reference_evidence: [] }] },
      { ...referenceReview, observations: [{ ...observation, specification_evidence: [] }] },
      { ...referenceReview, status: 'unavailable' },
    ]) expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: invalid }).success).toBe(false)
    const unavailable = { ...referenceReview, status: 'unavailable', sources: [{ ...source, applicability: 'not_reviewed' }], observations: [] }
    expect(agentSpecReviewSchema.safeParse({ ...review, reference_review: unavailable }).success).toBe(true)
  })
})
