import { Fragment, useLayoutEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { agentCompletedReviewSchema, type AgentCompletedReview, type UserDto } from '@web-app-demo/contracts'
import { Alert02Icon, ArrowRight01Icon, Message01Icon, CheckmarkCircle02Icon, File01Icon, Search01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'

import { ArcBadge, ArcDataTable } from '@/components/arc'
import { Skeleton } from '@/components/arc/skeleton/skeleton'
import { Button } from '@/components/interface/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/interface/card'
import { Typography } from '@/components/typography'
import { useAuth } from '@/features/auth'
import { completedReviewsQueryOptions, activityWeeksForWidth, buildActivityCalendar, loadExampleReport, mergeReviewHistory, reviewDayKey, reviewHistoryOptions, summarizeReview, summarizeReviews, type ReviewActivityDay, type ReviewSeverity, type ReviewSummary } from '@/features/agent'
import './review-dashboard.css'

const count = new Intl.NumberFormat('ru-RU')
const time = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
const severityLabels: Record<ReviewSeverity, string> = { critical: 'Критичные', high: 'Высокие', medium: 'Средние', low: 'Низкие' }
const verdictLabels = { ready: 'Готово', needs_revision: 'Требует доработки', not_ready: 'Не готово' } as const

export function ReviewDashboard({ user }: { user: UserDto }) {
  const { transport } = useAuth()
  const { data: sessionReviews = [] } = useQuery(reviewHistoryOptions(user.id))
  const knowledge = useQuery(completedReviewsQueryOptions(transport, user.id))
  const reviews = mergeReviewHistory(sessionReviews, knowledge.data?.reviews ?? [])
  const [selectedDay, setSelectedDay] = useState<string | undefined>()
  const totals = summarizeReviews(reviews)
  const example = useQuery({ queryKey: ['public', 'overview', 'example'], queryFn: async () => summarizeReview(loadExampleReport()), enabled: knowledge.isSuccess && reviews.length === 0, staleTime: Infinity, retry: false })
  const chartTotals = reviews.length > 0 ? totals : example.data ? summarizeReviews([example.data]) : undefined
  const metrics = [
    { label: 'Проверено ТЗ', value: totals.reviews, icon: File01Icon, tone: 'violet' },
    { label: 'Требования', value: totals.requirements, icon: CheckmarkCircle02Icon, tone: 'violet' },
    { label: 'Замечания', value: totals.findings, icon: Search01Icon, tone: 'violet' },
    { label: 'Критичные', value: totals.severity.critical, icon: Alert02Icon, tone: 'critical' },
  ] as const

  return <div className="review-overview mx-auto grid w-full min-w-0 max-w-[1440px] gap-5 p-4 md:p-8" data-testid="review-dashboard" lang="ru">
    <header className="overview-heading flex flex-wrap items-end justify-between gap-4">
      <div className="grid gap-1.5 min-w-0">
        <Typography as="h1" variant="h2">Обзор</Typography>
        <Typography variant="bodySm" tone="muted" data-testid="dashboard-account" className="break-words">{user.displayName ?? user.email}</Typography>
      </div>
    </header>

    <section aria-label="Показатели проверок" className="grid min-w-0 gap-3">
      <div className="flex items-center justify-between gap-3">
        <Typography as="h2" variant="h6">Показатели</Typography>
        <ArcBadge tone="neutral" size="sm">История проверок</ArcBadge>
      </div>
      <div className="overview-metrics-grid grid min-w-0 grid-cols-2 xl:grid-cols-4">
        {metrics.map(({ label, value, icon, tone }) => <Card key={label} size="sm" className="overview-metric min-w-0">
          <CardContent className="grid gap-3">
            <div className="flex items-center justify-between gap-2"><Typography variant="bodySm" tone="muted">{label}</Typography><span className="overview-metric-icon" data-tone={tone}><HugeiconsIcon icon={icon} size={19} strokeWidth={1.8} aria-hidden /></span></div>
            <Typography as="strong" variant="h2" className="tabular-nums">{count.format(value)}</Typography>
          </CardContent>
        </Card>)}
      </div>
    </section>

    <section aria-label={reviews.length ? 'Аналитика текущих проверок' : 'Демонстрационная аналитика'} className="grid gap-3 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2"><Typography as="h2" variant="h6">{reviews.length ? 'Аналитика проверок' : 'Пример анализа'}</Typography>{!reviews.length && <ArcBadge tone="info" size="sm">Учебные данные</ArcBadge>}</div>
        {!reviews.length && <Typography variant="caption" tone="muted">Учебное ТЗ · платежи</Typography>}
      </div>
      {chartTotals ? <div className="overview-charts">
        <QualityChart totals={chartTotals} />
        <SeverityChart severity={chartTotals.severity} />
      </div> : <Card>{knowledge.isError || example.isError ? <div className="flex items-center justify-between gap-3"><Typography variant="bodySm" tone="muted">Не удалось загрузить {knowledge.isError ? 'проверки' : 'пример'}</Typography><Button variant="outline" size="sm" onClick={() => void (knowledge.isError ? knowledge.refetch() : example.refetch())}>Повторить</Button></div> : <Skeleton label="Загрузка аналитики" lines={4} />}</Card>}
    </section>

    <section className="overview-lower" aria-label="История и материалы">
      <ActivityCalendar reviews={reviews} selectedDay={selectedDay} onSelectDay={(day) => setSelectedDay(selectedDay === day ? undefined : day)} />
      <Card className="overview-archive-card min-w-0">
        <CardHeader><div className="flex items-center justify-between gap-2"><CardTitle>Отчёты по проверкам</CardTitle><span className="overview-metric-icon" data-tone="violet"><HugeiconsIcon icon={Message01Icon} size={19} strokeWidth={1.8} aria-hidden /></span></div></CardHeader>
        {knowledge.isPending ? <Skeleton label="Загрузка проверок" lines={3} /> : knowledge.isError ? <div className="grid gap-3"><Typography variant="bodySm" tone="muted">Не удалось загрузить проверки</Typography><Button variant="outline" size="sm" onClick={() => void knowledge.refetch()}>Повторить</Button></div> : <ReviewChatSummary reviews={knowledge.data?.reviews ?? []} />}
      </Card>
      <RecentReviews reviews={reviews} selectedDay={selectedDay} onClearDay={() => setSelectedDay(undefined)} />
    </section>
  </div>
}

function QualityChart({ totals }: { totals: ReturnType<typeof summarizeReviews> }) {
  const [highlight, setHighlight] = useState<'passed' | 'revision'>()
  const total = totals.passed + totals.needsRevision
  const preciseShare = total ? 100 * totals.passed / total : 0
  const share = Math.round(preciseShare)
  const focusedShare = highlight === 'revision' && total ? Math.round(100 * totals.needsRevision / total) : share
  const focusedLabel = highlight === 'revision' ? 'на доработку' : 'без замечаний'
  const passedColor = highlight === 'revision' ? 'color-mix(in srgb, var(--overview-success) 30%, var(--arc-surface))' : 'var(--overview-success)'
  const revisionColor = highlight === 'passed' ? 'color-mix(in srgb, var(--overview-purple) 30%, var(--arc-surface))' : 'var(--overview-purple)'
  return <Card className="overview-chart-card min-w-0">
    <CardHeader><CardTitle>Качество требований</CardTitle></CardHeader>
    <CardContent className="overview-quality">
      <div className="overview-ring" role="img" aria-label={total ? `${share}% требований без замечаний` : 'Нет оценённых требований'} title={total ? `${totals.passed} из ${total} требований без замечаний` : undefined}>
        <span className="overview-pie" aria-hidden="true" style={{ backgroundImage: total ? `conic-gradient(${passedColor} 0% ${preciseShare}%, ${revisionColor} ${preciseShare}% 100%)` : undefined }} />
        <div className="overview-ring-label"><Typography as="strong" variant="h2" className="tabular-nums">{total ? `${focusedShare}%` : '—'}</Typography><Typography variant="caption" tone="muted">{focusedLabel}</Typography></div>
      </div>
      <div className="grid gap-4 min-w-0">
        <div className="overview-quality-total"><Typography as="strong" variant="h3" className="tabular-nums">{count.format(total)}</Typography><Typography variant="caption" tone="muted">Всего требований</Typography></div>
        <div className="grid gap-3"><LegendRow color="var(--overview-success)" label="Без замечаний" value={totals.passed} onHover={() => setHighlight('passed')} onLeave={() => setHighlight(undefined)} /><LegendRow color="var(--overview-purple)" label="На доработку" value={totals.needsRevision} onHover={() => setHighlight('revision')} onLeave={() => setHighlight(undefined)} /></div>
      </div>
    </CardContent>
  </Card>
}

function LegendRow({ color, label, value, onHover, onLeave }: { color: string; label: string; value: number; onHover: () => void; onLeave: () => void }) {
  return <div className="overview-legend" onPointerEnter={onHover} onPointerLeave={onLeave}><span aria-hidden="true" className="overview-legend-dot" style={{ backgroundColor: color }} /><Typography variant="bodySm">{label}</Typography><Typography as="strong" variant="bodySmMedium" className="tabular-nums">{count.format(value)}</Typography></div>
}

function SeverityChart({ severity }: { severity: Record<ReviewSeverity, number> }) {
  const total = Object.values(severity).reduce((sum, value) => sum + value, 0)
  return <Card className="overview-chart-card min-w-0">
    <CardHeader><div className="flex items-center justify-between gap-2"><CardTitle>Важность замечаний</CardTitle><Typography variant="caption" tone="muted">Всего: {count.format(total)}</Typography></div></CardHeader>
    <CardContent className="grid gap-4">
      {(Object.keys(severityLabels) as ReviewSeverity[]).map((level) => <div className="overview-severity-row grid gap-1.5" key={level} data-severity={level} title={`${severityLabels[level]}: ${severity[level]} (${total ? Math.round(100 * severity[level] / total) : 0}%)`}>
        <div className="flex justify-between items-center gap-2"><Typography variant="bodySm">{severityLabels[level]}</Typography><div className="flex items-center gap-2"><Typography variant="caption" tone="muted" className="overview-severity-percent tabular-nums">{total ? Math.round(100 * severity[level] / total) : 0}%</Typography><Typography variant="bodySmMedium" className="tabular-nums">{count.format(severity[level])}</Typography></div></div>
        <div className="overview-bar" aria-hidden="true"><div className="overview-bar-fill" style={{ width: `${total ? 100 * severity[level] / total : 0}%` }} /></div>
      </div>)}
    </CardContent>
  </Card>
}

function RecentReviews({ reviews, selectedDay, onClearDay }: { reviews: ReviewSummary[]; selectedDay?: string; onClearDay: () => void }) {
  const [showAll, setShowAll] = useState(false)
  const filtered = reviews.filter((review) => !selectedDay || reviewDayKey(new Date(review.date)) === selectedDay)
    .sort((a,b) => Date.parse(b.date) - Date.parse(a.date))
  const rows = showAll || selectedDay ? filtered : filtered.slice(0, 8)
  return <Card size="sm" className="overview-history min-w-0">
    <CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><CardTitle>{selectedDay ? `Проверки · ${selectedDay.split('-').reverse().join('.')}` : 'Последние проверки'}</CardTitle>{selectedDay && <Button type="button" variant="outline" size="sm" onClick={onClearDay}>Все даты</Button>}{!selectedDay && filtered.length > 8 && <Button type="button" variant="ghost" size="sm" onClick={() => setShowAll(!showAll)}>{showAll ? 'Последние 8' : `Все проверки (${filtered.length})`}</Button>}</div></CardHeader>
    <CardContent className="min-w-0">
      {rows.length ? <ArcDataTable<ReviewSummary> rows={rows} rowKey="id" caption="Последние проверки" defaultSort={{ key: 'date', direction: 'desc' }} itemName={{ one: 'проверка', other: 'проверок' }} columns={[
        { key: 'filename', label: 'Документ', render: (_, row) => agentCompletedReviewSchema.shape.id.safeParse(row.id).success ? <Link to="/app/assistant" search={{ reviewId: row.id }} className="overview-report-link" title={row.filename}><Typography as="span" variant="bodySmMedium" className="break-words">{row.filename}</Typography></Link> : <Typography variant="bodySmMedium" className="break-words" title={row.filename}>{row.filename}</Typography> },
        { key: 'date', label: 'Дата', sortable: true, render: (_, row) => <Typography variant="caption" tone="muted">{time.format(new Date(row.date))}</Typography> },
        { key: 'requirements', label: 'Требования', numeric: true, sortable: true },
        { key: 'findings', label: 'Замечания', numeric: true, sortable: true },
        { key: 'verdict', label: 'Вердикт', render: (_, row) => <ArcBadge size="sm" tone={row.verdict === 'ready' ? 'success' : row.verdict === 'needs_revision' ? 'warning' : 'danger'}>{verdictLabels[row.verdict]}</ArcBadge> },
      ]} /> : <div className="overview-history-empty"><div className="flex min-w-0 items-center gap-3"><span className="overview-metric-icon" data-tone="violet"><HugeiconsIcon icon={File01Icon} size={19} aria-hidden /></span><Typography variant="bodySm" tone="muted">Пока нет проверок</Typography></div><Button asChild variant="outline" size="sm"><Link to="/app/assistant">Начать проверку <HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden /></Link></Button></div>}
    </CardContent>
  </Card>
}

function ReviewChatSummary({ reviews }: { reviews: AgentCompletedReview[] }) {
  const recent = [...reviews].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).slice(0, 3)
  if (recent.length === 0) return <CardContent className="overview-archive-content-empty grid gap-3">
    <div className="overview-archive-empty">
      <HugeiconsIcon icon={File01Icon} size={22} strokeWidth={1.6} aria-hidden />
      <Typography variant="bodySmMedium">Нет завершённых ТЗ</Typography>
    </div>
    <Button asChild variant="outline"><Link to="/app/assistant">Начать проверку <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Link></Button>
  </CardContent>
  return <CardContent className="overview-archive-content grid gap-3 min-w-0">
    <div className="flex items-center justify-between gap-2"><Typography variant="caption" tone="muted">Завершённые проверки</Typography><ArcBadge tone="info" size="sm">{count.format(reviews.length)}</ArcBadge></div>
    <ul className="overview-archive-list">{recent.map(review => <li key={review.id}><Link to="/app/assistant" search={{ reviewId: review.id }} className="overview-archive-row" aria-label={`Открыть отчёт: ${review.filename}`} title="Открыть отчёт" data-testid="archived-review-link">
      <span className="overview-archive-file"><HugeiconsIcon icon={File01Icon} size={19} strokeWidth={1.7} aria-hidden /></span>
      <div className="grid min-w-0 gap-1"><Typography as="span" variant="bodySmMedium" className="truncate" title={review.filename}>{review.filename}</Typography><Typography as="span" variant="caption" tone="muted">{time.format(new Date(review.created_at))}</Typography></div>
      <span className="overview-archive-row-end"><ArcBadge size="sm" tone={review.verdict === 'ready' ? 'success' : review.verdict === 'needs_revision' ? 'warning' : 'danger'}>{verdictLabels[review.verdict]}</ArcBadge><HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden /></span>
    </Link></li>)}</ul>
    <Button asChild variant="outline"><Link to="/app/chat">Открыть чат <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Link></Button>
  </CardContent>
}

const calendarDate = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
const calendarMonth = new Intl.DateTimeFormat('ru-RU', { month: 'short' })

function dayLevel(value: number) { return value === 0 ? 0 : value === 1 ? 1 : value === 2 ? 2 : value <= 4 ? 3 : 4 }
function dayDescription(day: ReviewActivityDay) { return `${calendarDate.format(day.date)} · проверок: ${count.format(day.count)}` }

function ActivityCalendar({ reviews, selectedDay, onSelectDay }: { reviews: ReviewSummary[]; selectedDay?: string; onSelectDay: (key: string) => void }) {
  const [hovered, setHovered] = useState<ReviewActivityDay>()
  const [weekCount, setWeekCount] = useState(12)
  const calendarRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = calendarRef.current
    if (!element) return
    const resize = () => setWeekCount(activityWeeksForWidth(element.clientWidth))
    resize()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(resize)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const calendar = buildActivityCalendar(reviews, new Date(), weekCount)
  const days = calendar.flat()
  const selected = days.find((day) => day.key === selectedDay)
  const active = hovered ?? selected
  const rangeCount = days.reduce((sum, day) => sum + day.count, 0)
  return <Card className="min-w-0 overview-activity-card">
    <CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><CardTitle>Активность проверок</CardTitle><ArcBadge tone="neutral" size="sm">{`${weekCount} нед.`}</ArcBadge></div></CardHeader>
    <CardContent className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><Typography variant="bodySmMedium">Завершено: {count.format(rangeCount)}</Typography><Typography variant="caption" tone="muted" className="overview-activity-readout">{active ? dayDescription(active) : 'История проверок'}</Typography></div>
      <div ref={calendarRef} className="overview-calendar" aria-label={`Календарь активности: ${weekCount} нед.`} style={{ gridTemplateColumns: `1.35rem repeat(${weekCount}, minmax(0, 1fr))` }} onPointerLeave={() => setHovered(undefined)}>
        {['', 'Пн', '', 'Ср', '', 'Пт', '', ''].map((label, i) => <Typography key={i} as="span" variant="caption" tone="muted" aria-hidden="true">{label}</Typography>)}
        {calendar.map((week, index) => <Fragment key={week[0]!.key}>
            <Typography as="span" variant="caption" tone="muted" className="overview-calendar-month">{index === 0 || week[0]!.date.getMonth() !== calendar[index - 1]![0]!.date.getMonth() ? calendarMonth.format(week[0]!.date) : ''}</Typography>
            {week.map((day) => day.future
              ? <span key={day.key} aria-hidden="true" className="overview-day overview-day-future" />
              : day.count > 0
                ? <button key={day.key} type="button" className="overview-day" aria-label={dayDescription(day)} aria-pressed={day.key === selectedDay} data-level={dayLevel(day.count)} data-today={day.today} onPointerEnter={() => setHovered(day)} onFocus={() => setHovered(day)} onBlur={() => setHovered(undefined)} onClick={() => onSelectDay(day.key)} />
                : <span key={day.key} className="overview-day" aria-hidden="true" data-level="0" data-today={day.today} onPointerEnter={() => setHovered(day)} />)}
        </Fragment>)}
      </div>
      <div className="overview-activity-footer"><Typography variant="caption" tone="muted">{calendarDate.format(days[0]!.date)} — {calendarDate.format(days.find((day) => day.today)!.date)}</Typography><div className="overview-activity-legend" aria-label="От 0 до 5 и более проверок в день"><Typography as="span" variant="caption" tone="muted">Меньше</Typography>{[0,1,2,3,4].map(level => <span className="overview-day" data-level={level} key={level} aria-hidden="true" />)}<Typography as="span" variant="caption" tone="muted">Больше</Typography></div></div>
    </CardContent>
  </Card>
}
