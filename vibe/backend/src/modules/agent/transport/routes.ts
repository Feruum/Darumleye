import {
  AGENT_DOCUMENT_MAX_BYTES,
  AGENT_DEFAULT_REVIEW_MODE,
  agentReviewSubmissionModeSchema,
  AGENT_REFERENCE_MAX_BYTES,
  AGENT_REFERENCE_MAX_FILES,
  AGENT_REFERENCE_TOTAL_MAX_BYTES,
  AGENT_SPEC_REVIEW_MAX_BYTES,
  AGENT_SPEC_REVIEW_FORMATS,
  agentChatRequestSchema,
  agentCompletedReviewSchema,
  agentMemoryInputSchema,
  agentReferenceManifestSchema,
  agentReferenceSourceSnapshotSchema,
  agentReviewFixInputSchema,
} from '@web-app-demo/contracts'
import { Hono } from 'hono'
import type { MiddlewareHandler } from 'hono'
import { bodyLimit } from 'hono/body-limit'

import type { AppEnv } from '../../../env'
import { AppError, errorResponse } from '../../../http/errors'
import type { AuthHttpEnv } from '../../auth'

const allowedExtensions = new Set(['pdf', 'txt'])
const allowedReviewExtensions = new Set<string>(AGENT_SPEC_REVIEW_FORMATS)

