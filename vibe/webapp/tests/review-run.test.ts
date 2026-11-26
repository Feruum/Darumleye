import { expect, test } from 'bun:test'
import type { AgentSpecReview } from '@web-app-demo/contracts'
import { clearPendingReviewCancellation, clearPendingReviewJob, createReviewRun, readPendingReviewCancellation, readPendingReviewJob, rememberPendingReviewCancellation, rememberPendingReviewJob } from '../src/features/agent/review-run'

const file = new File(['Тестовое требование'], 'review.txt', { type: 'text/plain' })
const report = { filename: file.name, passport: { run_id: 'test-run' } } as AgentSpecReview

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('leaving the review screen does not abort a running review; its result and original file are retained', async () => {
  const run = createReviewRun()
  const result = deferred<AgentSpecReview>()
  let signal: AbortSignal | undefined
  const notified: AgentSpecReview[] = []
  const unsubscribe = run.subscribe(() => {})
  run.setFile(file)
  expect(run.start((_, abortSignal) => { signal = abortSignal; return result.promise }, { completed: (value) => notified.push(value) })).toBe(true)
  unsubscribe()
  await Promise.resolve()
  expect(signal?.aborted).toBe(false)
  expect(run.reset()).toBe(false)
  expect(run.start(() => result.promise)).toBe(false)
  result.resolve(report)
  await result.promise
  await Promise.resolve()
  expect(run.getSnapshot()).toMatchObject({ status: 'completed', file, report })
  expect(notified).toEqual([report])
})

test('cancelling and starting again cannot replace the newer result with a late response', async () => {
  const run = createReviewRun()
  const first = deferred<AgentSpecReview>()
  const second = deferred<AgentSpecReview>()
  let firstSignal: AbortSignal | undefined
  const notified: AgentSpecReview[] = []
  run.setFile(file)
  run.start((_, signal) => { firstSignal = signal; return first.promise }, { completed: (value) => notified.push(value) })
  await Promise.resolve()
  run.cancel()
  expect(firstSignal?.aborted).toBe(true)
  expect(run.getSnapshot()).toMatchObject({ status: 'cancelled', file })
  run.start(() => second.promise, { completed: (value) => notified.push(value) })
  await Promise.resolve()
  first.resolve({ ...report, filename: 'obsolete.txt' })
  await first.promise
  second.resolve(report)
  await second.promise
  await Promise.resolve()
  expect(run.getSnapshot()).toMatchObject({ status: 'completed', report })
  expect(notified).toEqual([report])
})

test('failed reviews keep input for retry; disposing an account cancels and clears all private input', async () => {
  const run = createReviewRun()
  const rejected = deferred<AgentSpecReview>()
  const error = new Error('provider unavailable')
  const failures: unknown[] = []
  run.setFile(file)
  run.start(() => rejected.promise, { failed: (reason) => failures.push(reason) })
  await Promise.resolve()
  rejected.reject(error)
  await rejected.promise.catch(() => undefined)
  await Promise.resolve()
  expect(run.getSnapshot()).toMatchObject({ status: 'failed', file, error })
  expect(failures).toEqual([error])
  const pending = deferred<AgentSpecReview>()
  let signal: AbortSignal | undefined
  run.start((_, abortSignal) => { signal = abortSignal; return pending.promise })
  await Promise.resolve()
  run.dispose()
  expect(signal?.aborted).toBe(true)
  expect(run.getSnapshot()).toEqual({ status: 'idle', references: [] })
  pending.resolve(report)
  await pending.promise
  await Promise.resolve()
  expect(run.getSnapshot()).toEqual({ status: 'idle', references: [] })
})

test('new-review navigation never interrupts running work and applies a fresh intent only once', async () => {
  const run = createReviewRun()
  run.setFile(file)
  const result = deferred<AgentSpecReview>()
  run.start(() => result.promise)
  await Promise.resolve()
  expect(run.openNewReview(1)).toBe(false)
  result.resolve(report)
  await result.promise
  await Promise.resolve()
  expect(run.openNewReview(1)).toBe(false)
  expect(run.getSnapshot().status).toBe('completed')
  expect(run.openNewReview(2)).toBe(true)
  expect(run.getSnapshot()).toEqual({ status: 'idle', references: [] })
  run.setFile(file)
  expect(run.openNewReview(1)).toBe(false)
  expect(run.getSnapshot().file).toBe(file)
})

