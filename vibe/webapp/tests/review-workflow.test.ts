import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ReviewSetup, ReviewSteps } from '../src/features/agent/ReviewSetup'
import { agentReviewModeLabels, agentSpecReviewSchema } from '@web-app-demo/contracts'

import { deriveReviewSteps, formatReviewElapsed, filterReportFindings } from '../src/features/agent/review-workflow'
import { reviewFindingsWithDecisions } from '../src/features/agent/review-report'

test('onboarding steps reflect actual file, run and report state without simulated progress', () => {
  expect(deriveReviewSteps(false, 'setup')).toEqual(['current', 'upcoming', 'upcoming'])
  expect(deriveReviewSteps(true, 'setup')).toEqual(['done', 'current', 'upcoming'])
  expect(deriveReviewSteps(true, 'running')).toEqual(['done', 'done', 'current'])
  expect(deriveReviewSteps(true, 'report')).toEqual(['done', 'done', 'done'])
})

test('elapsed waiting shows real time in minutes and seconds', () => {
  expect(formatReviewElapsed(0)).toBe('0:00')
  expect(formatReviewElapsed(67)).toBe('1:07')
  expect(formatReviewElapsed(189)).toBe('3:09')
})

test('demo is a validated, document-only report and severity/assessment filters preserve original IDs', async () => {
  const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())
  expect(report.filename).toBe('fintech-payment-spec.txt')
  expect(report.reference_review).toBeNull()
  const entries = reviewFindingsWithDecisions(report)
  expect(entries.length).toBeGreaterThan(0)
  expect(filterReportFindings(entries, 'all', 'all')).toEqual(entries)
  expect(filterReportFindings(entries, 'high', 'all').every(({ finding }) => finding.severity === 'high')).toBe(true)
  expect(filterReportFindings(entries, 'all', 'unsupported').every(({ assessment }) => assessment?.status === 'unsupported')).toBe(true)
})

test('onboarding renders two arrow connectors and identifies the current step', () => {
  const markup = renderToStaticMarkup(createElement(ReviewSteps, { hasFile: true, phase: 'setup' }))
  expect(markup.match(/sr-step-arrow/g)).toHaveLength(2)
  expect(markup.match(/aria-current="step"/g)).toHaveLength(1)
  expect(markup).toContain('data-state="done"')
  expect(markup).toContain('data-state="upcoming"')
})

test('stepper exposes the real running state for its decorative animation', () => {
  const running = renderToStaticMarkup(createElement(ReviewSteps, { hasFile: true, phase: 'running' }))
  const report = renderToStaticMarkup(createElement(ReviewSteps, { hasFile: true, phase: 'report' }))
  expect(running).toContain('data-phase="running"')
  expect(running.match(/aria-current="step"/g)).toHaveLength(1)
  expect(report).toContain('data-phase="report"')
  expect(report).not.toContain('aria-current="step"')
})

test('setup has only one entry point to the prepared example', () => {
  const noop = () => {}
  const markup = renderToStaticMarkup(createElement(ReviewSetup, {
    references: [], cancelled: false, loadingExample: false,
    onFileSelect: noop, onReferencesChange: noop, onReferenceError: noop,
    onViewExample: noop, onSubmit: noop,
  }))
  expect(markup.match(/Посмотреть пример отчёта/g)).toHaveLength(1)
  expect(markup).not.toContain('Взять учебный пример')
})

test('setup offers only paired and direct validation with paired selected by default', () => {
  const noop = () => {}
  const props = { references: [], cancelled: false, loadingExample: false, onFileSelect: noop, onReferencesChange: noop, onReferenceError: noop, onViewExample: noop, onSubmit: noop }
  for (const mode of [undefined, 'multi_agent', 'multi_agent_direct'] as const) {
    const markup = renderToStaticMarkup(createElement(ReviewSetup, { ...props, reviewMode: mode }))
    const summary = markup.match(/<summary>(.*?)<\/summary>/s)?.[1]
    expect(summary).toContain(agentReviewModeLabels[mode ?? 'multi_agent'])
    const radios = markup.match(/<input[^>]+type="radio"[^>]*>/g) ?? []
    expect(radios.map(radio => radio.match(/value="([^"]+)"/)?.[1])).toEqual(['multi_agent', 'multi_agent_direct'])
    expect(radios.filter(radio => radio.includes('checked=""')).map(radio => radio.match(/value="([^"]+)"/)?.[1])).toEqual([mode ?? 'multi_agent'])
  }
})

test('reattaching a durable job hides upload and mode editing and permits recovery without an original file', () => {
  const noop = () => {}
  const markup = renderToStaticMarkup(createElement(ReviewSetup, {
    pendingRecovery: true, references: [], cancelled: false, loadingExample: false, requestError: 'Connection lost',
    onFileSelect: noop, onReferencesChange: noop, onReferenceError: noop, onViewExample: noop, onSubmit: noop,
  }))
  expect(markup).toContain('Возобновить проверку')
  expect(markup).not.toContain('Режим проверки')
  expect(markup).not.toContain('type="file"')
  expect(markup).not.toContain('disabled=""')
})
