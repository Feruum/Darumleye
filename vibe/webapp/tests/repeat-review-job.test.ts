import { expect, test } from 'bun:test'
import { ApiRequestError, type AuthenticatedTransport } from '../src/platform/api'
import { createRepeatReviewJobStore, submitRepeatReview, resumeRepeatReview, cancelRepeatReview } from '../src/features/agent/repeat-review-job'

const original = '0196f6f8-6600-7000-8000-000000000001'
const jobId = '0196f6f8-6600-7000-8000-000000000002'
const file = new File(['Revised requirement.'], 'revised.md')
function storage() {
  const entries = new Map<string, string>()
  return { entries, getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => { entries.set(key, value) }, removeItem: (key: string) => { entries.delete(key) } }
}
const job = { id: jobId, filename: file.name, started_at: '2026-11-25T12:00:00Z' }
const report = { filename: file.name }

test('repeat review stores only an opaque UUID per owner and original, and reattaches without uploading again', async () => {
  const local = storage()
  const store = createRepeatReviewJobStore('owner', original, local)
  let uploads = 0
  let available = false
  const transport = { request: async (path: string, _: unknown, options: { method?: string; body?: FormData }) => {
    if (options.method === 'POST') {
      uploads++
      expect(options.body?.get('job_id')).toBe(jobId)
      expect(options.body?.get('review_mode')).toBe('multi_agent')
      throw new TypeError('connection lost after admission')
    }
    if (!available) throw new Error('report unavailable')
    return path.includes('/spec-reviews/') ? { report } : { ...job, status: 'completed', review_id: jobId }
  } } as unknown as AuthenticatedTransport
  await expect(submitRepeatReview(store, transport, jobId, file, new AbortController().signal, [], 'multi_agent', 0)).rejects.toThrow('report unavailable')
  expect(store.read()).toEqual({ id: jobId, cancelling: false })
  expect([...local.entries.values()]).toEqual([jobId])
  expect(createRepeatReviewJobStore('other-owner', original, local).read()).toBeUndefined()
  expect(createRepeatReviewJobStore('owner', jobId, local).read()).toBeUndefined()
  available = true
  expect(await resumeRepeatReview(store, transport, jobId, new AbortController().signal, 0)).toEqual(report)
  expect(uploads).toBe(1)
  expect(store.read()).toBeUndefined()
})

test('repeat review cancellation is persisted through a transient failure and sent to the exact original job', async () => {
  const local = storage()
  const store = createRepeatReviewJobStore('owner', original, local)
  store.remember(jobId)
  let calls = 0
  const transport = { request: async (path: string, _: unknown, options: { method?: string }) => {
    expect(path).toBe(`/api/agent/spec-review-jobs/${jobId}`)
    expect(options.method).toBe('DELETE')
    if (++calls === 1) throw new Error('temporarily unavailable')
    return { ...job, status: 'cancelled' }
  } } as unknown as AuthenticatedTransport
  await expect(cancelRepeatReview(store, transport, jobId, new AbortController().signal, 0)).rejects.toThrow('temporarily unavailable')
  expect(store.read()).toEqual({ id: jobId, cancelling: true })
  await cancelRepeatReview(store, transport, jobId, new AbortController().signal, 0)
  expect(store.read()).toBeUndefined()
})

test('definitive failure clears pending repeat registration so corrected input may be submitted again', async () => {
  const store = createRepeatReviewJobStore('owner', original, storage())
  const transport = { request: async () => { throw new ApiRequestError(422, 'REVIEW_FAILED', 'rejected') } } as unknown as AuthenticatedTransport
  await expect(submitRepeatReview(store, transport, jobId, file, new AbortController().signal, [], 'legacy', 0)).rejects.toThrow()
  expect(store.read()).toBeUndefined()
})

test('a repeat cancellation that loses the race to successful completion returns the archived report', async () => {
  const store = createRepeatReviewJobStore('owner', original, storage())
  store.remember(jobId)
  const transport = { request: async (path: string, _: unknown, options: { method?: string }) => {
    if (options.method === 'DELETE') return { ...job, status: 'completed', review_id: jobId }
    expect(path).toBe(`/api/agent/spec-reviews/${jobId}`)
    return { report }
  } } as unknown as AuthenticatedTransport
  expect(await cancelRepeatReview(store, transport, jobId, new AbortController().signal, 0)).toEqual(report)
  expect(store.read()).toBeUndefined()
})


test.each([undefined, 'legacy', 'multi_agent_direct'] as const)('a repeat of %s submits direct validation', async mode => {
  const store = createRepeatReviewJobStore('owner', original, storage())
  const transport = { request: async (path: string, _: unknown, options: { method?: string; body?: FormData }) => {
    if (options.method === 'POST') {
      expect(options.body?.get('review_mode')).toBe('multi_agent_direct')
      return { ...job, status: 'completed', review_id: jobId }
    }
    expect(path).toBe(`/api/agent/spec-reviews/${jobId}`)
    return { report }
  } } as unknown as AuthenticatedTransport
  expect(await submitRepeatReview(store, transport, jobId, file, new AbortController().signal, [], mode, 0)).toEqual(report)
  expect(store.read()).toBeUndefined()
})