export function createAgentRoutes(options: {
  env: AppEnv
  fetchAgent?: typeof fetch
  requireAuth: MiddlewareHandler<AuthHttpEnv>
}) {
  const routes = new Hono<AuthHttpEnv>()
  const proxy = (path: string, init: RequestInit | undefined, userId: string) =>
    callAgent(options.env, path, init, userId, options.fetchAgent ?? fetch)

  routes.use('*', options.requireAuth)

  routes.get('/modules', async (c) => {
    const response = await proxy('/api/v1/modules', undefined, c.var.user.id)
    return relayJson(response)
  })

  routes.get('/memories', async (c) => {
    return relayJson(await proxy('/api/v1/memories', undefined, c.var.user.id))
  })

  routes.post('/memories', bodyLimit({ maxSize: 16 * 1024 }), async (c) => {
    const input = agentMemoryInputSchema.parse(await c.req.json())
    return relayJson(await proxy('/api/v1/memories', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
    }, c.var.user.id))
  })

  routes.delete('/memories/:memoryId', async (c) => {
    return relayJson(await proxy(`/api/v1/memories/${encodeURIComponent(c.req.param('memoryId'))}`, { method: 'DELETE' }, c.var.user.id))
  })

  routes.get('/documents', async (c) => {
    const response = await proxy('/admin/documents', undefined, c.var.user.id)
    return relayJson(response)
  })

  routes.get('/spec-reviews', async (c) => {
    const response = await relayJson(await proxy('/api/v1/spec-reviews', undefined, c.var.user.id))
    response.headers.set('cache-control', 'no-store')
    return response
  })

  for (const method of ['get', 'delete'] as const) {
    routes[method]('/spec-review-jobs/:jobId', async (c) => {
      const jobId = agentCompletedReviewSchema.shape.id.parse(c.req.param('jobId'))
      const response = await relayJson(await proxy(`/api/v1/spec-review-jobs/${encodeURIComponent(jobId)}`, {
        method: method === 'delete' ? 'DELETE' : 'GET',
        signal: c.req.raw.signal,
      }, c.var.user.id))
      response.headers.set('cache-control', 'no-store')
      return response
    })
  }

  routes.get('/spec-reviews/:reviewId', async (c) => {
    const reviewId = agentCompletedReviewSchema.shape.id.parse(c.req.param('reviewId'))
    const response = await relayJson(await proxy(`/api/v1/spec-reviews/${encodeURIComponent(reviewId)}`, { signal: c.req.raw.signal }, c.var.user.id))
    response.headers.set('cache-control', 'no-store')
    return response
  })

  routes.get('/spec-reviews/:reviewId/references/:sourceId', async (c) => {
    const reviewId = agentCompletedReviewSchema.shape.id.parse(c.req.param('reviewId'))
    const sourceId = agentReferenceSourceSnapshotSchema.shape.source_id.parse(c.req.param('sourceId'))
    const response = await relayJson(await proxy(`/api/v1/spec-reviews/${encodeURIComponent(reviewId)}/references/${encodeURIComponent(sourceId)}`, { signal: c.req.raw.signal }, c.var.user.id))
    response.headers.set('cache-control', 'no-store')
    return response
  })

  routes.post('/spec-reviews/:reviewId/references/:sourceId', bodyLimit({ maxSize: AGENT_REFERENCE_MAX_BYTES + 64 * 1024 }), async (c) => {
    const reviewId = agentCompletedReviewSchema.shape.id.parse(c.req.param('reviewId'))
    const sourceId = agentReferenceSourceSnapshotSchema.shape.source_id.parse(c.req.param('sourceId'))
    const incoming = await c.req.formData().catch(() => { throw new AppError(400, 'BAD_REQUEST', 'Send the original reference as multipart/form-data') })
    const file = incoming.get('file')
    if (!(file instanceof File) || file.size === 0 || incoming.getAll('file').length !== 1) throw new AppError(400, 'BAD_REQUEST', 'One non-empty reference file is required')
    if (file.size > AGENT_REFERENCE_MAX_BYTES) throw new AppError(413, 'PAYLOAD_TOO_LARGE', 'Reference exceeds the 5 MiB limit')
    const body = new FormData()
    body.set('file', file, file.name)
    const response = await relayJson(await proxy(`/api/v1/spec-reviews/${encodeURIComponent(reviewId)}/references/${encodeURIComponent(sourceId)}`, {
      method: 'POST', body, signal: AbortSignal.any([c.req.raw.signal, AbortSignal.timeout(60_000)]),
    }, c.var.user.id))
    response.headers.set('cache-control', 'no-store')
    return response
  })

  routes.post('/spec-reviews/:reviewId/fix', bodyLimit({ maxSize: 16 * 1024 }), async (c) => {
    const reviewId = agentCompletedReviewSchema.shape.id.parse(c.req.param('reviewId'))
    const input = agentReviewFixInputSchema.parse(await c.req.json())
    const response = await relayJson(await proxy(`/api/v1/spec-reviews/${encodeURIComponent(reviewId)}/fix`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
      signal: c.req.raw.signal,
    }, c.var.user.id), 'Fix proposal request is too large')
    response.headers.set('cache-control', 'no-store')
    return response
  })

  routes.get('/documents/:documentId', async (c) => {
    const response = await proxy(`/admin/documents/${encodeURIComponent(c.req.param('documentId'))}`, undefined, c.var.user.id)
    return relayJson(response)
  })

  routes.delete('/documents/:documentId', async (c) => {
    const response = await proxy(`/admin/documents/${encodeURIComponent(c.req.param('documentId'))}`, { method: 'DELETE' }, c.var.user.id)
    return relayJson(response)
  })

  function uploadRoute(path: string, target: string, maxBytes: number, limitMessage: string, review = false, job = false) {
    const requestLimit = maxBytes + (review ? AGENT_REFERENCE_TOTAL_MAX_BYTES : 0) + 256 * 1024
    const requestLimitMessage = review ? 'Specification and references exceed the upload limit' : limitMessage
    routes.post(path, bodyLimit({
      maxSize: requestLimit,
      onError: (c) => c.json(errorResponse('PAYLOAD_TOO_LARGE', requestLimitMessage), 413),
    }), async (c) => {
      const declaredLength = Number(c.req.header('content-length'))
      if (Number.isFinite(declaredLength) && declaredLength > requestLimit) {
        throw new AppError(413, 'PAYLOAD_TOO_LARGE', requestLimitMessage)
      }

      let incoming: FormData
      try {
        incoming = await c.req.formData()
      } catch {
        throw new AppError(400, 'BAD_REQUEST', 'Send the document as multipart/form-data')
      }
      const file = incoming.get('file')
      const formats = review ? 'PDF, TXT, Markdown, DOCX, XLSX, CSV, JPG, JPEG or PNG' : 'PDF or TXT'
      if (!(file instanceof File)) {
        throw new AppError(400, 'BAD_REQUEST', `A ${formats} file is required`)
      }
      if (!review && file.size === 0) {
        throw new AppError(400, 'BAD_REQUEST', 'The document is empty')
      }
      if (file.size > maxBytes) {
        throw new AppError(413, 'PAYLOAD_TOO_LARGE', limitMessage)
      }
      if (!isSupportedAgentDocument(file.name, review)) {
        throw new AppError(400, 'BAD_REQUEST', `Only ${formats} documents are supported`)
      }

      const outgoing = new FormData()
      outgoing.set('file', file, file.name)
      if (review) {
        appendReviewReferences(incoming, outgoing)
        const modes = incoming.getAll('review_mode')
        const mode = agentReviewSubmissionModeSchema.safeParse(modes[0] ?? AGENT_DEFAULT_REVIEW_MODE)
        if (modes.length > 1 || !mode.success) {
          throw new AppError(400, 'BAD_REQUEST', 'Invalid review mode')
        }
        outgoing.set('review_mode', mode.data)
      }
      if (job) {
        const supplied = incoming.getAll('job_id')
        if (supplied.length !== 1) throw new AppError(400, 'BAD_REQUEST', 'Provide one review job ID')
        outgoing.set('job_id', agentCompletedReviewSchema.shape.id.parse(supplied[0]))
      }
      const response = await proxy(target, {
        method: 'POST',
        body: outgoing,
        signal: review ? AbortSignal.any([c.req.raw.signal, AbortSignal.timeout(job ? 120_000 : 24 * 60 * 1000)]) : c.req.raw.signal,
      }, c.var.user.id)
      return relayJson(response, review ? limitMessage : undefined)
    })
  }

  uploadRoute('/documents', '/admin/ingest', AGENT_DOCUMENT_MAX_BYTES, 'Document exceeds the 25 MiB upload limit')
  uploadRoute('/spec-reviews', '/api/v1/spec-reviews', AGENT_SPEC_REVIEW_MAX_BYTES, 'Document exceeds the 25 MiB review limit', true)
  uploadRoute('/spec-review-jobs', '/api/v1/spec-review-jobs', AGENT_SPEC_REVIEW_MAX_BYTES, 'Document exceeds the 25 MiB review limit', true, true)

  routes.post('/chat/stream', async (c) => {
    const input = agentChatRequestSchema.parse(await c.req.json())
    const response = await proxy('/api/v1/chat/stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify(input),
      signal: c.req.raw.signal,
    }, c.var.user.id)
    if (!response.ok) return await relayJson(response)
    return new Response(response.body, {
      status: response.status,
      headers: {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache, no-transform',
        connection: 'keep-alive',
        'x-accel-buffering': 'no',
      },
    })
  })

  return routes
}

