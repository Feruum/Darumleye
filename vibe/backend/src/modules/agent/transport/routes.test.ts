import { expect, spyOn, test } from 'bun:test'
import { Hono } from 'hono'

import { AGENT_DOCUMENT_MAX_BYTES, agentDocumentSchema, agentDocumentsResponseSchema } from '@web-app-demo/contracts'
import { loadEnv } from '../../../env'
import { handleError } from '../../../http/errors'
import { AuthFailure, createRequireAuth } from '../../auth'
import { createAgentRoutes } from './routes'

const env = loadEnv({
  DATABASE_URL: 'postgresql://superuser:superpassword@localhost:54329/web_app_demo',
  JWT_SECRET: '12345678901234567890123456789012',
  AGENT_SERVICE_URL: 'http://127.0.0.1:8001',
  AGENT_API_KEY: 'internal-test-key',
})

const user = {
  id: '0196f6f8-6600-7000-8000-000000000001',
  email: 'user@example.com',
  displayName: 'User',
  role: 'user' as const,
  createdAt: '2026-01-01T00:00:00.000Z',
  sessionId: 'session-1',
}

function createTestApp(fetchAgent: typeof fetch = fetch) {
  const requireAuth = createRequireAuth(async (accessToken) => {
    if (accessToken !== 'valid-session-token') {
      throw new AuthFailure('access_token_required', 'A valid session is required')
    }
    return user
  })
  const app = new Hono()
  app.route('/api/agent', createAgentRoutes({ env, fetchAgent, requireAuth }))
  app.onError(handleError)
  return app
}

test('memory proxy requires a session and forwards only explicit writes with trusted identity', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ memories: [] })
  })
  expect((await app.request('/api/agent/memories')).status).toBe(401)
  expect(calls).toHaveLength(0)
  const headers = { Authorization: 'Bearer valid-session-token', 'Content-Type': 'application/json', 'X-User-ID': 'forged-user' }
  expect((await app.request('/api/agent/memories', { headers })).status).toBe(200)
  expect((await app.request('/api/agent/memories', { method: 'POST', headers, body: JSON.stringify({ content: 'private', user_id: 'forged-user' }) })).status).toBe(400)
  expect(calls).toHaveLength(1)
  expect((await app.request('/api/agent/memories', { method: 'POST', headers, body: JSON.stringify({ content: '  Prefer Russian.  ' }) })).status).toBe(200)
  expect(calls[1]!.url).toBe('http://127.0.0.1:8001/api/v1/memories')
  expect(JSON.parse(String(calls[1]!.init?.body))).toEqual({ content: 'Prefer Russian.' })
  expect(new Headers(calls[1]!.init?.headers).get('X-User-ID')).toBe(user.id)
  expect((await app.request('/api/agent/memories/0196f6f8-6600-7000-8000-000000000001', { method: 'DELETE', headers })).status).toBe(200)
  expect(calls[2]!.init?.method).toBe('DELETE')
})

test('chat proxy requires a session and forwards only the verified user identity', async () => {
  const calls: Array<{ url: RequestInfo | URL; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url, init })
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('event: token\ndata: {"content":"hello"}\n\n'))
          controller.close()
        },
      }),
      { headers: { 'content-type': 'text/event-stream' } },
    )
  })

  const unauthorized = await app.request('/api/agent/modules')
  expect(unauthorized.status).toBe(401)
  expect(calls).toHaveLength(0)

  const response = await app.request('/api/agent/chat/stream', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer valid-session-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages: [{ type: 'human', content: 'Explain this PDF' }],
      thread_id: 'thread-1',
      module_id: 'rag',
    }),
  })

  expect(response.status).toBe(200)
  expect(response.headers.get('content-type')).toContain('text/event-stream')
  expect(await response.text()).toContain('"content":"hello"')

  const { url, init } = calls[0]!
  const headers = new Headers(init?.headers)
  expect(String(url)).toBe('http://127.0.0.1:8001/api/v1/chat/stream')
  expect(headers.get('X-API-Key')).toBe('internal-test-key')
  expect(headers.get('X-User-ID')).toBe(user.id)
  expect(JSON.parse(String(init?.body))).toMatchObject({ module_id: 'rag', thread_id: 'thread-1' })
})

