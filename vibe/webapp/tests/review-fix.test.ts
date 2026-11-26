import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { AgentSpecReview } from '@web-app-demo/contracts'
import { applyReviewEdits, canFixReview, reviewFixErrorMessage, revisedSpecificationFilename } from '../src/features/agent/review-fix'
import { ReviewFixFindingList } from '../src/features/agent/ReviewFixWorkspace'
import { ApiRequestError } from '../src/platform/api'
import { applyDateRuleEdit, checkDateConflict } from '../src/features/agent/date-conflict-demo'

test('rechecks the exact date-example edit for both business decisions', () => {
  expect(checkDateConflict()).toEqual({ ruleMonth: 'Январь', exampleMonth: 'Февраль', hasConflict: true })
  expect(checkDateConflict(applyDateRuleEdit('purchase'))).toEqual({ ruleMonth: 'Январь', exampleMonth: 'Январь', hasConflict: false })
  const revised = applyDateRuleEdit('posting')
  expect(checkDateConflict(revised)).toEqual({ ruleMonth: 'Февраль', exampleMonth: 'Февраль', hasConflict: false })
  expect(checkDateConflict(revised.replace('Февральский счётчик вырос', 'Январский счётчик вырос')).hasConflict).toBe(true)
})

test('applies approved changes against the original, without changing its source units', () => {
  const units = ['# Requirements', 'The search is fast; the table is readable.', 'Keep this.']
  const result = applyReviewEdits(units, [
    { unit: 2, before: 'fast', after: 'available in 2 seconds' },
    { unit: 2, before: 'readable', after: 'shown in rows' },
  ])
  expect(result).toBe('# Requirements\nThe search is available in 2 seconds; the table is shown in rows.\nKeep this.')
  expect(units[1]).toBe('The search is fast; the table is readable.')
})

test('does not apply ambiguous, overlapping or missing original anchors', () => {
  expect(() => applyReviewEdits(['fast fast'], [{ unit: 1, before: 'fast', after: 'measured' }])).toThrow()
  expect(() => applyReviewEdits(['fast search'], [
    { unit: 1, before: 'fast', after: 'measured' },
    { unit: 1, before: 'fast search', after: 'search result' },
  ])).toThrow()
  expect(() => applyReviewEdits(['fast'], [{ unit: 1, before: 'slow', after: 'measured' }])).toThrow()
})

test('creates a separate text copy even for PDF originals', () => {
  expect(revisedSpecificationFilename('spec.pdf')).toBe('spec-revised.txt')
  expect(revisedSpecificationFilename('spec.md')).toBe('spec-revised.md')
})

test('allows deleting a duplicate but rejects an empty or invalid text document', () => {
  expect(applyReviewEdits(['Keep.', 'Duplicate.'], [{ unit: 2, before: 'Duplicate.', after: '' }])).toBe('Keep.\n')
  expect(() => applyReviewEdits(['Only requirement.'], [{ unit: 1, before: 'Only requirement.', after: '' }])).toThrow()
  expect(() => applyReviewEdits(['Keep.'], [{ unit: 1, before: 'Keep.', after: 'New\0text' }])).toThrow()
})

test('the correction queue exposes one independent button per finding and an unambiguous selection', () => {
  const findings: AgentSpecReview['findings'] = [
    { kind: 'other', severity: 'high', issue: 'First finding', recommendation: 'First correction', evidence: [], requirement_ids: [] },
    { kind: 'ambiguous', severity: 'medium', issue: 'Second finding', recommendation: 'Second correction', evidence: [], requirement_ids: [] },
  ]
  const html = renderToStaticMarkup(createElement(ReviewFixFindingList, {
    findings, selected: 1, accepted: {}, declined: {}, proposals: {}, disabled: false, onSelect: () => {},
  }))
  expect(html.match(/data-testid="fix-finding"/g)).toHaveLength(2)
  expect(html.match(/aria-pressed="true"/g)).toHaveLength(1)
  expect(html.match(/aria-pressed="false"/g)).toHaveLength(1)
  expect(html).not.toContain('<select')
})

test('correction errors distinguish a lost connection from a rejected AI proposal', () => {
  expect(reviewFixErrorMessage(new TypeError('Failed to fetch'), 'proposal')).toContain('сервером')
  expect(reviewFixErrorMessage(new ApiRequestError(429, 'RATE_LIMITED', 'Rate limit'), 'proposal')).toContain('лимит')
  expect(reviewFixErrorMessage(new ApiRequestError(502, 'REVIEW_FAILED', 'Invalid response'), 'proposal')).toContain('правку')
  expect(reviewFixErrorMessage(new TypeError('Failed to fetch'), 'verify')).toContain('Принятые правки сохранены')
})

test('accepted findings are identified independently of selection and count toward queue progress', () => {
  const findings: AgentSpecReview['findings'] = [
    { kind: 'other', severity: 'high', issue: 'First finding', recommendation: 'First correction', evidence: [], requirement_ids: [] },
    { kind: 'other', severity: 'medium', issue: 'Second finding', recommendation: 'Second correction', evidence: [], requirement_ids: [] },
  ]
  const html = renderToStaticMarkup(createElement(ReviewFixFindingList, {
    findings, selected: 1, accepted: { 'D-001': [{ unit: 1, before: 'old', after: 'new' }] },
    declined: {}, proposals: {}, disabled: false, onSelect: () => {},
  }))
  expect(html).toContain('aria-pressed="false" data-state="accepted"')
  expect(html).toContain('aria-pressed="true" data-state="pending"')
  expect(html).toContain('1 из 2')
})


test('unreadable photo blocks prevent correction of an incomplete source', () => {
  const report = { passport: { run_id: 'review-id' }, findings: [{}], unreadable_pages: [], unreadable_units: [2] } as unknown as AgentSpecReview
  expect(canFixReview(report)).toBe(false)
})