test('resume polls an accepted server job without recreating a File and survives disposal without cancelling the server', async () => {
  const result = deferred<AgentSpecReview>()
  const first = createReviewRun()
  let cancelled = 0
  let signal: AbortSignal | undefined
  const job = { id: '0196f6f8-6600-7000-8000-000000000001', filename: 'review.txt', started_at: '2026-11-25T12:00:00Z', status: 'running' } as const
  expect(first.resume((abort, observe) => { signal = abort; observe(job); return result.promise }, { cancelled: () => cancelled++ })).toBe(true)
  expect(first.getSnapshot()).toMatchObject({ status: 'running', filename: job.filename, startedAt: Date.parse(job.started_at) })
  expect(first.getSnapshot().file).toBeUndefined()
  first.dispose()
  expect(signal?.aborted).toBe(true)
  expect(cancelled).toBe(0)

  const reloaded = createReviewRun()
  expect(reloaded.resume((_, observe) => { observe(job); return result.promise })).toBe(true)
  result.resolve(report)
  await result.promise
  await Promise.resolve()
  expect(reloaded.getSnapshot()).toMatchObject({ status: 'completed', filename: job.filename, report })
  expect(reloaded.getSnapshot().file).toBeUndefined()
})

test('explicit cancellation calls the server callback once; resumed jobs cannot replace newer state', () => {
  const run = createReviewRun()
  let cancelled = 0
  run.resume(() => deferred<AgentSpecReview>().promise, { cancelled: () => cancelled++ })
  expect(run.cancel()).toBe(true)
  expect(run.cancel()).toBe(false)
  expect(cancelled).toBe(1)
  expect(run.getSnapshot().status).toBe('cancelled')
})

test('reload marker stores only an opaque UUID scoped to the owner', () => {
  const contents = new Map<string, string>()
  const storage = { getItem: (key: string) => contents.get(key) ?? null, setItem: (key: string, value: string) => { contents.set(key, value) }, removeItem: (key: string) => { contents.delete(key) } }
  const id = '0196f6f8-6600-7000-8000-000000000001'
  const newer = '0196f6f8-6600-7000-8000-000000000002'
  rememberPendingReviewJob('owner', id, storage)
  expect([...contents.values()]).toEqual([id])
  expect(readPendingReviewJob('another', storage)).toBeUndefined()
  expect(readPendingReviewJob('owner', storage)).toBe(id)
  rememberPendingReviewJob('owner', newer, storage)
  clearPendingReviewJob('owner', id, storage)
  expect(readPendingReviewJob('owner', storage)).toBe(newer)
  clearPendingReviewJob('owner', newer, storage)
  expect(contents.size).toBe(0)
})

test('cancellation intent is owner-scoped and survives a reload without retaining the document', () => {
  const contents = new Map<string, string>()
  const storage = { getItem: (key: string) => contents.get(key) ?? null, setItem: (key: string, value: string) => { contents.set(key, value) }, removeItem: (key: string) => { contents.delete(key) } }
  const id = '0196f6f8-6600-7000-8000-000000000001'
  rememberPendingReviewJob('owner', id, storage)
  rememberPendingReviewCancellation('owner', id, storage)
  expect(readPendingReviewCancellation('another', storage)).toBeUndefined()
  expect(readPendingReviewCancellation('owner', storage)).toBe(id)
  expect([...contents.values()]).toEqual([id, id])
  clearPendingReviewCancellation('owner', '0196f6f8-6600-7000-8000-000000000002', storage)
  expect(readPendingReviewCancellation('owner', storage)).toBe(id)
  clearPendingReviewCancellation('owner', id, storage)
  clearPendingReviewJob('owner', id, storage)
  expect(contents.size).toBe(0)
})


test('new reviews default to multi-agent while direct validation is fixed during submission', async () => {
  const run = createReviewRun()
  run.setReviewMode('multi_agent_direct')
  run.setFile(file)
  const result = deferred<AgentSpecReview>()
  let mode: string | undefined
  expect(run.start(input => { mode = input.reviewMode; return result.promise })).toBe(true)
  await Promise.resolve()
  expect(mode).toBe('multi_agent_direct')
  expect(run.setReviewMode('multi_agent')).toBe(false)
  run.cancel()
  expect(run.getSnapshot().reviewMode).toBe('multi_agent_direct')
  expect(run.reset()).toBe(true)
  run.setFile(file)
  run.start(input => { mode = input.reviewMode; return result.promise })
  expect(mode).toBe('multi_agent')
  run.dispose()
})

test('a failed reattachment can retry without an uploaded file or different review mode', async () => {
  const run = createReviewRun()
  const failure = deferred<AgentSpecReview>()
  expect(run.resume(() => failure.promise)).toBe(true)
  failure.reject(new Error('Report temporarily unavailable'))
  await failure.promise.catch(() => undefined)
  await Promise.resolve()
  expect(run.getSnapshot()).toMatchObject({ status: 'failed', references: [] })
  expect(run.getSnapshot().file).toBeUndefined()
  expect(run.resume(async () => report)).toBe(true)
  await Promise.resolve()
  await Promise.resolve()
  expect(run.getSnapshot()).toMatchObject({ status: 'completed', report })
})
