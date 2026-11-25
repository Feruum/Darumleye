import { AGENT_DEFAULT_REVIEW_MODE, type AgentReviewJob, type AgentReviewMode, type AgentSpecReview } from '@web-app-demo/contracts'
import type { ReferenceDraft } from './reference-sources'

type ReviewDraft = { file?: File; filename?: string; references: ReferenceDraft[]; reviewMode?: AgentReviewMode }
type SubmittedReview = { file: File; references: ReferenceDraft[]; startedAt: number; reviewMode: AgentReviewMode }
type ObservedReview = ReviewDraft & { startedAt: number }

export type ReviewRunSnapshot = (ReviewDraft & { status: 'idle' | 'cancelled' })
  | (ObservedReview & { status: 'running' })
  | (ObservedReview & { status: 'completed'; report: AgentSpecReview })
  | (ObservedReview & { status: 'failed'; error: unknown })

type ObserveJob = (job: AgentReviewJob) => void
type ReviewRequest = (input: SubmittedReview, signal: AbortSignal, observe: ObserveJob) => Promise<AgentSpecReview | undefined>
type ResumeRequest = (signal: AbortSignal, observe: ObserveJob) => Promise<AgentSpecReview | undefined>
type ReviewCallbacks = { completed?: (report: AgentSpecReview) => void; failed?: (error: unknown) => void; cancelled?: () => void }

type PendingJobStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const pendingJobKey = (ownerId: string) => `agent:review-job:${ownerId}`
const pendingCancellationKey = (ownerId: string) => `agent:review-job-cancellation:${ownerId}`
const jobUuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i

// Only the opaque job ID survives a reload. Files, filenames, references and
// report text remain in memory or the owner-scoped server archive.
export function readPendingReviewJob(ownerId: string, storage?: PendingJobStorage) {
  try {
    const value = (storage ?? globalThis.sessionStorage).getItem(pendingJobKey(ownerId))
    return value && jobUuid.test(value) ? value : undefined
  } catch { return undefined }
}

export function rememberPendingReviewJob(ownerId: string, id: string, storage?: PendingJobStorage) {
  if (!jobUuid.test(id)) return
  try { (storage ?? globalThis.sessionStorage).setItem(pendingJobKey(ownerId), id) } catch { /* storage may be disabled */ }
}

export function clearPendingReviewJob(ownerId: string, id: string, storage?: PendingJobStorage) {
  try {
    const target = storage ?? globalThis.sessionStorage
    if (target.getItem(pendingJobKey(ownerId)) === id) target.removeItem(pendingJobKey(ownerId))
  } catch { /* storage may be disabled */ }
}

export function readPendingReviewCancellation(ownerId: string, storage?: PendingJobStorage) {
  try {
    const value = (storage ?? globalThis.sessionStorage).getItem(pendingCancellationKey(ownerId))
    return value && jobUuid.test(value) ? value : undefined
  } catch { return undefined }
}

export function rememberPendingReviewCancellation(ownerId: string, id: string, storage?: PendingJobStorage) {
  if (!jobUuid.test(id)) return
  try { (storage ?? globalThis.sessionStorage).setItem(pendingCancellationKey(ownerId), id) } catch { /* storage may be disabled */ }
}

export function clearPendingReviewCancellation(ownerId: string, id: string, storage?: PendingJobStorage) {
  try {
    const target = storage ?? globalThis.sessionStorage
    if (target.getItem(pendingCancellationKey(ownerId)) === id) target.removeItem(pendingCancellationKey(ownerId))
  } catch { /* storage may be disabled */ }
}

export function createReviewRun() {
  let snapshot: ReviewRunSnapshot = { status: 'idle', references: [] }
  let active: AbortController | undefined
  let cancelActive: (() => void) | undefined
  let lastNewReview: number | undefined
  const listeners = new Set<() => void>()

  function publish(next: ReviewRunSnapshot) {
    snapshot = next
    for (const listener of listeners) listener()
  }

  function reset() {
    if (active) return false
    publish({ status: 'idle', references: [] })
    return true
  }

  function launch(input: ObservedReview, request: ResumeRequest, callbacks: ReviewCallbacks) {
    const controller = new AbortController()
    active = controller
    cancelActive = callbacks.cancelled
    let observation = input
    publish({ ...observation, status: 'running' })
    void (async () => {
      let report: AgentSpecReview | undefined
      try {
        report = await request(controller.signal, (job) => {
          if (active !== controller) return
          observation = { ...observation, filename: job.filename, startedAt: Date.parse(job.started_at) }
          publish({ ...observation, status: 'running' })
        })
      } catch (error) {
        if (active !== controller) return
        active = undefined
        cancelActive = undefined
        publish({ ...observation, status: 'failed', error })
        callbacks.failed?.(error)
        return
      }
      if (active !== controller) return
      active = undefined
      cancelActive = undefined
      if (!report) {
        publish({ status: 'cancelled', file: observation.file, references: observation.references, reviewMode: observation.reviewMode })
        return
      }
      publish({ ...observation, status: 'completed', report })
      callbacks.completed?.(report)
    })()
    return true
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    setFile(file?: File) {
      if (active) return false
      publish({ status: 'idle', file, references: snapshot.references, reviewMode: snapshot.reviewMode })
      return true
    },
    setReferences(references: ReferenceDraft[]) {
      if (active) return false
      publish({ status: 'idle', file: snapshot.file, references: [...references], reviewMode: snapshot.reviewMode })
      return true
    },
    setReviewMode(reviewMode: AgentReviewMode) {
      if (active) return false
      publish({ status: 'idle', file: snapshot.file, references: snapshot.references, reviewMode })
      return true
    },
    reset,
    openNewReview(intent: number) {
      if (lastNewReview !== undefined && intent <= lastNewReview) return false
      lastNewReview = intent
      return reset()
    },
    start(request: ReviewRequest, callbacks: ReviewCallbacks = {}) {
      if (active || !snapshot.file) return false
      const input: SubmittedReview = { file: snapshot.file, references: [...snapshot.references], reviewMode: snapshot.reviewMode ?? AGENT_DEFAULT_REVIEW_MODE, startedAt: Date.now() }
      return launch(input, (signal, observe) => request(input, signal, observe), callbacks)
    },
    resume(request: ResumeRequest, callbacks: ReviewCallbacks = {}) {
      if (active || (snapshot.status !== 'idle' && snapshot.status !== 'failed')) return false
      const input: ObservedReview = snapshot.status === 'failed' ? { file: snapshot.file, filename: snapshot.filename, references: snapshot.references, reviewMode: snapshot.reviewMode, startedAt: snapshot.startedAt } : { references: [], startedAt: Date.now() }
      return launch(input, request, callbacks)
    },
    cancel() {
      if (!active) return false
      const controller = active
      active = undefined
      controller.abort()
      const cancel = cancelActive
      cancelActive = undefined
      cancel?.()
      publish({ status: 'cancelled', file: snapshot.file, references: snapshot.references, reviewMode: snapshot.reviewMode })
      return true
    },
    dispose() {
      const controller = active
      active = undefined
      cancelActive = undefined
      controller?.abort()
      lastNewReview = undefined
      publish({ status: 'idle', references: [] })
    },
  }
}

export type ReviewRun = ReturnType<typeof createReviewRun>
