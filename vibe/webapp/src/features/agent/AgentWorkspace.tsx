import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/features/auth'
import { ArchivedReviewWorkspace } from './ArchivedReviewWorkspace'
import { SpecReviewCard } from './SpecReviewCard'
import type { ReferenceUpload } from './reference-sources'
import { useReviewRun } from './review-run-context'

export function AgentWorkspace() {
  const { user } = useAuth()
  const { snapshot, openNewReview } = useReviewRun()
  const { newReview, reviewId, view } = useSearch({ from: '/userWorkspace/app/assistant' })
  const navigate = useNavigate({ from: '/app/assistant' })
  const [upload, setUpload] = useState<{ ownerId?: string; reviewId: string; file?: File; references: readonly ReferenceUpload[] }>()
  const retained = upload?.ownerId === user?.id && upload?.reviewId === reviewId ? upload : snapshot.status === 'completed' && snapshot.report.passport.run_id === reviewId ? snapshot : undefined

  useEffect(() => {
    if (newReview !== undefined) openNewReview(newReview)
  }, [newReview, openNewReview])

  async function startFix(id: string, file: File | undefined, references: readonly ReferenceUpload[]) {
    setUpload({ ownerId: user?.id, reviewId: id, file, references })
    // Put the archived report in browser history before entering its dedicated fix screen.
    await navigate({ search: { reviewId: id }, replace: true })
    await navigate({ search: { reviewId: id, view: 'fix' } })
  }

  return <div className="sr-page mx-auto grid w-full min-w-0 max-w-[1440px] gap-5 p-4 md:p-8" lang="ru">
    {reviewId ? <ArchivedReviewWorkspace key={`${user?.id}:${reviewId}`} reviewId={reviewId} view={view} sourceFile={retained?.file} references={retained?.references} /> : <SpecReviewCard key={`${user?.id ?? 'guest'}:${newReview ?? 0}`} active onStartFix={(id, file, references) => void startFix(id, file, references)} />}
  </div>
}
