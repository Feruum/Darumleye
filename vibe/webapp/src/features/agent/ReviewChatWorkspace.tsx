import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { File01Icon, ArrowRight01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { Alert, AlertDescription } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { SearchField } from '@/components/interface/search-field'
import { Typography } from '@/components/typography'
import { useAuth } from '@/features/auth'
import { AgentThread } from './AgentThread'
import { MemoryCard } from './MemoryCard'
import { completedReviewsQueryOptions } from './queries'
import { filterReviewHistory, groupReviewHistory, reviewHistoryDate, reviewHistoryTime, reviewVerdictLabels, type ReviewHistoryFilter } from './review-chat-history'

export function ReviewChatWorkspace() {
  const { user } = useAuth()
  const { reviewId } = useSearch({ from: '/userWorkspace/app/chat' })
  return <AuthenticatedReviewChat key={`${user?.id}:${reviewId ?? ''}`} initialReviewId={reviewId} />
}

function AuthenticatedReviewChat({ initialReviewId }: { initialReviewId?: string }) {
  const { transport, user } = useAuth()
  const query = useQuery(completedReviewsQueryOptions(transport, user?.id))
  const [selectedId, setSelectedId] = useState<string | undefined>(initialReviewId)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<ReviewHistoryFilter>('all')
  const reviews = query.data?.reviews ?? []
  const selected = selectedId ? reviews.find((review) => review.id === selectedId) : reviews[0]
  const filtered = filterReviewHistory(reviews, search, filter)
  const groups = groupReviewHistory(filtered)
  const hasFilters = Boolean(search.trim()) || filter !== 'all'
  const clearFilters = () => { setSearch(''); setFilter('all') }
  return <div className="sr-page agent-page mx-auto grid w-full min-w-0 max-w-[1440px] gap-5 p-4 md:p-8" data-mode="knowledge" lang="ru">
    <header className="grid gap-1.5">
      <Typography as="h1" variant="h2">Чат по проверкам</Typography>
      <Typography tone="muted" variant="bodySm">Обсуждайте результаты проверки и исходные требования. Повторная загрузка не нужна.</Typography>
    </header>
    <div className="agent-knowledge-layout grid min-w-0 gap-4">
      <aside className="agent-knowledge-sidebar grid min-w-0 content-start gap-3" aria-label="Завершённые проверки">
        <section className="sr-panel agent-review-history" aria-label="История проверок">
          <header className="agent-history-header">
            <div className="flex items-center justify-between gap-2">
              <Typography as="h2" variant="h6">История проверок</Typography>
              {reviews.length > 0 && <Typography variant="caption" tone="muted" className="tabular-nums">{reviews.length}</Typography>}
            </div>
            {reviews.length > 0 && <>
              <SearchField label="Поиск отчёта" placeholder="Название или дата" value={search} onValueChange={setSearch} />
              <div className="agent-history-filters">
                <select aria-label="Статус проверки" value={filter} onChange={(event) => setFilter(event.currentTarget.value as ReviewHistoryFilter)}>
                  <option value="all">Все статусы</option>
                  {Object.entries(reviewVerdictLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <Typography as="span" variant="caption" tone="muted" role="status" className="tabular-nums">{filtered.length} из {reviews.length}</Typography>
              </div>
            </>}
          </header>
          <div className="agent-history-scroll" aria-label="Список проверок">
            {query.isPending ? <Typography tone="muted" variant="bodySm">Загрузка проверок…</Typography> : query.isError ? <div className="grid gap-2"><Alert variant="destructive"><AlertDescription>Не удалось загрузить проверки.</AlertDescription></Alert><Button type="button" variant="outline" onClick={() => void query.refetch()}>Повторить</Button></div> : reviews.length === 0 ? <div className="grid gap-3">
              <Typography tone="muted" variant="bodySm">Пока нет завершённых проверок. Они появятся здесь автоматически.</Typography>
              <Button asChild className="sr-primary"><Link to="/app/assistant">Проверить ТЗ <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Link></Button>
            </div> : groups.length === 0 ? <div className="grid justify-items-start gap-2 py-3">
              <Typography variant="bodySmMedium">Ничего не найдено</Typography>
              <Typography variant="caption" tone="muted">Измените запрос или статус.</Typography>
              <Button variant="ghost" type="button" size="sm" onClick={clearFilters}>Сбросить фильтры</Button>
            </div> : groups.map(group => <section className="agent-history-group" aria-label={group.label} key={group.key}>
              <Typography as="h3" variant="caption" tone="muted" className="agent-history-group-label">{group.label}</Typography>
              <ul className="grid gap-0.5">{group.reviews.map(review => <li key={review.id}>
                <button className="agent-review-choice" type="button" title={`${review.filename} · ${reviewHistoryDate.format(new Date(review.created_at))}`} data-testid="completed-review" aria-pressed={review.id === selected?.id} onClick={() => setSelectedId(review.id)}>
                  <HugeiconsIcon icon={File01Icon} size={17} aria-hidden className="shrink-0" />
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <Typography as="span" variant="bodySmMedium" className="truncate">{review.filename}</Typography>
                    <span className="agent-review-meta">
                      <Typography as="time" variant="caption" tone="muted" dateTime={review.created_at}>{reviewHistoryTime.format(new Date(review.created_at))}</Typography>
                      <span className="agent-review-verdict" data-verdict={review.verdict}>
                        <span aria-hidden="true" />
                        <Typography as="span" variant="caption">{reviewVerdictLabels[review.verdict]}</Typography>
                      </span>
                    </span>
                  </span>
                </button>
              </li>)}</ul>
            </section>)}
          </div>
          {selected && <footer className="agent-history-footer">
            {hasFilters && !filtered.some(review => review.id === selected.id) && <Typography variant="caption" tone="muted" className="break-words">Текущая проверка: {selected.filename}</Typography>}
            <Button asChild variant="outline" size="sm"><Link to="/app/assistant" search={{ reviewId: selected.id }}>Открыть отчёт <HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden /></Link></Button>
          </footer>}
        </section>
        <details className="sr-panel sr-knowledge-disclosure">
          <Typography as="summary" variant="bodySmMedium">Заметки для ассистента</Typography>
          <MemoryCard />
        </details>
      </aside>
      <section className="agent-chat-panel" aria-label="Диалог по проверке">
        <AgentThread key={selected?.id ?? 'empty'} transport={transport} reviewId={selected?.id} reviewFilename={selected?.filename} />
      </section>
    </div>
  </div>
}
