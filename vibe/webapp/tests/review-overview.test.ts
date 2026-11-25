import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { agentSpecReviewSchema } from '@web-app-demo/contracts'
import { expect, test } from 'bun:test'
import { authQueryKeys, clearAuthenticatedSession } from '../src/features/auth/queries'
import { createExampleFile, loadExampleReport } from '../src/features/agent/review-example'
import { activityWeeksForWidth, buildActivityCalendar, mergeReviewHistory, recordReview, reviewHistoryKey, reviewHistoryOptions, summarizeReview, summarizeReviews } from '../src/features/agent/review-overview'

const report = agentSpecReviewSchema.parse(await Bun.file(new URL('../src/features/agent/examples/fintech-payment-review.json', import.meta.url)).json())

test('the public example uses app-bundled content and requires no separate fetch', async () => {
  const example = loadExampleReport()
  const file = createExampleFile()
  expect(example).toEqual(report)
  expect(file.name).toBe(example.filename)
  expect(await file.text()).toContain('100 000')
})

test('overview uses report statistics and actual severity counts, including zeroes', () => {
  const summary = summarizeReview(report)
  expect(summary).toMatchObject({ requirements: 7, findings: 8, passed: 1, needsRevision: 6, verdict: 'not_ready', severity: { critical: 0, high: 4, medium: 4, low: 0 } })
  expect(summarizeReviews([])).toMatchObject({ reviews: 0, requirements: 0, findings: 0, passed: 0, needsRevision: 0, severity: {critical: 0, high: 0, medium: 0, low: 0} })
  expect(summarizeReviews([summary, { ...summary, id: 'another' }])).toMatchObject({ reviews: 2, requirements: 14, findings: 16, passed: 2, needsRevision: 12, severity: {critical: 0, high: 8, medium: 8, low: 0} })
})

test('completed report history is owner-scoped, deduplicated and removed on sign-out', async () => {
  const client = new QueryClient()
  client.setQueryData(authQueryKeys.me(), { user: { id: 'owner' } })
  recordReview(client, 'owner', report)
  recordReview(client, 'owner', report)
  recordReview(client, 'other', report)
  expect(client.getQueryData(reviewHistoryKey('owner'))).toEqual([summarizeReview(report)])
  expect(client.getQueryData(reviewHistoryKey('other'))).toBeUndefined()
  await clearAuthenticatedSession(client, () => {})
  recordReview(client, 'owner', report)
  expect(client.getQueryData(reviewHistoryKey('owner'))).toBeUndefined()
  client.clear()
})

test('the dashboard observes newly completed reviews without a server fetch and retains summaries across route changes', () => {
  const client = new QueryClient()
  client.setQueryData(authQueryKeys.me(), { user: { id: 'owner' } })
  const options = reviewHistoryOptions('owner')
  const overview = new QueryObserver(client, options)
  const unsubscribe = overview.subscribe(() => {})
  expect(overview.getCurrentResult().data).toEqual([])
  expect(overview.getCurrentResult().fetchStatus).toBe('idle')
  recordReview(client, 'owner', report)
  expect(overview.getCurrentResult().data).toEqual([summarizeReview(report)])
  unsubscribe()
  const revisit = new QueryObserver(client, options)
  expect(revisit.getCurrentResult().data).toEqual([summarizeReview(report)])
  client.clear()
})

test('activity calendar includes every run on its local date, starts on Monday and never shows future activity', () => {
  const summary = summarizeReview(report)
  const now = new Date(2026, 10, 25, 12)
  const reviews = [
    { ...summary, date: new Date(2026, 10, 25, 9).toISOString() },
    { ...summary, id: 'second', date: new Date(2026, 10, 25, 11).toISOString() },
    { ...summary, id: 'previous', date: new Date(2026, 10, 24, 11).toISOString() },
  ]
  const calendar = buildActivityCalendar(reviews, now)
  expect(calendar).toHaveLength(12)
  expect(calendar.every(week => week.length === 7)).toBe(true)
  const days = calendar.flat()
  expect(days.find(day => day.key === '2026-11-25')).toMatchObject({ count: 2, today: true, future: false })
  expect(days.find(day => day.key === '2026-11-24')).toMatchObject({ count: 1, future: false })
  expect(days.find(day => day.key === '2026-11-26')).toMatchObject({ count: 0, future: true })
  expect(days.reduce((sum, day) => sum + day.count, 0)).toBe(3)
})


test('activity calendar adapts the period to the available width without oversized cells', () => {
  expect(activityWeeksForWidth(270)).toBe(12)
  expect(activityWeeksForWidth(500)).toBe(21)
  expect(activityWeeksForWidth(840)).toBe(37)
  expect(activityWeeksForWidth(1400)).toBe(52)
  const now = new Date(2026, 10, 25, 12)
  const weeks = buildActivityCalendar([], now, activityWeeksForWidth(840))
  expect(weeks).toHaveLength(37)
  expect(weeks.flat().filter(day => day.today)).toHaveLength(1)
  expect(weeks[0]?.[0]?.date.getDay()).toBe(1)
})


test('overview restores real archived analysis after a reload and deduplicates the current run', () => {
  const summary = summarizeReview(report)
  const archive = { id: summary.id, filename: summary.filename, created_at: summary.date, verdict: summary.verdict, requirements: summary.requirements, findings: summary.findings, analytics: { passed: summary.passed, needs_revision: summary.needsRevision, severity: summary.severity } }
  expect(mergeReviewHistory([], [archive])).toEqual([summary])
  expect(mergeReviewHistory([summary], [archive])).toEqual([summary])
})
