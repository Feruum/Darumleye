import { expect, test } from 'bun:test'
import { AGENT_SPEC_REVIEW_MAX_BYTES, type AgentSpecDecisionReview, type AgentSpecReview } from '@web-app-demo/contracts'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { isSupportedDocument, isSupportedSpecification } from '../src/features/agent/api'
import { formatEvidenceLocation, requirementNeedsRevision, reviewDownloadFilename, reviewFindingsWithDecisions } from '../src/features/agent/review-report'
import { DecisionReviewSummary, FindingDecisionReviewBadge } from '../src/features/agent/DecisionReview'

test('Markdown and office formats are accepted for review without extending RAG ingestion', () => {
  const file = { name: 'requirements.MD', size: 200 }
  expect(isSupportedSpecification(file)).toBe(true)
  expect(isSupportedDocument(file)).toBe(false)
  expect(isSupportedSpecification({ ...file, size: 0 })).toBe(false)
  expect(isSupportedSpecification({ ...file, size: AGENT_SPEC_REVIEW_MAX_BYTES + 1 })).toBe(false)
  expect(isSupportedSpecification({ name: 'requirements.exe', size: 20 })).toBe(false)
  for (const extension of ['docx', 'xlsx', 'csv', 'jpg', 'jpeg', 'png']) {
    expect(isSupportedSpecification({ name: `requirements.${extension}`, size: 25 * 1024 * 1024 })).toBe(true)
    expect(isSupportedDocument({ name: `requirements.${extension}`, size: 10 })).toBe(false)
  }
})

test('uncertain and failed checks require review, not applicable checks do not', () => {
  const check = { criterion: 'Проверяемость', comment: 'Причина' }
  expect(requirementNeedsRevision([{ ...check, status: 'fail' }])).toBe(true)
  expect(requirementNeedsRevision([{ ...check, status: 'uncertain' }])).toBe(true)
  expect(requirementNeedsRevision([{ ...check, status: 'not_applicable' }])).toBe(false)
  expect(requirementNeedsRevision([{ ...check, status: 'pass' }])).toBe(false)
})

test('download uses a safe Markdown filename', () => {
  expect(reviewDownloadFilename('requirements.md')).toBe('requirements-review.md')
  expect(reviewDownloadFilename('folder/ТЗ.pdf')).toBe('ТЗ-review.md')
  expect(reviewDownloadFilename('..')).toBe('specification-review.md')
})

test('evidence locations retain the entire cited source range', () => {
  expect(formatEvidenceLocation({ start: 2, end: 3 }, 'pdf')).toBe('Страницы 2–3')
  expect(formatEvidenceLocation({ start: 12, end: 15 }, 'md')).toBe('Строки 12–15')
  expect(formatEvidenceLocation({ start: 4, end: 4 }, 'txt')).toBe('Строка 4')
  expect(formatEvidenceLocation({ start: 1, end: 1 }, 'png')).toBe('Изображение 1')
})

const audit: AgentSpecDecisionReview = {
  provider: 'nace', status: 'completed', model: 'drex-max-finance', explanation: 'Независимая оценка обоснованности.',
  findings: [
    { id: 'D-002', probability: 0.31, status: 'unsupported' },
    { id: 'D-001', probability: 0.8, status: 'supported' },
    { id: 'D-003', probability: 0.79, status: 'uncertain' },
  ],
}

test('attaches decisions by original finding ID, even for identical findings and unordered decisions', () => {
  const finding: AgentSpecReview['findings'][number] = { kind: 'other', severity: 'high', issue: 'Недостаточно критериев.', recommendation: 'Уточнить критерии.', evidence: [], requirement_ids: ['R-001'] }
  const findings = [finding, finding, finding]
  expect(reviewFindingsWithDecisions({ findings, decision_review: audit }).map(({ id, assessment }) => [id, assessment?.status])).toEqual([
    ['D-001', 'supported'], ['D-002', 'unsupported'], ['D-003', 'uncertain'],
  ])
  expect(reviewFindingsWithDecisions({ findings, decision_review: null }).every(({ assessment }) => assessment === undefined)).toBe(true)
})

test('independent review summary is absent when disabled and differentiates complete and unavailable results', () => {
  expect(renderToStaticMarkup(createElement(DecisionReviewSummary, { audit: null }))).toBe('')
  const complete = renderToStaticMarkup(createElement(DecisionReviewSummary, { audit }))
  expect(complete).toContain('drex-max-finance')
  expect(complete).toContain('data-status="completed"')
  expect(complete).toContain('data-tone="pass"')
  expect(complete).toContain('data-tone="uncertain"')
  expect(complete).toContain('data-tone="fail"')
  const unavailable = renderToStaticMarkup(createElement(DecisionReviewSummary, { audit: { ...audit, status: 'unavailable', findings: [] } }))
  expect(unavailable).toContain('data-status="unavailable"')
  expect(unavailable).not.toContain('data-tone="pass"')
  const notApplicable = renderToStaticMarkup(createElement(DecisionReviewSummary, { audit: { ...audit, status: 'not_applicable', findings: [] } }))
  expect(notApplicable).toContain('data-status="not_applicable"')
  expect(notApplicable).not.toContain('data-tone="fail"')
})

test('finding badge displays the exact model assessment without removing unsupported findings', () => {
  const markup = renderToStaticMarkup(createElement(FindingDecisionReviewBadge, { assessment: audit.findings[0] }))
  expect(markup).toContain('D-002')
  expect(markup).toContain('31%')
  expect(markup).toContain('0,31')
  expect(markup).toContain('data-tone="fail"')
  expect(renderToStaticMarkup(createElement(FindingDecisionReviewBadge, { assessment: undefined }))).toBe('')
})
