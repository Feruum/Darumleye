import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { AgentReferenceMetadata, AgentReferenceReview } from '@web-app-demo/contracts'
import { buildSpecificationReviewFormData } from '../src/features/agent/api'
import { referencePresets, referenceSelectionError, type ReferenceUpload } from '../src/features/agent/reference-sources'
import { ReferenceReviewPanel } from '../src/features/agent/ReferenceReview'

const document = new File(['Требование'], 'spec.md')
const metadata: AgentReferenceMetadata = { category: 'bank_policy', title: 'Учебная политика', issuer: 'Тестовый владелец', version: '1.0', scope: 'Операции', source_url: null }
const reference: ReferenceUpload = { file: new File(['Правило'], 'policy.txt'), metadata }

test('reference upload is optional and serializes exact metadata in file order', () => {
  expect([...buildSpecificationReviewFormData(document).keys()]).toEqual(['file', 'review_mode'])
  const another = { ...reference, file: new File(['Норма'], 'rules.md'), metadata: { ...metadata, category: 'payments_data' as const } }
  const form = buildSpecificationReviewFormData(document, [reference, another])
  expect(form.getAll('reference_files').map((file) => (file as File).name)).toEqual(['policy.txt', 'rules.md'])
  expect(JSON.parse(String(form.get('reference_manifest')))).toEqual([reference.metadata, another.metadata])
})

test('reference validation rejects empty metadata, bad files, excess count/bytes and unsafe URL', () => {
  expect(referenceSelectionError([])).toBeUndefined()
  expect(referenceSelectionError([reference])).toBeUndefined()
  expect(referenceSelectionError([{ ...reference, metadata: { ...metadata, category: '' } }])).toBeDefined()
  expect(referenceSelectionError([{ ...reference, metadata: { ...metadata, version: ' ' } }])).toBeDefined()
  expect(referenceSelectionError([{ ...reference, metadata: { ...metadata, source_url: 'javascript:alert(1)' } }])).toBeDefined()
  expect(referenceSelectionError([{ ...reference, file: new File(['x'], 'policy.exe') }])).toBeDefined()
  expect(referenceSelectionError(Array.from({ length: 9 }, () => reference))).toBeDefined()
  const file = new File([new Uint8Array(5 * 1024 * 1024)], 'policy.md')
  expect(referenceSelectionError(Array.from({ length: 3 }, () => ({ ...reference, file })))).toBeDefined()
  expect(() => buildSpecificationReviewFormData(document, [{ ...reference, metadata: { ...metadata, title: '' } }])).toThrow()
})

test('all source categories have metadata presets, not built-in reference texts', () => {
  expect(new Set(referencePresets.map(({ metadata }) => metadata.category))).toEqual(new Set(['quality_standard', 'bank_policy', 'api_testing', 'payments_data', 'security_regulation']))
  expect(referencePresets.filter(({ id }) => ['bank-policy', 'api-testing'].includes(id)).every(({ metadata }) => metadata.issuer === '' && metadata.source_url === null)).toBe(true)
})

const completed: AgentReferenceReview = {
  status: 'completed', explanation: 'Сопоставление предоставленных документов.',
  sources: [{ id: 'S-001', ...metadata, filename: 'policy.pdf', source: 'pdf', sha256: 'a'.repeat(64), unreadable_pages: [3], applicability: 'relevant', summary: 'Относится к операциям.' }],
  observations: [{ id: 'RF-001', source_id: 'S-001', status: 'potential_discrepancy', issue: 'Проверить срок.', recommendation: 'Согласовать срок.', requirement_ids: ['R-001'], specification_evidence: [{ start: 5, end: 6, quote: 'Цитата из ТЗ' }], reference_evidence: [{ start: 2, end: 2, quote: 'Пункт политики' }] }],
}

test('employee reference report identifies both evidence locations and source by name', () => {
  const markup = renderToStaticMarkup(createElement(ReferenceReviewPanel, { review: completed, specificationSource: 'md' }))
  expect(markup).toContain('Учебная политика')
  expect(markup).not.toContain('S-001')
  expect(markup).not.toContain('RF-001')
  expect(markup).toContain('Цитата из ТЗ')
  expect(markup).toContain('Строки 5–6')
  expect(markup).toContain('Пункт политики')
  expect(markup).toContain('Страница 2')
  expect(markup).toContain('Не прочитаны страницы: 3')
  const unavailable = renderToStaticMarkup(createElement(ReferenceReviewPanel, { review: { ...completed, status: 'unavailable', observations: [], sources: [{ ...completed.sources[0], applicability: 'not_reviewed' }] }, specificationSource: 'md' }))
  expect(unavailable).toContain('data-status="unavailable"')
  expect(unavailable).not.toContain('RF-001')
})

test('reference report discloses partial coverage without technical model or retrieval metadata', () => {
  const review: AgentReferenceReview = {
    ...completed,
    retrieval: { mode: 'chroma_hybrid_local_features', sources: [{ source_id: 'S-001', total_chunks: 8, selected_chunks: 2, total_characters: 3000, selected_characters: 750, partial: true }] },
    decision_review: {
      provider: 'nace', status: 'completed', model: 'fixture-model', explanation: 'Отдельное мнение второй модели.',
      findings: [{ id: 'RF-001', probability: 0.4, status: 'uncertain' }],
    },
  }
  const markup = renderToStaticMarkup(createElement(ReferenceReviewPanel, { review, specificationSource: 'md' }))
  expect(markup).toContain('Проверена часть материалов')
  expect(markup).not.toContain('ChromaDB')
  expect(markup).not.toContain('fixture-model')
  expect(markup).not.toContain('Drex')
  expect(markup).not.toContain('SHA-256')
  const empty = renderToStaticMarkup(createElement(ReferenceReviewPanel, { review: { ...review, observations: [], decision_review: { ...review.decision_review!, status: 'not_applicable', findings: [] } }, specificationSource: 'md' }))
  expect(empty).toContain('В проверенных фрагментах материалов')
  expect(empty).not.toContain('В предоставленных документах')
})


test('multipart always sends the chosen review strategy, including explicit legacy', () => {
  const file = new File(['Requirement'], 'spec.md')
  expect(buildSpecificationReviewFormData(file, [], 'multi_agent').get('review_mode')).toBe('multi_agent')
  expect(buildSpecificationReviewFormData(file).get('review_mode')).toBe('multi_agent')
  expect(buildSpecificationReviewFormData(file, [], 'multi_agent_direct').get('review_mode')).toBe('multi_agent_direct')
  expect(buildSpecificationReviewFormData(file, [], 'legacy').get('review_mode')).toBe('multi_agent_direct')
})
