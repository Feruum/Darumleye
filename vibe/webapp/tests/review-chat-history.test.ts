import { expect, test } from 'bun:test'
import type { AgentCompletedReview } from '@web-app-demo/contracts'
import { filterReviewHistory, groupReviewHistory } from '../src/features/agent/review-chat-history'

const review = (id: string, filename: string, day: number, hour: number, verdict: AgentCompletedReview['verdict']): AgentCompletedReview => ({
  id, filename, created_at: new Date(2026, 10, day, hour, 5).toISOString(), verdict, requirements: 4, findings: 1,
})
const items = [
  review('1', 'payment-spec.txt', 25, 17, 'not_ready'),
  review('2', 'PAYMENT-spec-revised.txt', 25, 21, 'ready'),
  review('3', 'регламент_переводов.md', 24, 10, 'needs_revision'),
  review('4', 'payment-spec.txt', 20, 9, 'ready'),
]

test('history search matches filename regardless of case, separators, date/time and status', () => {
  expect(filterReviewHistory(items, 'payment revised', 'all').map(item => item.id)).toEqual(['2'])
  expect(filterReviewHistory(items, 'регламент переводов', 'all').map(item => item.id)).toEqual(['3'])
  expect(filterReviewHistory(items, '21:05', 'all').map(item => item.id)).toEqual(['2'])
  expect(filterReviewHistory(items, 'не готово', 'all').map(item => item.id)).toEqual(['1'])
  expect(filterReviewHistory(items, 'payment', 'ready').map(item => item.id)).toEqual(['2', '4'])
  expect(filterReviewHistory(items, 'unknown', 'all')).toEqual([])
})

test('history groups independently selected reviews by local day, newest first', () => {
  const groups = groupReviewHistory(items, new Date(2026, 10, 25, 23))
  expect(groups.map(group => group.label)).toEqual(['Сегодня', 'Вчера', '20 ноября'])
  expect(groups[0]?.reviews.map(item => item.id)).toEqual(['2', '1'])
  expect(groups[1]?.reviews.map(item => item.id)).toEqual(['3'])
})