test('unknown module IDs become a client error from the agent service', async () => {
  let calls = 0
  const app = createTestApp(async () => {
    calls += 1
    return Response.json({ detail: { code: 'UNKNOWN_MODULE' } }, { status: 422 })
  })
  const response = await app.request('/api/agent/chat/stream', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer valid-session-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages: [{ type: 'human', content: 'Hello' }],
      thread_id: 'thread-1',
      module_id: 'unknown-module',
    }),
  })

  expect(response.status).toBe(400)
  expect(calls).toBe(1)
  expect(await response.json()).toMatchObject({ error: { code: 'BAD_REQUEST' } })
})

test('agent service connection failures become a stable 503 response', async () => {
  const app = createTestApp(async () => {
    throw new Error('connection refused')
  })
  const errorLog = spyOn(console, 'error').mockImplementation(() => {})

  try {
    const response = await app.request('/api/agent/modules', {
      headers: { Authorization: 'Bearer valid-session-token' },
    })

    expect(response.status).toBe(503)
    expect(await response.json()).toMatchObject({ error: { code: 'AGENT_UNAVAILABLE' } })
  } finally {
    errorLog.mockRestore()
  }
})

test('an unconfigured LLM stays distinguishable from an unavailable agent service', async () => {
  const app = createTestApp(async () => Response.json({
    error: {
      code: 'LLM_NOT_CONFIGURED',
      message: 'Configure the chat provider to enable agent responses',
    },
  }, { status: 503 }))
  const response = await app.request('/api/agent/chat/stream', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer valid-session-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages: [{ type: 'human', content: 'Answer from my file' }],
      module_id: 'rag',
    }),
  })

  expect(response.status).toBe(503)
  expect(await response.json()).toMatchObject({
    error: { code: 'LLM_NOT_CONFIGURED' },
  })
})

test('document uploads reject empty, extensionless, unsupported and oversized files', async () => {
  let calls = 0
  const app = createTestApp(async () => {
    calls += 1
    return Response.json({})
  })
  const authorization = { Authorization: 'Bearer valid-session-token' }
  const unsupported = new FormData()
  unsupported.set('file', new File(['not a supported document'], 'notes.docx', { type: 'text/plain' }))

  const unsupportedResponse = await app.request('/api/agent/documents', {
    method: 'POST',
    headers: authorization,
    body: unsupported,
  })
  expect(unsupportedResponse.status).toBe(400)

  for (const file of [new File([], 'empty.txt'), new File(['text'], 'txt')]) {
    const body = new FormData()
    body.set('file', file)
    const response = await app.request('/api/agent/documents', { method: 'POST', headers: authorization, body })
    expect(response.status).toBe(400)
  }

  const oversized = new FormData()
  oversized.set('file', new File([new Uint8Array(AGENT_DOCUMENT_MAX_BYTES + 1)], 'large.txt', { type: 'text/plain' }))
  const oversizedResponse = await app.request('/api/agent/documents', {
    method: 'POST',
    headers: authorization,
    body: oversized,
  })
  expect(oversizedResponse.status).toBe(413)
  expect(calls).toBe(0)
})

test('document upload and list accept the real FastAPI datetime format', async () => {
  const document = {
    id: '0196f6f8-6600-7000-8000-000000000001', job_id: '0196f6f8-6600-7000-8000-000000000002',
    filename: 'notes.txt', status: 'QUEUED', content_hash: 'test', progress: 0,
    created_at: '2026-11-25T12:00:00.123456+00:00', updated_at: '2026-11-25T12:00:00.123456+00:00',
    error_code: null, error_message: null,
  }
  const app = createTestApp(async (url) => String(url).endsWith('/admin/ingest')
    ? Response.json(document, { status: 202 })
    : Response.json({ documents: [document] }))
  const body = new FormData()
  body.set('file', new File(['hello'], 'notes.txt', { type: 'text/plain' }))
  const headers = { Authorization: 'Bearer valid-session-token' }
  const uploaded = await app.request('/api/agent/documents', { method: 'POST', headers, body })
  expect(uploaded.status).toBe(202)
  expect(agentDocumentSchema.parse(await uploaded.json())).toEqual(document)
  const listed = await app.request('/api/agent/documents', { headers })
  expect(agentDocumentsResponseSchema.parse(await listed.json())).toEqual({ documents: [document] })
})

