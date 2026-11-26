import {
  agentChatRequestSchema,
  agentCompletedReviewsResponseSchema,
  agentCompletedReviewDetailSchema,
  agentReferenceSourceSnapshotSchema,
  agentDocumentSchema,
  agentDocumentsResponseSchema,
  agentModulesResponseSchema,
  agentReviewJobSchema,
  agentReferenceManifestSchema,
  agentReviewFixInputSchema,
  agentReviewFixSchema,
  agentMemoriesResponseSchema,
  agentMemoryInputSchema,
  agentMemorySchema,
  agentMemoryDeletedSchema,
  AGENT_DOCUMENT_MAX_BYTES,
  AGENT_DEFAULT_REVIEW_MODE,
  agentReviewSubmissionModeSchema,
  AGENT_SPEC_REVIEW_MAX_BYTES,
  AGENT_SPEC_REVIEW_FORMATS,
  type AgentChatRequest,
  type AgentReviewFixInput,
  type AgentReviewJob,
  type AgentReviewMode,
} from '@web-app-demo/contracts'

import { ZodError } from 'zod'
import { ApiRequestError, type AuthenticatedTransport } from '@/platform/api'
import { referenceSelectionError, type ReferenceUpload } from './reference-sources'

export function getAgentModules(transport: AuthenticatedTransport, signal?: AbortSignal) {
  return transport.request('/api/agent/modules', agentModulesResponseSchema, { signal })
}

export function getAgentDocuments(transport: AuthenticatedTransport, signal?: AbortSignal) {
  return transport.request('/api/agent/documents', agentDocumentsResponseSchema, { signal })
}

export function getCompletedReviews(transport: AuthenticatedTransport, signal?: AbortSignal) {
  return transport.request('/api/agent/spec-reviews', agentCompletedReviewsResponseSchema, { signal })
}

export function getCompletedReview(transport: AuthenticatedTransport, reviewId: string, signal?: AbortSignal) {
  return transport.request(`/api/agent/spec-reviews/${encodeURIComponent(reviewId)}`, agentCompletedReviewDetailSchema, { signal })
}

export function getCompletedReferenceSource(transport: AuthenticatedTransport, reviewId: string, sourceId: string, signal?: AbortSignal) {
  return transport.request(`/api/agent/spec-reviews/${encodeURIComponent(reviewId)}/references/${encodeURIComponent(sourceId)}`, agentReferenceSourceSnapshotSchema, { signal })
}

export function restoreCompletedReferenceSource(transport: AuthenticatedTransport, reviewId: string, sourceId: string, file: File, signal?: AbortSignal) {
  const body = new FormData()
  body.set('file', file, file.name)
  return transport.request(`/api/agent/spec-reviews/${encodeURIComponent(reviewId)}/references/${encodeURIComponent(sourceId)}`, agentReferenceSourceSnapshotSchema, { method: 'POST', body, signal })
}

export function proposeReviewFix(transport: AuthenticatedTransport, reviewId: string, input: AgentReviewFixInput, signal?: AbortSignal) {
  return transport.request(`/api/agent/spec-reviews/${encodeURIComponent(reviewId)}/fix`, agentReviewFixSchema, {
    method: 'POST', body: agentReviewFixInputSchema.parse(input), signal,
  })
}

export function getAgentMemories(transport: AuthenticatedTransport, signal?: AbortSignal) {
  return transport.request('/api/agent/memories', agentMemoriesResponseSchema, { signal })
}

export function createAgentMemory(transport: AuthenticatedTransport, content: string) {
  return transport.request('/api/agent/memories', agentMemorySchema, {
    method: 'POST',
    body: agentMemoryInputSchema.parse({ content }),
  })
}

export function deleteAgentMemory(transport: AuthenticatedTransport, id: string) {
  return transport.request(`/api/agent/memories/${encodeURIComponent(id)}`, agentMemoryDeletedSchema, { method: 'DELETE' })
}