function appendReviewReferences(incoming: FormData, outgoing: FormData) {
  const references = incoming.getAll('reference_files')
  const manifests = incoming.getAll('reference_manifest')
  if (!references.length && !manifests.length) return
  if (references.length > AGENT_REFERENCE_MAX_FILES) {
    throw new AppError(400, 'BAD_REQUEST', `At most ${AGENT_REFERENCE_MAX_FILES} reference documents are allowed`)
  }
  if (manifests.length !== 1 || typeof manifests[0] !== 'string') {
    throw new AppError(400, 'BAD_REQUEST', 'Provide one reference manifest matching the uploaded references')
  }
  let suppliedManifest: unknown
  try {
    suppliedManifest = JSON.parse(manifests[0])
  } catch {
    throw new AppError(400, 'BAD_REQUEST', 'The reference manifest must be valid JSON')
  }
  const manifest = agentReferenceManifestSchema.safeParse(suppliedManifest)
  if (!manifest.success || manifest.data.length !== references.length) {
    throw new AppError(400, 'BAD_REQUEST', 'Reference metadata must match every uploaded reference document')
  }
  const files: File[] = []
  let totalBytes = 0
  for (const reference of references) {
    if (!(reference instanceof File) || reference.size === 0 || !isSupportedAgentDocument(reference.name, true)) {
      throw new AppError(400, 'BAD_REQUEST', 'References must be nonempty PDF, TXT, Markdown, DOCX, XLSX, CSV, JPG, JPEG or PNG files')
    }
    if (reference.size > AGENT_REFERENCE_MAX_BYTES) {
      throw new AppError(413, 'PAYLOAD_TOO_LARGE', 'A reference document exceeds the 5 MiB limit')
    }
    totalBytes += reference.size
    if (totalBytes > AGENT_REFERENCE_TOTAL_MAX_BYTES) {
      throw new AppError(413, 'PAYLOAD_TOO_LARGE', 'Reference documents exceed the 10 MiB combined limit')
    }
    files.push(reference)
  }
  if (!files.length) return
  for (const file of files) outgoing.append('reference_files', file, file.name)
  outgoing.set('reference_manifest', JSON.stringify(manifest.data))
}