test('completed reviews list forwards the authenticated owner and review chat forwards the chosen ID', async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ path: String(url), init })
    return String(url).endsWith('/spec-reviews') ? Response.json({ reviews: [] })
      : new Response('event: end\ndata: {}\n\n', { headers: { 'content-type': 'text/event-stream' } })
  })
  const headers = { Authorization: 'Bearer valid-session-token', 'X-User-ID': 'forged', 'Content-Type': 'application/json' }
  expect((await app.request('/api/agent/spec-reviews')).status).toBe(401)
  const listing = await app.request('/api/agent/spec-reviews', { headers })
  expect(listing.status).toBe(200)
  expect(listing.headers.get('cache-control')).toBe('no-store')
  expect(calls[0]?.path).toEndWith('/api/v1/spec-reviews')
  expect(new Headers(calls[0]?.init?.headers).get('X-User-ID')).toBe(user.id)
  const review_id = '0196f6f8-6600-7000-8000-000000000001'
  const body = { module_id: 'review_chat', review_id, messages: [{ type: 'human', content: 'Explain the report' }] }
  const chat = await app.request('/api/agent/chat/stream', { method: 'POST', headers, body: JSON.stringify(body) })
  expect(chat.status).toBe(200)
  expect(JSON.parse(String(calls[1]?.init?.body))).toEqual(body)
  expect(new Headers(calls[1]?.init?.headers).get('X-User-ID')).toBe(user.id)
})

test('specification review validates the session and forwards the default mode and selected file', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ filename: 'payment.txt', source: 'txt', unreadable_pages: [], summary: 'Review', findings: [] })
  })
  const body = new FormData()
  body.set('file', new File(['Retry after a timeout.'], 'payment.txt', { type: 'text/plain' }))
  body.set('user_id', 'forged-user')
  const path = '/api/agent/spec-reviews'

  expect((await app.request(path, { method: 'POST', body })).status).toBe(401)
  expect(calls).toHaveLength(0)
  const response = await app.request(path, {
    method: 'POST',
    headers: { Authorization: 'Bearer valid-session-token', 'X-User-ID': 'forged-user' },
    body,
  })

  expect(response.status).toBe(200)
  expect(await response.json()).toMatchObject({ filename: 'payment.txt', findings: [] })
  expect(calls).toHaveLength(1)
  expect(calls[0]!.url).toBe('http://127.0.0.1:8001/api/v1/spec-reviews')
  expect(new Headers(calls[0]!.init?.headers).get('X-User-ID')).toBe(user.id)
  const forwarded = calls[0]!.init?.body as FormData
  expect([...forwarded.keys()]).toEqual(['file', 'review_mode'])
  expect(forwarded.get('review_mode')).toBe('multi_agent')
  expect(await (forwarded.get('file') as File).text()).toBe('Retry after a timeout.')
})

test('specification review preserves a recoverable document error', async () => {
  const app = createTestApp(async () => Response.json({
    detail: { code: 'REVIEW_NO_TEXT', message: 'No readable text' },
  }, { status: 422 }))
  const body = new FormData()
  body.set('file', new File([' '], 'scanned.pdf', { type: 'application/pdf' }))
  const response = await app.request('/api/agent/spec-reviews', {
    method: 'POST',
    headers: { Authorization: 'Bearer valid-session-token' },
    body,
  })

  expect(response.status).toBe(422)
  expect(await response.json()).toMatchObject({ error: { code: 'REVIEW_NO_TEXT' } })
})

test('review mode is validated and forwarded only for specification reviews', async () => {
  const requests: FormData[] = []
  const app = createTestApp(async (_url, init) => {
    requests.push(init?.body as FormData)
    return Response.json({})
  })
  const headers = { Authorization: 'Bearer valid-session-token' }
  const upload = (mode: string) => {
    const form = new FormData()
    form.set('file', new File(['REQ-1'], 'requirements.txt', { type: 'text/plain' }))
    form.set('review_mode', mode)
    return form
  }
  expect((await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body: upload('multi_agent') })).status).toBe(200)
  expect(requests[0]?.get('review_mode')).toBe('multi_agent')
  const job = upload('multi_agent')
  job.set('job_id', '0196f6f8-6600-7000-8000-000000000001')
  expect((await app.request('/api/agent/spec-review-jobs', { method: 'POST', headers, body: job })).status).toBe(200)
  expect(requests[1]?.get('review_mode')).toBe('multi_agent')
  expect((await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body: upload('legacy') })).status).toBe(200)
  expect(requests[2]?.get('review_mode')).toBe('legacy')
  const legacyJob = upload('legacy')
  legacyJob.set('job_id', '0196f6f8-6600-7000-8000-000000000001')
  expect((await app.request('/api/agent/spec-review-jobs', { method: 'POST', headers, body: legacyJob })).status).toBe(200)
  expect(requests[3]?.get('review_mode')).toBe('legacy')
  expect((await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body: upload('invalid') })).status).toBe(400)
  const duplicate = upload('legacy')
  duplicate.append('review_mode', 'multi_agent')
  expect((await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body: duplicate })).status).toBe(400)
  expect(requests).toHaveLength(4)
})

