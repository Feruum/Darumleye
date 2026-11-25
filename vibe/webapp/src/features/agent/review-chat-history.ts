import type { AgentCompletedReview } from '@web-app-demo/contracts'
import { reviewDayKey } from './review-overview'

export const reviewVerdictLabels = { ready: 'Готово', needs_revision: 'Нужна доработка', not_ready: 'Не готово' } as const
export type ReviewHistoryFilter = 'all' | AgentCompletedReview['verdict']
export const reviewHistoryDate = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
export const reviewHistoryTime = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })

function normalize(value: string) {
  return value.toLocaleLowerCase('ru-RU').replace(/ё/g, 'е').replace(/[^\p{L}\p{N}:.]+/gu, ' ').trim()
}

export function filterReviewHistory(reviews: readonly AgentCompletedReview[], query: string, filter: ReviewHistoryFilter) {
  const terms = normalize(query).split(/\s+/).filter(Boolean)
  return reviews.filter(review => {
    if (filter !== 'all' && review.verdict !== filter) return false
    if (!terms.length) return true
    const date = new Date(review.created_at)
    const text = normalize(`${review.filename} ${reviewHistoryDate.format(date)} ${reviewVerdictLabels[review.verdict]} ${review.id}`)
    return terms.every(term => text.includes(term))
  })
}

export function groupReviewHistory(reviews: readonly AgentCompletedReview[], now = new Date()) {
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const todayKey = reviewDayKey(now)
  const yesterdayKey = reviewDayKey(yesterday)
  const groups = new Map<string, { key: string; label: string; reviews: AgentCompletedReview[] }>()
  for (const review of reviews.toSorted((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))) {
    const date = new Date(review.created_at)
    const key = reviewDayKey(date)
    let group = groups.get(key)
    if (!group) {
      const label = key === todayKey ? 'Сегодня' : key === yesterdayKey ? 'Вчера'
        : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', ...(date.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) }).format(date)
      group = { key, label, reviews: [] }
      groups.set(key, group)
    }
    group.reviews.push(review)
  }
  return [...groups.values()]
}
