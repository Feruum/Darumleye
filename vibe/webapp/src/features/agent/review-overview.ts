import { queryOptions, skipToken, type QueryClient } from '@tanstack/react-query'
import type { AgentCompletedReview, AgentSpecReview, MeResponse } from '@web-app-demo/contracts'

import { authQueryKeys, sessionQueryKeys } from '@/features/auth'

export type ReviewSeverity = AgentSpecReview['findings'][number]['severity']
export type ReviewSummary = {
  id: string
  filename: string
  date: string
  verdict: AgentSpecReview['verdict']['status']
  requirements: number
  passed: number
  needsRevision: number
  findings: number
  severity: Record<ReviewSeverity, number>
}

const reviewHistoryPrefix = [...sessionQueryKeys.all, 'agent', 'review-overview'] as const
export const reviewHistoryKey = (ownerId: string) => [...reviewHistoryPrefix, ownerId] as const
const emptySeverity = (): Record<ReviewSeverity, number> => ({ critical: 0, high: 0, medium: 0, low: 0 })

export function summarizeReview(report: AgentSpecReview): ReviewSummary {
  const severity = emptySeverity()
  for (const finding of report.findings) severity[finding.severity] += 1
  return {
    id: report.passport.run_id ?? `${report.passport.run_at}:${report.filename}`,
    filename: report.filename,
    date: report.passport.run_at,
    verdict: report.verdict.status,
    requirements: report.statistics.requirements,
    passed: report.statistics.passed,
    needsRevision: report.statistics.needs_revision,
    findings: report.statistics.violations,
    severity,
  }
}

export function summarizeReviews(reviews: readonly ReviewSummary[]) {
  return reviews.reduce((totals, review) => {
    totals.reviews++
    totals.requirements += review.requirements
    totals.passed += review.passed
    totals.needsRevision += review.needsRevision
    totals.findings += review.findings
    for (const severity of Object.keys(totals.severity) as ReviewSeverity[]) totals.severity[severity] += review.severity[severity]
    return totals
  }, { reviews: 0, requirements: 0, passed: 0, needsRevision: 0, findings: 0, severity: emptySeverity() })
}

/** Merge immediate local summaries with persistent archive analytics, without counting a run twice. */
export function mergeReviewHistory(session: readonly ReviewSummary[], archive: readonly AgentCompletedReview[]): ReviewSummary[] {
  const merged = new Map(session.map((review) => [review.id, review]))
  for (const review of archive) {
    if (!review.analytics) continue
    merged.set(review.id, {
      id: review.id, filename: review.filename, date: review.created_at, verdict: review.verdict,
      requirements: review.requirements, findings: review.findings, passed: review.analytics.passed,
      needsRevision: review.analytics.needs_revision, severity: review.analytics.severity,
    })
  }
  return [...merged.values()].sort((a, b) => Date.parse(b.date) - Date.parse(a.date))
}

/** Keep only a lightweight, current-account summary in memory. Sign-out clears the session namespace. */
export function recordReview(client: QueryClient, ownerId: string, report: AgentSpecReview) {
  if (client.getQueryData<MeResponse>(authQueryKeys.me())?.user.id !== ownerId) return
  const summary = summarizeReview(report)
  client.setQueryDefaults(reviewHistoryPrefix, { gcTime: Infinity })
  client.setQueryData<ReviewSummary[]>(reviewHistoryKey(ownerId), (current = []) => [summary, ...current.filter((entry) => entry.id !== summary.id)])
}

export function reviewHistoryOptions(ownerId: string) {
  return queryOptions({ queryKey: reviewHistoryKey(ownerId), queryFn: skipToken, initialData: [] as ReviewSummary[], staleTime: Infinity, gcTime: Infinity })
}

export function reviewDayKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export type ReviewActivityDay = { key: string; date: Date; count: number; today: boolean; future: boolean }

/** Fit approximately 18px cells plus gaps within the actual card, not the viewport. */
export function activityWeeksForWidth(width: number) {
  if (!Number.isFinite(width) || width <= 0) return 12
  return Math.max(12, Math.min(52, Math.floor((width - 22) / 22)))
}

/** Calendar weeks starting on Monday and ending in the current week. */
export function buildActivityCalendar(reviews: readonly ReviewSummary[], now = new Date(), weeks = 12): ReviewActivityDay[][] {
  const weekCount = Math.max(1, Math.min(52, Math.floor(weeks)))
  const counts = new Map<string, number>()
  for (const review of reviews) {
    const key = reviewDayKey(new Date(review.date))
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const today = reviewDayKey(now)
  const first = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12)
  first.setDate(first.getDate() - ((first.getDay() + 6) % 7) - (weekCount - 1) * 7)
  return Array.from({ length: weekCount }, (_, week) => Array.from({ length: 7 }, (_, weekday) => {
    const date = new Date(first)
    date.setDate(first.getDate() + week * 7 + weekday)
    const key = reviewDayKey(date)
    const future = key > today
    return { key, date, count: future ? 0 : counts.get(key) ?? 0, today: key === today, future }
  }))
}