test('review jobs relay only authenticated upload and job ID, then poll and cancel by UUID', async () => {
  const jobId = '0196f6f8-6600-7000-8000-000000000001'
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ path: String(url), init })
    return Response.json({ id: jobId, filename: 'review.txt', started_at: '2026-11-25T12:00:00Z', status: init?.method === 'DELETE' ? 'cancelled' : 'running' }, { status: init?.method === 'POST' ? 202 : 200 })
  })
  const headers = { Authorization: 'Bearer valid-session-token', 'X-User-ID': 'forged' }
  const path = '/api/agent/spec-review-jobs'
  const body = new FormData()
  body.set('file', new File(['A requirement'], 'review.txt', { type: 'text/plain' }))
  body.set('job_id', jobId)
  body.set('owner_id', 'forged')
  expect((await app.request(path, { method: 'POST', body })).status).toBe(401)
  const accepted = await app.request(path, { method: 'POST', headers, body })
  expect(accepted.status).toBe(202)
  expect(accepted.headers.get('cache-control')).toBe('no-store')
  expect(calls[0]?.path).toEndWith('/api/v1/spec-review-jobs')
  expect([...((calls[0]?.init?.body as FormData).keys())].sort()).toEqual(['file', 'job_id', 'review_mode'])
  expect((calls[0]?.init?.body as FormData).get('review_mode')).toBe('multi_agent')
  expect((calls[0]?.init?.body as FormData).get('job_id')).toBe(jobId)
  expect(new Headers(calls[0]?.init?.headers).get('X-User-ID')).toBe(user.id)
  expect((await app.request(`${path}/${jobId}`, { headers })).status).toBe(200)
  expect((await app.request(`${path}/${jobId}`, { method: 'DELETE', headers })).status).toBe(200)
  expect(calls[1]?.path).toEndWith(`/api/v1/spec-review-jobs/${jobId}`)
  expect(calls[2]?.init?.method).toBe('DELETE')
  expect((await app.request(`${path}/invalid`, { headers })).status).toBe(400)
})

test('Markdown is accepted for specification review but not knowledge-base uploads', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ filename: 'requirements.MD', source: 'md' })
  })
  const body = new FormData()
  body.set('file', new File(['# Требования\nФ-1. Уведомление'], 'requirements.MD', { type: 'text/markdown' }))
  const headers = { Authorization: 'Bearer valid-session-token' }

  const ragResponse = await app.request('/api/agent/documents', { method: 'POST', headers, body })
  expect(ragResponse.status).toBe(400)
  expect(calls).toHaveLength(0)

  const reviewResponse = await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body })
  expect(reviewResponse.status).toBe(200)
  expect(calls).toHaveLength(1)
  expect(calls[0]!.url).toBe('http://127.0.0.1:8001/api/v1/spec-reviews')
  const file = (calls[0]!.init?.body as FormData).get('file') as File
  expect(file.name).toBe('requirements.MD')
  expect(await file.text()).toBe('# Требования\nФ-1. Уведомление')
})

test('Office, CSV and photo uploads reach the reviewer while knowledge-base formats remain unchanged', async () => {
  const forwarded: string[] = []
  const app = createTestApp(async (_url, init) => {
    forwarded.push(((init?.body as FormData).get('file') as File).name)
    return Response.json({})
  })
  const headers = { Authorization: 'Bearer valid-session-token' }
  for (const extension of ['docx', 'xlsx', 'csv', 'jpg', 'jpeg', 'png']) {
    const body = new FormData()
    body.set('file', new File(['fixture'], `requirements.${extension}`))
    expect((await app.request('/api/agent/documents', { method: 'POST', headers, body })).status).toBe(400)
    expect((await app.request('/api/agent/spec-reviews', { method: 'POST', headers, body })).status).toBe(200)
  }
  expect(forwarded).toEqual(['requirements.docx', 'requirements.xlsx', 'requirements.csv', 'requirements.jpg', 'requirements.jpeg', 'requirements.png'])
})

const reviewPath = '/api/agent/spec-reviews'
const reviewAuth = { Authorization: 'Bearer valid-session-token' }
const policyMetadata = {
  category: 'bank_policy', title: 'Internal policy', issuer: 'Owner', version: '1.0', scope: 'Payments', source_url: null,
}

