import { createContext, useContext, useSyncExternalStore } from 'react'
import type { ReviewRun } from './review-run'

type ReviewRunContextValue = {
  run: ReviewRun
  submit: () => boolean
  openNewReview: (intent: number) => void
}

export const ReviewRunContext = createContext<ReviewRunContextValue | null>(null)

export function useReviewRun() {
  const context = useContext(ReviewRunContext)
  if (!context) throw new Error('ReviewRunProvider is required in the user workspace')
  const { run } = context
  const snapshot = useSyncExternalStore(run.subscribe, run.getSnapshot, run.getSnapshot)
  return { ...context, snapshot }
}