export async function uploadAgentDocument(transport: AuthenticatedTransport, file: File) {
  const validationError = documentSelectionError(file)
  if (validationError) throw new Error(validationError)

  const form = new FormData()
  form.set('file', file, file.name)
  const response = await transport.requestRaw('/api/agent/documents', {
    method: 'POST',
    body: form,
  })
  return agentDocumentSchema.parse(await response.json())
}

export function buildSpecificationReviewFormData(file: File, references: readonly ReferenceUpload[] = [], reviewMode: AgentReviewMode = AGENT_DEFAULT_REVIEW_MODE) {
  const error = referenceSelectionError(references)
  if (error) throw new Error(error)
  const body = new FormData()
  body.set('file', file, file.name)
  body.set('review_mode', agentReviewSubmissionModeSchema.parse(reviewMode))
  if (references.length) {
    const manifest = agentReferenceManifestSchema.parse(references.map(({ metadata }) => metadata))
    for (const { file: reference } of references) body.append('reference_files', reference, reference.name)
    body.set('reference_manifest', JSON.stringify(manifest))
  }
  return body
}

export async function reviewAgentSpecification(transport: AuthenticatedTransport, file: File, signal = new AbortController().signal, references: readonly ReferenceUpload[] = [], interval = 1500, reviewMode: AgentReviewMode = AGENT_DEFAULT_REVIEW_MODE, id: string = crypto.randomUUID()) {
  let initial: AgentReviewJob | undefined
  try {
    initial = await startReviewJob(transport, id, file, signal, references, reviewMode)
  } catch (error) {
    signal.throwIfAborted()
    if (error instanceof ApiRequestError && (error.status < 500 || error.code === 'LLM_NOT_CONFIGURED')) throw error
    // Acknowledgement can be lost after durable admission. Reattach instead
    // of submitting the revised document again under another ID.
  }
  return waitForReviewJob(transport, id, signal, () => {}, initial, interval)
}

export function startReviewJob(transport: AuthenticatedTransport, id: string, file: File, signal?: AbortSignal, references: readonly ReferenceUpload[] = [], reviewMode: AgentReviewMode = AGENT_DEFAULT_REVIEW_MODE) {
  const body = buildSpecificationReviewFormData(file, references, reviewMode)
  body.set('job_id', id)
  return transport.request('/api/agent/spec-review-jobs', agentReviewJobSchema, { method: 'POST', body, signal })
}

export function getReviewJob(transport: AuthenticatedTransport, id: string, signal?: AbortSignal) {
  return transport.request(`/api/agent/spec-review-jobs/${encodeURIComponent(id)}`, agentReviewJobSchema, { signal })
}

export function cancelReviewJob(transport: AuthenticatedTransport, id: string, signal?: AbortSignal) {
  return transport.request(`/api/agent/spec-review-jobs/${encodeURIComponent(id)}`, agentReviewJobSchema, { method: 'DELETE', signal })
}

function pollDelay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return }
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve() }, ms)
    function stop() { clearTimeout(timer); reject(signal.reason) }
    signal.addEventListener('abort', stop, { once: true })
  })
}

function jobRetryDelay(interval: number, errors: number) {
  return errors ? Math.min(15_000, interval * 2 ** Math.min(errors, 4)) : interval
}

export async function cancelReviewJobWhenAvailable(transport: AuthenticatedTransport, id: string, signal: AbortSignal, interval = 1500) {
  let errors = 0
  let missing = 0
  for (;;) {
    signal.throwIfAborted()
    try {
      const result = await cancelReviewJob(transport, id, signal)
      if (result.status !== 'running') return result
      errors = 0
      missing = 0
    } catch (error) {
      signal.throwIfAborted()
      if (error instanceof ApiRequestError && error.status === 404) {
        if (++missing >= 4) return undefined
      } else if (error instanceof TypeError || (error instanceof ApiRequestError && (error.status >= 500 || error.status === 429))) {
        missing = 0
      } else throw error
      errors++
    }
    await pollDelay(jobRetryDelay(interval, errors), signal)
  }
}