function referenceUpload(files: (File | string)[], manifest: unknown = files.map(() => policyMetadata)) {
  const body = new FormData()
  body.set('file', new File(['Payment requirements.'], 'requirements.md', { type: 'text/markdown' }))
  for (const reference of files) body.append('reference_files', reference)
  if (manifest !== undefined) body.set('reference_manifest', JSON.stringify(manifest))
  return body
}

test('reference review forwards selected files and matching, normalized metadata in order with trusted identity', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ reference_review: { status: 'completed' } })
  })
  const body = referenceUpload([
    new File(['Policy rules.'], 'bank.txt', { type: 'text/plain' }),
    new File(['API rules'], 'API.MD', { type: 'text/markdown' }),
  ], [{ ...policyMetadata, title: ' Internal policy ' }, { ...policyMetadata, category: 'api_testing' }])
  body.set('user_id', 'forged-user')
  expect((await app.request(reviewPath, { method: 'POST', headers: reviewAuth, body })).status).toBe(200)
  expect(calls).toHaveLength(1)
  const forwarded = calls[0]!.init?.body as FormData
  expect([...forwarded.keys()]).toEqual(['file', 'reference_files', 'reference_files', 'reference_manifest', 'review_mode'])
  expect((forwarded.getAll('reference_files') as File[]).map(file => file.name)).toEqual(['bank.txt', 'API.MD'])
  expect(JSON.parse(String(forwarded.get('reference_manifest')))).toEqual([policyMetadata, { ...policyMetadata, category: 'api_testing' }])
  expect(new Headers(calls[0]!.init?.headers).get('X-User-ID')).toBe(user.id)
})

test('reference review rejects invalid metadata, file types, count or mismatched documents before proxying', async () => {
  let calls = 0
  const app = createTestApp(async () => { calls++; return Response.json({}) })
  const valid = new File(['Rules'], 'rules.txt', { type: 'text/plain' })
  const missing = referenceUpload([valid])
  missing.delete('reference_manifest')
  const malformed = referenceUpload([valid])
  malformed.set('reference_manifest', 'not-json')
  const repeated = referenceUpload([valid])
  repeated.append('reference_manifest', JSON.stringify([policyMetadata]))
  const invalid = [
    missing, malformed, repeated,
    referenceUpload([valid], []), referenceUpload([], [policyMetadata]),
    referenceUpload([new File(['Rules'], 'rules.pptx')]),
    referenceUpload(['not a file']),
    referenceUpload([new File([], 'empty.md')]),
    referenceUpload(Array.from({ length: 9 }, () => valid)),
    referenceUpload([valid], [{ ...policyMetadata, scope: ' ' }]),
    referenceUpload([valid], [{ ...policyMetadata, source_url: 'file:///tmp/internal.txt' }]),
  ]
  for (const body of invalid) expect((await app.request(reviewPath, { method: 'POST', headers: reviewAuth, body })).status).toBe(400)
  expect(calls).toBe(0)
})

test('reference review enforces individual and cumulative limits independently of the specification limit', async () => {
  let calls = 0
  const app = createTestApp(async () => { calls++; return Response.json({}) })
  const oversized = referenceUpload([new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'oversized.pdf')])
  const excessiveTotal = referenceUpload(Array.from({ length: 3 }, (_, index) => new File([new Uint8Array(4 * 1024 * 1024)], `policy-${index}.pdf`)))
  for (const body of [oversized, excessiveTotal]) {
    expect((await app.request(reviewPath, { method: 'POST', headers: reviewAuth, body })).status).toBe(413)
  }
  expect(calls).toBe(0)
  const legitimate = referenceUpload([new File([new Uint8Array(4 * 1024 * 1024)], 'policy.pdf')])
  legitimate.set('file', new File([new Uint8Array(4 * 1024 * 1024)], 'requirements.pdf'))
  expect((await app.request(reviewPath, { method: 'POST', headers: reviewAuth, body: legitimate })).status).toBe(200)
  expect(calls).toBe(1)
  const excessiveRequest = referenceUpload(Array.from({ length: 2 }, (_, index) => new File([new Uint8Array(5 * 1024 * 1024)], `policy-${index}.pdf`)))
  excessiveRequest.set('file', new File([new Uint8Array(25 * 1024 * 1024)], 'requirements.pdf'))
  excessiveRequest.set('extra', 'x'.repeat(256 * 1024))
  expect((await app.request(reviewPath, { method: 'POST', headers: reviewAuth, body: excessiveRequest })).status).toBe(413)
  expect(calls).toBe(1)
})

