import { ArrowDown01Icon, ArrowLeft01Icon, ArrowRight01Icon, Message01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

import { Alert, AlertDescription } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { useAuth } from '@/features/auth'
import { ApiRequestError } from '@/platform/api'
import { agentQueryKeys, completedReviewQueryOptions } from './queries'
import { downloadReview } from './review-report'
import { recordReview } from './review-overview'
import { canFixReview } from './review-fix'
import { ReviewFixWorkspace } from './ReviewFixWorkspace'
import { SpecReviewReport } from './SpecReviewReport'
import type { ReferenceUpload } from './reference-sources'
import './spec-review.css'

export function ArchivedReviewWorkspace({ reviewId, view, sourceFile, references }: { reviewId: string; view?: 'fix'; sourceFile?: File; references?: readonly ReferenceUpload[] }) {
  const { transport, user } = useAuth()
  const queryClient = useQueryClient()
  const query = useQuery(completedReviewQueryOptions(transport, user?.id, reviewId))
  const detail = query.data
  const missing = query.error instanceof ApiRequestError && query.error.status === 404
  const showFix = view === 'fix'
  const canFix = Boolean(detail && canFixReview(detail.report, detail.id))
  const heading = useRef<HTMLDivElement>(null)
  useEffect(() => {
    heading.current?.focus({ preventScroll: true })
    heading.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
  }, [showFix])

  return <section className="sr-workspace sr-workflow" aria-label={showFix ? 'Доработка ТЗ' : 'Сохранённая проверка'}>
    <header className={showFix ? 'sr-heading' : 'sr-heading sr-reader-topbar'}>
      <div className="grid gap-2 min-w-0">
        <Button asChild variant="ghost" size="sm" className="w-fit">{showFix
          ? <Link to="/app/assistant" search={{ reviewId }}><HugeiconsIcon icon={ArrowLeft01Icon} size={17} aria-hidden />Назад к отчёту</Link>
          : <Link to="/app"><HugeiconsIcon icon={ArrowLeft01Icon} size={17} aria-hidden />К обзору</Link>}</Button>
        <div ref={heading} tabIndex={-1} className="sr-page-title"><Typography as="h1" variant="h1">{showFix ? 'Доработка ТЗ' : detail?.report.filename ?? 'Результат проверки'}</Typography></div>
        {showFix && detail && <Typography as="p" variant="bodySm" tone="muted">{detail.report.filename}</Typography>}
      </div>
      {detail && !showFix && <div className="sr-heading-actions">
        <Button asChild variant="outline"><Link to="/app/chat" search={{ reviewId: detail.id }}><HugeiconsIcon icon={Message01Icon} size={17} aria-hidden />Обсудить замечания</Link></Button>
        <Button variant="outline" onClick={() => downloadReview(detail.report)} type="button"><HugeiconsIcon icon={ArrowDown01Icon} size={17} aria-hidden />Скачать отчёт</Button>
        {canFix && <Button asChild className="sr-primary sr-press"><Link to="/app/assistant" search={{ reviewId: detail.id, view: 'fix' }}>Доработать ТЗ <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Link></Button>}
      </div>}
    </header>
    {query.isPending ? <div className="sr-panel sr-example-loading" role="status"><Typography variant="bodySm">Открываем отчёт…</Typography></div> : query.isError ? <Alert variant="destructive"><AlertDescription>{missing ? 'Отчёт не найден.' : 'Не удалось загрузить отчёт.'}</AlertDescription>{!missing && <Button variant="ghost" type="button" onClick={() => void query.refetch()}>Повторить</Button>}</Alert> : detail ? <div key={detail.id}>
      {!showFix && <SpecReviewReport report={detail.report} sourceFile={sourceFile} references={references} sourceUnits={detail.source_units} />}
      {canFix && <div hidden={!showFix}>
        <ReviewFixWorkspace active={showFix} report={detail.report} transport={transport} ownerId={user?.id} archivedId={detail.id} references={references} onVerified={report => {
          if (user?.id) {
            recordReview(queryClient, user.id, report)
            void queryClient.invalidateQueries({ queryKey: agentQueryKeys.completedReviews(user.id) })
          }
        }} />
      </div>}
      {showFix && !canFix && <Alert><AlertDescription>Для этого отчёта нет доступных правок. Вернитесь к результатам проверки.</AlertDescription></Alert>}
    </div> : null}
  </section>
}
