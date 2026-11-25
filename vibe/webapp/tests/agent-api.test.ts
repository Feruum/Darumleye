import { expect, test } from 'bun:test'

import { AGENT_DOCUMENT_MAX_BYTES } from '@web-app-demo/contracts'
import { isSupportedDocument, documentSelectionError, documentUploadErrorMessage, startReviewJob, waitForReviewJob, cancelReviewJobWhenAvailable, reviewAgentSpecification } from '../src/features/agent/api'
import { ApiRequestError, type AuthenticatedTransport } from '../src/platform/api'
import { z } from 'zod'

test('accepts PDF and TXT files up to the configured upload limit', () => {
  expect(isSupportedDocument({ name: 'handbook.PDF', size: AGENT_DOCUMENT_MAX_BYTES } as File)).toBe(true)
  expect(isSupportedDocument({ name: 'notes.txt', size: 10 } as File)).toBe(true)
})

test('rejects unsupported extensions and oversized files', () => {
  expect(isSupportedDocument({ name: 'slides.pptx', size: 10 } as File)).toBe(false)
  expect(isSupportedDocument({ name: 'notes.txt', size: AGENT_DOCUMENT_MAX_BYTES + 1 } as File)).toBe(false)
})

test('rejects empty files and filenames without an extension before uploading', () => {
  expect(isSupportedDocument(new File([], 'notes.txt'))).toBe(false)
  expect(isSupportedDocument(new File(['text'], 'pdf'))).toBe(false)
  expect(documentSelectionError(new File([], 'notes.txt'))).toContain('пуст')
  expect(documentSelectionError(new File(['text'], 'notes.exe'))).toContain('PDF или TXT')
})

test('maps malformed responses and rejected uploads to readable recovery messages', () => {
  const invalid = z.string().datetime().safeParse('invalid')
  if (invalid.success) throw new Error('Expected invalid timestamp')
  const message = documentUploadErrorMessage(invalid.error)
  expect(message).toContain('Проверьте список')
  expect(message).not.toContain('invalid_format')
  expect(documentUploadErrorMessage(new ApiRequestError(413, 'PAYLOAD_TOO_LARGE', 'too large'))).toContain('25 МиБ')
  expect(documentUploadErrorMessage(new ApiRequestError(400, 'BAD_REQUEST', 'raw server error'))).not.toContain('raw server error')
})

test('new review job uploads once and resumed polling only fetches the archived report', async () => {
  const id = '0196f6f8-6600-7000-8000-000000000001'
  const job = { id, filename: 'review.txt', started_at: '2026-11-25T12:00:00Z' }
  const report = { filename: job.filename }
  const calls: Array<{ path: string; method?: string; body?: unknown }> = []
  const responses = [{ ...job, status: 'running' }, { ...job, status: 'completed', review_id: id }, { report }]
  const transport = {
    request: async (path: string, _: unknown, options: { method?: string; body?: unknown }) => {
      calls.push({ path, ...options })
      return responses.shift()
    },
  } as unknown as AuthenticatedTransport
  const signal = new AbortController().signal
  await startReviewJob(transport, id, new File(['Private requirements'], job.filename), signal)
  const body = calls[0]?.body as FormData
  expect(body.get('job_id')).toBe(id)
  expect(body.get('review_mode')).toBe('multi_agent')
  expect(await (body.get('file') as File).text()).toBe('Private requirements')
  const result = await waitForReviewJob(transport, id, signal, () => {}, undefined, 0)
  expect(result).toEqual(report)
  expect(calls.map(call => [call.path, call.method ?? 'GET'])).toEqual([
    ['/api/agent/spec-review-jobs', 'POST'],
    [`/api/agent/spec-review-jobs/${id}`, 'GET'],
    [`/api/agent/spec-reviews/${id}`, 'GET'],
  ])
})

test('an accepted review survives more than four transient server restart failures without re-upload', async () => {
  const id = '0196f6f8-6600-7000-8000-000000000001'
  const job = { id, filename: 'review.txt', started_at: '2026-11-25T12:00:00Z' }
  const report = { filename: job.filename }
  let polls = 0
  const transport = {
    request: async (path: string) => {
      if (path.includes('/spec-reviews/')) return { report }
      polls++
      if (polls <= 8) throw polls % 2 ? new ApiRequestError(503, 'REVIEW_FAILED', 'service restarting') : new TypeError('network disconnected')
      return { ...job, status: 'completed', review_id: id }
    },
  } as unknown as AuthenticatedTransport
  const observed: string[] = []
  const result = await waitForReviewJob(transport, id, new AbortController().signal, value => observed.push(value.status), { ...job, status: 'running' }, 0)
  expect(result).toEqual(report)
  expect(polls).toBe(9)
  expect(observed).toEqual(['running', 'completed'])
})

test('a truly missing job does not poll indefinitely', async () => {
  const id = '0196f6f8-6600-7000-8000-000000000001'
  let polls = 0
  const transport = { request: async () => { polls++; throw new ApiRequestError(404, 'NOT_FOUND', 'missing job') } } as unknown as AuthenticatedTransport
  await expect(waitForReviewJob(transport, id, new AbortController().signal, () => {}, undefined, 0)).rejects.toMatchObject({ status: 404 })
  expect(polls).toBe(4)
})

test('explicit cancellation retries a temporarily unavailable server until acknowledged', async () => {
  const id = '0196f6f8-6600-7000-8000-000000000001'
  let attempts = 0
  const transport = { request: async (_path: string, _schema: unknown, options: { method: string }) => {
    expect(options.method).toBe('DELETE')
    if (++attempts <= 6) throw new ApiRequestError(503, 'REVIEW_FAILED', 'service restarting')
    return { id, filename: 'review.txt', started_at: '2026-11-25T12:00:00Z', status: 'cancelled' }
  } } as unknown as AuthenticatedTransport
  await expect(cancelReviewJobWhenAvailable(transport, id, new AbortController().signal, 0)).resolves.toMatchObject({ status: 'cancelled' })
  expect(attempts).toBe(7)
})


test('repeat verification keeps its multi-agent mode and follows the same durable job after a lost upload acknowledgement', async () => {
  let id = ''
  let uploads = 0
  let polls = 0
  const report = { filename: 'revised.txt' }
  const transport = { request: async (path: string, _schema: unknown, options: { method?: string; body?: FormData }) => {
    if (options.method === 'POST') {
      expect(path).toBe('/api/agent/spec-review-jobs')
      expect(await (options.body!.get('file') as File).text()).toBe('Revised requirements')
      expect(options.body!.get('review_mode')).toBe('multi_agent')
      id = String(options.body!.get('job_id'))
      uploads++
      throw new TypeError('connection lost after admission')
    }
    if (path === `/api/agent/spec-reviews/${id}`) return { report }
    expect(path).toBe(`/api/agent/spec-review-jobs/${id}`)
    if (++polls <= 5) throw new ApiRequestError(503, 'AGENT_UNAVAILABLE', 'server restarting')
    return { id, filename: report.filename, started_at: '2026-11-25T12:00:00Z', status: 'completed', review_id: id }
  } } as unknown as AuthenticatedTransport
  const result = await reviewAgentSpecification(transport, new File(['Revised requirements'], report.filename), new AbortController().signal, [], 0, 'multi_agent')
  expect(result).toEqual(report)
  expect(uploads).toBe(1)
  expect(polls).toBe(6)
})