test('RAG uploads continue to forward only their original document', async () => {
  const calls: FormData[] = []
  const app = createTestApp(async (_url, init) => { calls.push(init?.body as FormData); return Response.json({}) })
  const body = referenceUpload([new File(['Rules'], 'policy.pdf')])
  body.set('file', new File(['Text'], 'file.txt'))
  expect((await app.request('/api/agent/documents', { method: 'POST', headers: reviewAuth, body })).status).toBe(200)
  expect([...calls[0]!.keys()]).toEqual(['file'])
})

test('archived review detail is read-only, private, and relays the verified user identity', async () => {
  const reviewId = '0196f6f8-6600-7000-8000-000000000002'
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ id: reviewId, report: {}, source_units: ['example'] })
  })
  const path = `/api/agent/spec-reviews/${reviewId}`
  expect((await app.request(path)).status).toBe(401)
  expect(calls).toHaveLength(0)
  const headers = { Authorization: 'Bearer valid-session-token', 'X-User-ID': 'forged-user' }
  const response = await app.request(path, { headers })
  expect(response.status).toBe(200)
  expect(response.headers.get('cache-control')).toBe('no-store')
  expect(calls[0]?.url).toBe(`http://127.0.0.1:8001/api/v1/spec-reviews/${reviewId}`)
  expect(new Headers(calls[0]?.init?.headers).get('X-User-ID')).toBe(user.id)
  expect((await app.request('/api/agent/spec-reviews/not-a-uuid', { headers })).status).toBe(400)
  expect(calls).toHaveLength(1)
})

test('fix proposal proxy requires a session and forwards a validated finding ID', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ status: 'clarification', explanation: 'Missing information', question: 'Which limit?', edits: [] })
  })
  const path = '/api/agent/spec-reviews/0196f6f8-6600-7000-8000-000000000001/fix'
  const body = JSON.stringify({ finding_id: 'D-001', clarification: '' })
  expect((await app.request(path, { method: 'POST', body })).status).toBe(401)
  const headers = { Authorization: 'Bearer valid-session-token', 'Content-Type': 'application/json', 'X-User-ID': 'untrusted' }
  expect((await app.request(path, { method: 'POST', headers, body: JSON.stringify({ finding_id: 'no' }) })).status).toBe(400)
  expect(calls).toHaveLength(0)
  expect((await app.request(path, { method: 'POST', headers, body })).status).toBe(200)
  expect(calls[0]?.url).toBe('http://127.0.0.1:8001/api/v1/spec-reviews/0196f6f8-6600-7000-8000-000000000001/fix')
  expect(new Headers(calls[0]?.init?.headers).get('X-User-ID')).toBe(user.id)
})


test('reference originals are requested separately using trusted session identity and bounded attachment', async () => {
  const reviewId = '0196f6f8-6600-7000-8000-000000000002'
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const app = createTestApp(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json({ source_id: 'S-001', sha256: 'a'.repeat(64), source_units: ['Before', 'Exact quote', 'After'] })
  })
  const path = `/api/agent/spec-reviews/${reviewId}/references/S-001`
  const headers = { Authorization: 'Bearer valid-session-token', 'X-User-ID': 'forged-user' }
  expect((await app.request(path)).status).toBe(401)
  expect((await app.request(path.replace('S-001', 'foreign'), { headers })).status).toBe(400)
  expect(calls).toHaveLength(0)
  const response = await app.request(path, { headers })
  expect(response.status).toBe(200)
  expect(response.headers.get('cache-control')).toBe('no-store')
  expect(calls[0]?.url).toEndWith(`/api/v1/spec-reviews/${reviewId}/references/S-001`)
  expect(new Headers(calls[0]?.init?.headers).get('X-User-ID')).toBe(user.id)
  const body = new FormData()
  body.set('file', new File(['Original'], 'rules.txt'))
  body.set('owner_id', 'forged-user')
  const restored = await app.request(path, { method: 'POST', body, headers })
  expect(restored.status).toBe(200)
  expect(restored.headers.get('cache-control')).toBe('no-store')
  expect([...(calls[1]?.init?.body as FormData).keys()]).toEqual(['file'])
  expect(new Headers(calls[1]?.init?.headers).get('X-User-ID')).toBe(user.id)
})