async function callAgent(
  env: AppEnv,
  path: string,
  init: RequestInit | undefined,
  userId: string,
  fetchAgent: typeof fetch,
) {
  const headers = new Headers(init?.headers)
  headers.set('X-API-Key', env.AGENT_API_KEY)
  headers.set('X-User-ID', userId)

  try {
    return await fetchAgent(`${env.AGENT_SERVICE_URL}${path}`, { ...init, headers })
  } catch (cause) {
    console.error('Agent service is unavailable.', cause)
    throw new AppError(503, 'AGENT_UNAVAILABLE', 'The agent service is unavailable')
  }
}

async function relayJson(response: Response, reviewUploadLimitMessage?: string) {
  if (!response.ok) {
    const details = await response.json().catch(() => undefined)
    const failure = isRecord(details) && 'error' in details
      ? details.error
      : isRecord(details) && 'detail' in details
        ? details.detail
        : details
    if (response.status === 413) {
      throw new AppError(413, 'PAYLOAD_TOO_LARGE', reviewUploadLimitMessage ?? 'Document exceeds the 25 MiB upload limit')
    }
    if (reviewUploadLimitMessage) {
      if (response.status === 422 && isRecord(failure)) {
        if (failure.code === 'REVIEW_NO_TEXT' || failure.code === 'REVIEW_TEXT_LIMIT') {
          throw new AppError(422, failure.code, String(failure.message))
        }
        throw new AppError(422, 'REVIEW_INVALID_DOCUMENT', 'The document could not be read')
      }
      if (response.status === 429) {
        throw new AppError(429, 'RATE_LIMITED', 'Too many review requests')
      }
      if (response.status === 502 && isRecord(failure) && failure.code === 'REVIEW_FAILED') {
        throw new AppError(502, 'REVIEW_FAILED', 'The agent could not produce a valid review')
      }
    }
    if (response.status === 404) throw new AppError(404, 'NOT_FOUND', 'Agent resource was not found')
    if (response.status === 400 || response.status === 422) {
      throw new AppError(400, 'BAD_REQUEST', 'The agent rejected the request', details)
    }
    if (response.status === 503 && isRecord(failure) && failure.code === 'LLM_NOT_CONFIGURED') {
      throw new AppError(503, 'LLM_NOT_CONFIGURED', String(failure.message ?? 'Configure a chat provider to enable agent responses'))
    }
    console.error(`Agent service returned HTTP ${response.status}`)
    throw new AppError(503, 'AGENT_UNAVAILABLE', 'The agent service could not complete the request')
  }
  return new Response(response.body, {
    status: response.status,
    headers: {
      'content-type': response.headers.get('content-type') ?? 'application/json',
      ...(reviewUploadLimitMessage ? { 'cache-control': 'no-store' } : {}),
    },
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isSupportedAgentDocument(filename: string, review = false) {
  const extension = /\.([^.]+)$/.exec(filename)?.[1]?.toLowerCase()
  return (review ? allowedReviewExtensions : allowedExtensions).has(extension ?? '')
}
