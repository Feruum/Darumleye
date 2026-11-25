import type { AgentReviewMode } from '@web-app-demo/contracts'
import { ApiRequestError, type AuthenticatedTransport } from '@/platform/api'
import { cancelReviewJobWhenAvailable, reviewAgentSpecification, waitForReviewJob } from './api'
import type { ReferenceUpload } from './reference-sources'

export type RepeatReviewJob = { id: string; cancelling: boolean }
type JobStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const validId = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i

// Persist only the opaque job UUID, not the draft, its source files or the
// report. Scope it to both the authenticated owner and original archive ID.
export function createRepeatReviewJobStore(ownerId?: string, originalId?: string | null, storage?: JobStorage) {
  const prefix = ownerId && originalId && validId.test(originalId) ? `agent:repeat-review:${encodeURIComponent(ownerId)}:${originalId}` : undefined
  const get = (key: string) => { try { return prefix ? (storage ?? globalThis.sessionStorage).getItem(`${prefix}:${key}`) : null } catch { return null } }
  const set = (key: string, id: string) => { try { if (prefix && validId.test(id)) (storage ?? globalThis.sessionStorage).setItem(`${prefix}:${key}`, id) } catch { /* storage may be disabled */ } }
  const remove = (key: string, id: string) => { try { if (prefix && get(key) === id) (storage ?? globalThis.sessionStorage).removeItem(`${prefix}:${key}`) } catch { /* storage may be disabled */ } }
  let inMemory: RepeatReviewJob | undefined
  return {
    read(): RepeatReviewJob | undefined {
      const cancelled = get('cancel')
      const id = get('job')
      if (cancelled && validId.test(cancelled)) return { id: cancelled, cancelling: true }
      if (id && validId.test(id)) return { id, cancelling: false }
      return inMemory
    },
    remember(id: string) { if (validId.test(id)) { inMemory = { id, cancelling: false }; set('job', id) } },
    requestCancellation(id: string) { if (validId.test(id)) { inMemory = { id, cancelling: true }; set('cancel', id) } },
    clear(id: string) { if (inMemory?.id === id) inMemory = undefined; remove('cancel', id); remove('job', id) },
  }
}
export type RepeatReviewJobStore = ReturnType<typeof createRepeatReviewJobStore>

async function observeRepeat(store: RepeatReviewJobStore, id: string, operation: () => ReturnType<typeof waitForReviewJob>) {
  try {
    const result = await operation()
    // If cancellation replaced this poll, let that operation settle the UUID.
    if (!store.read()?.cancelling) store.clear(id)
    return result
  } catch (error) {
    if (!store.read()?.cancelling && error instanceof ApiRequestError && error.status < 500) store.clear(id)
    throw error
  }
}

export function submitRepeatReview(store: RepeatReviewJobStore, transport: AuthenticatedTransport, id: string, file: File, signal: AbortSignal, references: readonly ReferenceUpload[], mode: AgentReviewMode | undefined, interval = 1500) {
  store.remember(id)
  return observeRepeat(store, id, () => reviewAgentSpecification(transport, file, signal, references, interval, mode ?? 'legacy', id))
}

export function resumeRepeatReview(store: RepeatReviewJobStore, transport: AuthenticatedTransport, id: string, signal: AbortSignal, interval = 1500) {
  return observeRepeat(store, id, () => waitForReviewJob(transport, id, signal, () => {}, undefined, interval))
}

export async function cancelRepeatReview(store: RepeatReviewJobStore, transport: AuthenticatedTransport, id: string, signal: AbortSignal, interval = 1500) {
  store.requestCancellation(id)
  const result = await cancelReviewJobWhenAvailable(transport, id, signal, interval)
  // Cancellation may race with completion; recover the authoritative archive.
  const report = result?.status === 'completed' ? await waitForReviewJob(transport, id, signal, () => {}, result, interval) : undefined
  store.clear(id)
  if (result?.status === 'failed') throw new ApiRequestError(422, result.error.code, result.error.message)
  return report
}