export async function waitForReviewJob(transport: AuthenticatedTransport, id: string, signal: AbortSignal, observe: (job: AgentReviewJob) => void, initial?: AgentReviewJob, interval = 1500) {
  let job = initial
  let transientErrors = 0
  let missingErrors = 0
  for (;;) {
    signal.throwIfAborted()
    try {
      if (!job) job = await getReviewJob(transport, id, signal)
      observe(job)
      if (job.status === 'failed') throw new ApiRequestError(422, job.error.code, job.error.message)
      if (job.status === 'cancelled') return undefined
      if (job.status === 'completed') return (await getCompletedReview(transport, job.review_id, signal)).report
      job = undefined
      transientErrors = 0
      missingErrors = 0
    } catch (error) {
      signal.throwIfAborted()
      // A durable job can outlive a long server restart. Follow its UUID until
      // the server reports a terminal state, or the user leaves/cancels. Avoid
      // continuous rapid requests while the service is unavailable.
      if (error instanceof ApiRequestError && error.status === 404) {
        // A lost upload acknowledgement can briefly precede persistence, but
        // an actually missing job is not a transient server outage.
        if (++missingErrors >= 4) throw error
      } else if (error instanceof TypeError || (error instanceof ApiRequestError && (error.status >= 500 || error.status === 429))) {
        missingErrors = 0
      } else {
        throw error
      }
      transientErrors++
      job = undefined
    }
    await pollDelay(jobRetryDelay(interval, transientErrors), signal)
  }
}

export function isSupportedDocument(file: Pick<File, 'name' | 'size'>, maxBytes = AGENT_DOCUMENT_MAX_BYTES) {
  return documentSelectionError(file, maxBytes) === undefined
}

export function documentSelectionError(file: Pick<File, 'name' | 'size'>, maxBytes = AGENT_DOCUMENT_MAX_BYTES): string | undefined {
  if (!/\.(pdf|txt)$/i.test(file.name)) return 'Выберите PDF или TXT.'
  if (file.size === 0) return 'Файл пуст. Выберите документ с текстом.'
  if (file.size > maxBytes) return `Файл превышает допустимый размер (${maxBytes / 1024 / 1024} МиБ).`
}

export function documentUploadErrorMessage(error: unknown): string {
  if (error instanceof ZodError || error instanceof SyntaxError) {
    return 'Не удалось прочитать ответ сервера. Проверьте список документов перед повторной загрузкой.'
  }
  if (error instanceof ApiRequestError) {
    switch (error.code) {
      case 'PAYLOAD_TOO_LARGE': return 'Файл превышает допустимый размер (25 МиБ).'
      case 'BAD_REQUEST':
      case 'VALIDATION_ERROR':
      case 'UPLOAD_REJECTED': return 'Файл не прошёл проверку. Выберите исправный PDF или текстовый TXT.'
      case 'AGENT_UNAVAILABLE': return 'Сервис обработки документов недоступен. Попробуйте позже.'
      case 'UNAUTHORIZED': return 'Сессия завершена. Войдите в аккаунт повторно.'
    }
  }
  return 'Не удалось добавить документ. Повторите попытку.'
}

export function isSupportedSpecification(file: Pick<File, 'name' | 'size'>) {
  const extension = /\.([^.]+)$/.exec(file.name)?.[1]?.toLowerCase()
  return AGENT_SPEC_REVIEW_FORMATS.some(format => format === extension) && file.size > 0 && file.size <= AGENT_SPEC_REVIEW_MAX_BYTES
}

export function streamAgentChat(
  transport: AuthenticatedTransport,
  input: AgentChatRequest,
  signal?: AbortSignal,
) {
  const payload = agentChatRequestSchema.parse(input)
  return transport.requestRaw('/api/agent/chat/stream', {
    method: 'POST',
    body: payload,
    signal,
    headers: { Accept: 'text/event-stream' },
  })
}
