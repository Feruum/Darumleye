import { z } from 'zod'

export const agentModuleSchema = z
  .object({
    id: z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/),
    name: z.string().trim().min(1).max(80),
    description: z.string().trim().min(1).max(240),
  })
  .strict()

export const agentModulesResponseSchema = z
  .object({
    modules: z.array(agentModuleSchema),
  })
  .strict()

export const agentChatMessageSchema = z
  .object({
    type: z.enum(['human', 'assistant']),
    content: z.string().trim().min(1).max(32000),
  })
  .strict()

export const agentChatRequestSchema = z
  .object({
    messages: z.array(agentChatMessageSchema).min(1).max(50),
    thread_id: z.string().trim().min(1).max(128).optional(),
    module_id: agentModuleSchema.shape.id.optional(),
    review_id: z.uuid().optional(),
  })
  .strict().refine((value) => (value.module_id === 'review_chat') === Boolean(value.review_id), {
    path: ['review_id'], message: 'Select a completed review for review chat.',
  })

export const AGENT_DOCUMENT_MAX_BYTES = 25 * 1024 * 1024

export const agentDocumentStatusSchema = z.enum([
  'PENDING',
  'QUEUED',
  'PROCESSING',
  'COMPLETED',
  'FAILED',
  'DELETING',
  'DELETED',
])

export const agentDocumentSchema = z
  .object({
    id: z.uuid(),
    job_id: z.uuid(),
    filename: z.string().min(1).max(512),
    status: agentDocumentStatusSchema,
    content_hash: z.string().nullable(),
    progress: z.number().int().min(0).max(100),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
    started_at: z.string().datetime({ offset: true }).nullable().optional(),
    completed_at: z.string().datetime({ offset: true }).nullable().optional(),
    error_code: z.string().nullable(),
    error_message: z.string().nullable(),
  })
  .strict()

export const agentDocumentsResponseSchema = z
  .object({
    documents: z.array(agentDocumentSchema),
  })
  .strict()

export type AgentModule = z.infer<typeof agentModuleSchema>
export type AgentDocument = z.infer<typeof agentDocumentSchema>
export type AgentDocumentStatus = z.infer<typeof agentDocumentStatusSchema>
export type AgentChatRequest = z.infer<typeof agentChatRequestSchema>

export const AGENT_MEMORY_MAX_LENGTH = 2000

export const agentMemoryInputSchema = z.object({
  content: z.string().trim().min(1).max(AGENT_MEMORY_MAX_LENGTH),
}).strict()

export const agentMemorySchema = z.object({
  id: z.uuid(),
  content: z.string().min(1).max(AGENT_MEMORY_MAX_LENGTH),
  created_at: z.string().datetime({ offset: true }),
}).strict()

export const agentMemoriesResponseSchema = z.object({
  memories: z.array(agentMemorySchema).max(1000),
}).strict()

export const agentMemoryDeletedSchema = z.object({ deleted: z.literal(true) }).strict()
export type AgentMemory = z.infer<typeof agentMemorySchema>

export const AGENT_SPEC_REVIEW_MAX_BYTES = 25 * 1024 * 1024
export const AGENT_SPEC_REVIEW_MAX_CHARACTERS = 100_000
export const AGENT_SPEC_REVIEW_MAX_PAGES = 150
export const AGENT_SPEC_REVIEW_MAX_REQUIREMENTS = 500
export const AGENT_SPEC_REVIEW_FORMATS = ['pdf', 'txt', 'md', 'docx', 'xlsx', 'csv', 'jpg', 'jpeg', 'png'] as const
export const agentSpecSourceSchema = z.enum(AGENT_SPEC_REVIEW_FORMATS)
export const AGENT_REFERENCE_MAX_FILES = 8
export const AGENT_REFERENCE_MAX_BYTES = 5 * 1024 * 1024
export const AGENT_REFERENCE_TOTAL_MAX_BYTES = 10 * 1024 * 1024

export const agentReferenceCategorySchema = z.enum([
  'quality_standard', 'bank_policy', 'api_testing', 'payments_data', 'security_regulation',
])

export const agentReferenceMetadataSchema = z.object({
  category: agentReferenceCategorySchema,
  title: z.string().trim().min(1).max(200),
  issuer: z.string().trim().min(1).max(200),
  version: z.string().trim().min(1).max(200),
  scope: z.string().trim().min(1).max(500),
  // Metadata only. The server does not retrieve a document by URL.
  source_url: z.string().trim().min(1).max(2000).refine((value) => {
    try {
      const url = new URL(value)
      const authority = /^https?:\/\/([^/?#]+)/i.exec(value)?.[1]
      return (url.protocol === 'http:' || url.protocol === 'https:') && !!authority && !authority.includes('@') &&
        Boolean(url.hostname) && !url.username && !url.password && !/\s/u.test(value) &&
        !Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
    } catch {
      return false
    }
  }, 'Reference URLs must use http or https').nullable(),
}).strict()

export const agentReferenceManifestSchema = z.array(agentReferenceMetadataSchema).max(AGENT_REFERENCE_MAX_FILES)
export type AgentReferenceCategory = z.infer<typeof agentReferenceCategorySchema>
export type AgentReferenceMetadata = z.infer<typeof agentReferenceMetadataSchema>

const agentSpecEvidenceSchema = z.object({
  start: z.number().int().positive(),
  end: z.number().int().positive(),
  quote: z.string().min(1),
}).strict().refine(({ start, end }) => start <= end)

const agentSpecFindingSchema = z.object({
  kind: z.enum(['missing', 'ambiguous', 'conflict', 'untestable', 'other']),
  severity: z.enum(['critical', 'high', 'medium', 'low']),
  issue: z.string().min(1),
  recommendation: z.string().min(1),
  evidence: z.array(agentSpecEvidenceSchema),
  requirement_ids: z.array(z.string().min(1)),
  criteria: z.array(z.string().min(1)).optional(),
  clarification_question: z.string().min(1).nullish(),
}).strict()

const readinessIds = ['business_traceability', 'functional_structure', 'sequence', 'ux_ui', 'rollback', 'testability', 'data_flow', 'operational_risk', 'architecture', 'cross_system_approval', 'information_security', 'business_process', 'integration', 'versioning', 'approvals'] as const
export const agentSpecReadinessLabels: Record<typeof readinessIds[number], string> = {
  business_traceability: 'Технические требования покрывают бизнес-требования',
  functional_structure: 'Структура функциональной спецификации',
  sequence: 'Sequence-диаграммы и взаимодействия систем',
  ux_ui: 'Соответствие UX/UI-спецификации',
  rollback: 'План отката или скрытия функциональности',
  testability: 'Проверяемость требований',
  data_flow: 'Модель данных и потоки',
  operational_risk: 'Непротиворечивость и операционные риски',
  architecture: 'Архитектура и согласование с архитекторами',
  cross_system_approval: 'Согласование изменений с владельцами затронутых систем',
  information_security: 'Риски информационной безопасности и их оценка',
  business_process: 'Схема полного бизнес-процесса и границы доработки',
  integration: 'Описание интеграционного взаимодействия',
  versioning: 'Версия и дата документа',
  approvals: 'Блок согласующих и подтверждение согласования',
}
const agentSpecReadinessCheckSchema = z.object({
  id: z.enum(readinessIds),
  availability: z.enum(['present', 'missing', 'uncertain', 'not_applicable']),
  status: z.enum(['pass', 'fail', 'uncertain', 'not_applicable']),
  comment: z.string().min(1),
  evidence: z.array(agentSpecEvidenceSchema),
  question: z.string().min(1).nullish(),
}).strict().refine(row => (row.availability === 'not_applicable') === (row.status === 'not_applicable') && ((row.availability !== 'present' && row.status !== 'pass' && row.status !== 'not_applicable') || (row.evidence.length > 0 && (row.status !== 'pass' || row.availability === 'present'))), 'Checklist confirmation requires original evidence.')

const agentSpecClarificationSchema = z.object({
  id: z.string().regex(/^Q-\d{3,}$/),
  finding_id: z.string().regex(/^D-\d{3,}$/).nullish(),
  checklist_id: z.enum(readinessIds).nullish(),
  question: z.string().min(1),
  priority: z.enum(['critical', 'high', 'medium', 'low']),
  requirement_ids: z.array(z.string().min(1)),
  evidence: z.array(agentSpecEvidenceSchema),
}).strict().refine(question => Boolean(question.finding_id) !== Boolean(question.checklist_id), 'A question must reference exactly one finding or checklist item.')

const agentSpecCheckSchema = z.object({
  criterion: z.string().min(1),
  status: z.enum(['pass', 'fail', 'uncertain', 'not_applicable']),
  comment: z.string().min(1),
}).strict()

const agentSpecRequirementSchema = z.object({
  id: z.string().min(1),
  source_id: z.string().min(1).nullable(),
  text: z.string().min(1),
  evidence: agentSpecEvidenceSchema,
  checks: z.array(agentSpecCheckSchema),
}).strict()

const agentSpecSetCheckSchema = agentSpecCheckSchema.extend({
  evidence: z.array(agentSpecEvidenceSchema),
})

export const agentReviewModeSchema = z.enum(['legacy', 'multi_agent', 'multi_agent_direct'])
export type AgentReviewMode = z.infer<typeof agentReviewModeSchema>
export const agentReviewSubmissionModeSchema = agentReviewModeSchema
export type AgentReviewSubmissionMode = z.infer<typeof agentReviewSubmissionModeSchema>
export const AGENT_DEFAULT_REVIEW_MODE: AgentReviewSubmissionMode = 'multi_agent'
export const agentReviewModeLabels: Record<AgentReviewMode, string> = {
  legacy: 'Классический (Legacy)',
  multi_agent: 'Парная валидация',
  multi_agent_direct: 'Прямая валидация',
}

export const agentEvidenceRoleSchema = z.enum(['logic_inspector', 'scenario_builder', 'gap_hunter', 'contract_inspector', 'risk_inspector', 'reference_inspector', 'code_checks'])
export const agentEvidenceKindSchema = z.enum(['requirement', 'evidence', 'observation', 'counterexample', 'finding', 'question', 'reference'])
const agentEvidenceEdgeKindSchema = z.enum(['cites', 'about', 'contributes_to', 'disagrees_with', 'illustrates', 'asks', 'from_reference', 'adjudication_context'])
const agentEvidenceNodeSchema = z.object({
  id: z.string().min(1), kind: agentEvidenceKindSchema, title: z.string().min(1),
  role: agentEvidenceRoleSchema.nullish(), severity: agentSpecFindingSchema.shape.severity.nullish(),
  requirement_id: z.string().min(1).nullish(), finding_id: z.string().min(1).nullish(),
  question_id: z.string().min(1).nullish(), source_id: z.string().min(1).nullish(),
  evidence: agentSpecEvidenceSchema.nullish(), description: z.string().nullish(),
}).strict().refine(node => ((node.kind === 'evidence') === Boolean(node.evidence)) &&
  (!['observation', 'counterexample'].includes(node.kind) || Boolean(node.role)) &&
  (node.kind !== 'requirement' || node.requirement_id === node.id) &&
  (node.kind !== 'finding' || node.finding_id === node.id) &&
  (node.kind !== 'question' || node.question_id === node.id) &&
  (node.kind !== 'reference' || node.source_id === node.id), 'Graph node metadata must identify its report item or evidence.')
const agentEvidenceEdgeSchema = z.object({ source: z.string().min(1), target: z.string().min(1), kind: agentEvidenceEdgeKindSchema }).strict()
export const agentEvidenceGraphSchema = z.object({
  schema_version: z.literal(1), nodes: z.array(agentEvidenceNodeSchema), edges: z.array(agentEvidenceEdgeSchema),
  experts: z.array(z.object({ role: agentEvidenceRoleSchema, status: z.enum(['completed', 'unavailable', 'skipped']), observations: z.number().int().nonnegative() }).strict()),
  finalization_status: z.enum(['completed', 'limited', 'deterministic_fallback']), unresolved_count: z.number().int().nonnegative(),
  adjudication: z.object({ candidates: z.number().int().nonnegative(), confirmed: z.number().int().nonnegative(),
    unsupported: z.number().int().nonnegative(), uncertain: z.number().int().nonnegative(), unreviewed: z.number().int().nonnegative() }).strict()
    .refine(a => a.confirmed + a.unsupported + a.uncertain + a.unreviewed === a.candidates, 'Invalid adjudication coverage counts.').nullish(),
}).strict().superRefine(({ nodes, edges, experts }, context) => {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const ids = new Set(byId.keys())
  if (ids.size !== nodes.length) context.addIssue({ code: 'custom', path: ['nodes'], message: 'Duplicate graph node ID.' })
  if (new Set(experts.map(expert => expert.role)).size !== experts.length) context.addIssue({ code: 'custom', path: ['experts'], message: 'Duplicate expert.' })
  for (const [index, node] of nodes.entries()) {
    const references = [[node.requirement_id, 'requirement'], [node.finding_id, 'finding'], [node.question_id, 'question'], [node.source_id, 'reference']] as const
    if (references.some(([id, kind]) => id != null && byId.get(id)?.kind !== kind)) context.addIssue({ code: 'custom', path: ['nodes', index], message: 'Graph metadata must link to an existing node of the corresponding kind.' })
  }
  const edgeKeys = new Set<string>()
  for (const [index, edge] of edges.entries()) {
    const key = JSON.stringify([edge.source, edge.target, edge.kind])
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target || edgeKeys.has(key)) context.addIssue({ code: 'custom', path: ['edges', index], message: 'Duplicate or dangling graph edge.' })
    if (edge.kind === 'adjudication_context') {
      const source = byId.get(edge.source), target = byId.get(edge.target)
      if (source?.kind !== 'finding' || target?.kind !== 'evidence' || source.source_id != null || target.source_id != null) {
        context.addIssue({ code: 'custom', path: ['edges', index], message: 'Adjudication context must link a primary finding to primary-source evidence.' })
      }
    }
    edgeKeys.add(key)
  }
})
export type AgentEvidenceGraph = z.infer<typeof agentEvidenceGraphSchema>
export type AgentEvidenceNode = z.infer<typeof agentEvidenceNodeSchema>
export type AgentEvidenceRole = z.infer<typeof agentEvidenceRoleSchema>
export type AgentEvidenceKind = z.infer<typeof agentEvidenceKindSchema>

const agentSpecPassportSchema = z.object({
  document_title: z.string().min(1),
  run_at: z.string().datetime({ offset: true }),
  run_id: z.uuid().nullish(),
  document_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullish(),
  model: z.string().min(1),
  agent_version: z.string().min(1),
  review_mode: agentReviewModeSchema.optional(),
  review_profile: z.object({
    fact_profile: z.enum(['off', 'numeric', 'full']).nullish(),
    z3_enabled: z.boolean(), reasoning_effort: z.enum(['auto', 'low', 'medium']),
    adjudication_reasoning_effort: z.enum(['auto', 'inherit', 'low', 'medium']).nullish(),
    concurrency: z.number().int().positive(), drex_enabled: z.boolean(), references_count: z.number().int().nonnegative(),
  }).strict().nullish(),
}).strict()

const agentSpecStatisticsSchema = z.object({
  requirements: z.number().int().nonnegative(),
  violations: z.number().int().nonnegative(),
  critical: z.number().int().nonnegative(),
  passed: z.number().int().nonnegative(),
  needs_revision: z.number().int().nonnegative(),
}).strict()

const agentSpecVerdictSchema = z.object({
  status: z.enum(['ready', 'needs_revision', 'not_ready']),
  reason: z.string().min(1),
  priorities: z.array(z.string().min(1)),
}).strict()

export const agentCompletedReviewSchema = z.object({
  id: z.uuid(),
  filename: z.string().min(1).max(512),
  created_at: z.string().datetime({ offset: true }),
  verdict: agentSpecVerdictSchema.shape.status,
  requirements: z.number().int().nonnegative(),
  findings: z.number().int().nonnegative(),
  analytics: z.object({
    passed: z.number().int().nonnegative(),
    needs_revision: z.number().int().nonnegative(),
    severity: z.object({ critical: z.number().int().nonnegative(), high: z.number().int().nonnegative(), medium: z.number().int().nonnegative(), low: z.number().int().nonnegative() }).strict(),
  }).strict().optional(),
}).strict()

export const agentCompletedReviewsResponseSchema = z.object({ reviews: z.array(agentCompletedReviewSchema).max(200) }).strict()
export type AgentCompletedReview = z.infer<typeof agentCompletedReviewSchema>

const agentReviewJobBase = z.object({ id: z.uuid(), filename: z.string().min(1).max(512), started_at: z.string().datetime({ offset: true }) })
export const agentReviewJobSchema = z.discriminatedUnion('status', [
  agentReviewJobBase.extend({ status: z.literal('running') }).strict(),
  agentReviewJobBase.extend({ status: z.literal('completed'), review_id: z.uuid() }).strict(),
  agentReviewJobBase.extend({ status: z.literal('failed'), error: z.object({ code: z.string().min(1), message: z.string().min(1) }).strict() }).strict(),
  agentReviewJobBase.extend({ status: z.literal('cancelled') }).strict(),
])
export type AgentReviewJob = z.infer<typeof agentReviewJobSchema>

export function agentSpecFindingId(index: number) {
  return `D-${String(index + 1).padStart(3, '0')}`
}

const agentSpecDecisionFindingSchema = z.object({
  id: z.string().regex(/^D-\d{3,}$/),
  probability: z.number().min(0).max(1),
  status: z.enum(['supported', 'uncertain', 'unsupported']),
}).strict().refine(({ probability, status }) =>
  status === (probability >= 0.8 ? 'supported' : probability < 0.4 ? 'unsupported' : 'uncertain'),
  { path: ['status'], message: 'Decision status does not match the probability.' },
)

const agentSpecDecisionReviewSchema = z.object({
  provider: z.literal('nace'),
  status: z.enum(['completed', 'unavailable', 'not_applicable']),
  model: z.string().trim().min(1),
  explanation: z.string().trim().min(1),
  findings: z.array(agentSpecDecisionFindingSchema),
}).strict()

const agentReferenceSourceSchema = agentReferenceMetadataSchema.extend({
  id: z.string().regex(/^S-\d{3,}$/),
  filename: z.string().min(1),
  source: agentSpecSourceSchema,
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  unreadable_pages: z.array(z.number().int().positive().max(60)).max(60),
  ocr_pages: z.array(z.number().int().positive().max(60)).max(60).optional(),
  ocr_units: z.array(z.number().int().positive().max(24001)).max(60).optional(),
  unreadable_units: z.array(z.number().int().positive().max(24001)).max(60).optional(),
  applicability: z.enum(['relevant', 'unclear', 'not_applicable', 'not_reviewed']),
  summary: z.string().trim().min(1),
}).strict()

const agentReferenceObservationSchema = z.object({
  id: z.string().regex(/^RF-\d{3,}$/),
  source_id: z.string().regex(/^S-\d{3,}$/),
  status: z.enum(['potential_discrepancy', 'missing_evidence', 'unclear_applicability']),
  issue: z.string().trim().min(1),
  recommendation: z.string().trim().min(1),
  requirement_ids: z.array(z.string().min(1)),
  specification_evidence: z.array(agentSpecEvidenceSchema).min(1),
  reference_evidence: z.array(agentSpecEvidenceSchema).min(1),
}).strict()

const agentReferenceRetrievalSourceSchema = z.object({
  source_id: z.string().regex(/^S-\d{3,}$/),
  total_chunks: z.number().int().nonnegative(),
  selected_chunks: z.number().int().nonnegative(),
  total_characters: z.number().int().nonnegative(),
  selected_characters: z.number().int().nonnegative(),
  partial: z.boolean(),
}).strict().refine(({ total_chunks, selected_chunks, total_characters, selected_characters, partial }) =>
  selected_chunks <= total_chunks && selected_characters <= total_characters &&
  partial === (selected_chunks < total_chunks || selected_characters < total_characters),
  { message: 'Retrieval coverage must reflect the selected part of the source.' },
)

const agentReferenceRetrievalSchema = z.object({
  mode: z.string().trim().min(1),
  sources: z.array(agentReferenceRetrievalSourceSchema).min(1).max(AGENT_REFERENCE_MAX_FILES),
}).strict()

const agentReferenceDecisionFindingSchema = agentSpecDecisionFindingSchema.safeExtend({
  id: z.string().regex(/^RF-\d{3,}$/),
})

const agentReferenceDecisionReviewSchema = agentSpecDecisionReviewSchema.extend({
  findings: z.array(agentReferenceDecisionFindingSchema),
})

const agentReferenceReviewSchema = z.object({
  status: z.enum(['completed', 'unavailable']),
  explanation: z.string().trim().min(1),
  sources: z.array(agentReferenceSourceSchema).min(1).max(AGENT_REFERENCE_MAX_FILES),
  observations: z.array(agentReferenceObservationSchema),
  retrieval: agentReferenceRetrievalSchema.nullish(),
  decision_review: agentReferenceDecisionReviewSchema.nullish(),
}).strict()

export const agentSpecReviewSchema = z.object({
  filename: z.string().min(1),
  source: agentSpecSourceSchema,
  unreadable_pages: z.array(z.number().int().positive().max(AGENT_SPEC_REVIEW_MAX_PAGES)).max(AGENT_SPEC_REVIEW_MAX_PAGES),
  ocr_pages: z.array(z.number().int().positive().max(AGENT_SPEC_REVIEW_MAX_PAGES)).max(AGENT_SPEC_REVIEW_MAX_PAGES).optional(),
  ocr_units: z.array(z.number().int().positive().max(AGENT_SPEC_REVIEW_MAX_CHARACTERS + 1)).max(AGENT_SPEC_REVIEW_MAX_PAGES).optional(),
  unreadable_units: z.array(z.number().int().positive().max(AGENT_SPEC_REVIEW_MAX_CHARACTERS + 1)).max(AGENT_SPEC_REVIEW_MAX_PAGES).optional(),
  summary: z.string().min(1),
  findings: z.array(agentSpecFindingSchema),
  passport: agentSpecPassportSchema,
  requirements: z.array(agentSpecRequirementSchema).max(AGENT_SPEC_REVIEW_MAX_REQUIREMENTS),
  set_checks: z.array(agentSpecSetCheckSchema),
  statistics: agentSpecStatisticsSchema,
  verdict: agentSpecVerdictSchema,
  decision_review: agentSpecDecisionReviewSchema.nullish(),
  reference_review: agentReferenceReviewSchema.nullish(),
  report_markdown: z.string().min(1),
  readiness_checklist: z.array(agentSpecReadinessCheckSchema).length(readinessIds.length).optional(),
  clarification_questions: z.array(agentSpecClarificationSchema).optional(),
  extraction_gaps: z.array(agentSpecEvidenceSchema).optional(),
  evidence_graph: agentEvidenceGraphSchema.nullish(),
}).strict().superRefine(({ findings, requirements, readiness_checklist: checklist, clarification_questions: questions, extraction_gaps, verdict }, context) => {
  const rowIds = new Set(checklist?.map(row => row.id))
  if (checklist && rowIds.size !== checklist.length) context.addIssue({ code: 'custom', path: ['readiness_checklist'], message: 'Duplicate checklist item.' })
  if (checklist?.some(row => row.status === 'fail' || row.status === 'uncertain') && verdict.status === 'ready') context.addIssue({ code: 'custom', path: ['verdict', 'status'], message: 'Unconfirmed readiness checklist cannot accompany a ready verdict.' })
  if (extraction_gaps?.length && verdict.status === 'ready') context.addIssue({ code: 'custom', path: ['verdict', 'status'], message: 'Uncovered requirement candidates cannot accompany a ready verdict.' })
  const reqIds = new Set(requirements.map(item => item.id))
  const findingIds = new Set(findings.map((_, index) => agentSpecFindingId(index)))
  for (const [index, question] of (questions ?? []).entries()) {
    if (question.id !== `Q-${String(index + 1).padStart(3, '0')}` ||
      (question.finding_id && !findingIds.has(question.finding_id)) ||
      (question.checklist_id && !rowIds.has(question.checklist_id)) || question.requirement_ids.some(id => !reqIds.has(id))) {
      context.addIssue({ code: 'custom', path: ['clarification_questions', index], message: 'Question references an unknown source or inconsistent ID.' })
    }
  }
}).superRefine(({ findings, decision_review: audit, unreadable_units, verdict }, context) => {
  if (unreadable_units?.length && verdict.status === 'ready') {
    context.addIssue({ code: 'custom', path: ['verdict', 'status'], message: 'A document with unreadable image blocks cannot be ready.' })
  }
  if (!audit) return
  if (audit.status !== 'completed') {
    if (audit.findings.length > 0 || (audit.status === 'not_applicable' && findings.length > 0)) {
      context.addIssue({ code: 'custom', path: ['decision_review'], message: 'Decision review status does not match its findings.' })
    }
    return
  }
  const assessedIds = new Set(audit.findings.map(({ id }) => id))
  if (findings.length === 0 || audit.findings.length !== findings.length || assessedIds.size !== findings.length ||
      !findings.every((_, index) => assessedIds.has(agentSpecFindingId(index)))) {
    context.addIssue({ code: 'custom', path: ['decision_review', 'findings'], message: 'Completed decision review must cover every report finding exactly once.' })
  }
}).superRefine(({ requirements, reference_review: review }, context) => {
  if (!review) return
  const sources = new Map(review.sources.map(source => [source.id, source]))
  const requirementIds = new Set(requirements.map(requirement => requirement.id))
  if (review.sources.some((source, index) => source.id !== `S-${String(index + 1).padStart(3, '0')}`)) {
    context.addIssue({ code: 'custom', path: ['reference_review', 'sources'], message: 'Reference source IDs must match source order.' })
  }
  if (review.status === 'unavailable'
    ? review.observations.length > 0 || review.sources.some(source => source.applicability !== 'not_reviewed')
    : review.sources.some(source => source.applicability === 'not_reviewed')) {
    context.addIssue({ code: 'custom', path: ['reference_review'], message: 'The reference review status must match its sources and observations.' })
  }
  for (const [index, observation] of review.observations.entries()) {
    const source = sources.get(observation.source_id)
    const invalid = observation.id !== `RF-${String(index + 1).padStart(3, '0')}` ||
      !source || source.applicability === 'not_applicable' || source.applicability === 'not_reviewed' ||
      (source.applicability === 'unclear' && observation.status !== 'unclear_applicability') ||
      new Set(observation.requirement_ids).size !== observation.requirement_ids.length ||
      observation.requirement_ids.some(id => !requirementIds.has(id))
    if (invalid) {
      context.addIssue({ code: 'custom', path: ['reference_review', 'observations', index], message: 'The observation must refer to an applicable source and valid report requirement IDs.' })
    }
  }
  if (review.retrieval) {
    const retrievedIds = new Set(review.retrieval.sources.map(source => source.source_id))
    if (review.retrieval.sources.length !== sources.size ||
        retrievedIds.size !== sources.size ||
        !review.sources.every(source => retrievedIds.has(source.id))) {
      context.addIssue({ code: 'custom', path: ['reference_review', 'retrieval', 'sources'], message: 'Retrieval coverage must account for each supplied source exactly once.' })
    }
  }
  const audit = review.decision_review
  if (!audit) return
  if (audit.status !== 'completed') {
    if (audit.findings.length > 0 ||
        (audit.status === 'not_applicable' && (review.observations.length > 0 || review.status !== 'completed'))) {
      context.addIssue({ code: 'custom', path: ['reference_review', 'decision_review'], message: 'Reference decision status does not match the observations.' })
    }
    return
  }
  const assessedIds = new Set(audit.findings.map(finding => finding.id))
  if (review.status !== 'completed' || review.observations.length === 0 ||
      audit.findings.length !== review.observations.length ||
      assessedIds.size !== review.observations.length ||
      !review.observations.every(observation => assessedIds.has(observation.id))) {
    context.addIssue({ code: 'custom', path: ['reference_review', 'decision_review', 'findings'], message: 'Completed reference decision review must assess every observation exactly once.' })
  }
}).superRefine(({ passport, evidence_graph: graph, requirements, findings, clarification_questions, reference_review }, context) => {
  if (!graph) {
    if (passport.review_mode && passport.review_mode !== 'legacy') context.addIssue({ code: 'custom', path: ['evidence_graph'], message: 'Multi-agent review must preserve its evidence graph.' })
    return
  }
  const requirementIds = new Set(requirements.map(item => item.id))
  const findingIds = new Set([...findings.map((_, index) => agentSpecFindingId(index)), ...(reference_review?.observations ?? []).map(item => item.id)])
  const questionIds = new Set((clarification_questions ?? []).map(item => item.id))
  const referenceIds = new Set((reference_review?.sources ?? []).map(item => item.id))
  const graphNodes = new Map(graph.nodes.map(node => [node.id, node]))
  const expectedNodes = [[requirementIds, 'requirement'], [findingIds, 'finding'], [questionIds, 'question'], [referenceIds, 'reference']] as const
  if (expectedNodes.some(([ids, kind]) => [...ids].some(id => graphNodes.get(id)?.kind !== kind))) {
    context.addIssue({ code: 'custom', path: ['evidence_graph', 'nodes'], message: 'Graph must cover every requirement, finding, question and reference in the report.' })
  }
  for (const [index, node] of graph.nodes.entries()) {
    if ((node.requirement_id && !requirementIds.has(node.requirement_id)) ||
        (node.finding_id && !findingIds.has(node.finding_id)) ||
        (node.question_id && !questionIds.has(node.question_id)) ||
        (node.source_id && !referenceIds.has(node.source_id))) {
      context.addIssue({ code: 'custom', path: ['evidence_graph', 'nodes', index], message: 'Graph node references an unknown report item.' })
    }
  }
})

export const agentCompletedReviewDetailSchema = z.object({
  id: z.uuid(),
  report: agentSpecReviewSchema,
  source_units: z.array(z.string()).max(AGENT_SPEC_REVIEW_MAX_CHARACTERS + 1),
}).strict().refine(({ id, report }) => !report.passport.run_id || report.passport.run_id === id).superRefine(({ report, source_units }, context) => {
  if (!report.evidence_graph) return
  // Match Python's whitespace normalization of source quotes, including its
  // splitlines control separators; do not count an external RF citation here.
  // eslint-disable-next-line no-control-regex -- Python str.split() recognizes these separators.
  const whitespace = /[ \t\n\r\f\v\x1c-\x1f\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu
  const normalize = (value: string) => value.replace(whitespace, ' ').replace(/^ +| +$/g, '')
  const unreadable = new Set([...report.unreadable_pages, ...(report.unreadable_units ?? [])])
  for (const [index, node] of report.evidence_graph.nodes.entries()) {
    if (node.kind !== 'evidence' || node.source_id || !node.evidence) continue
    const { start, end, quote } = node.evidence
    if (end > source_units.length || [...unreadable].some(unit => unit >= start && unit <= end) || !normalize(quote) || !normalize(source_units.slice(start - 1, end).join(' ')).includes(normalize(quote))) {
      context.addIssue({ code: 'custom', path: ['report', 'evidence_graph', 'nodes', index, 'evidence'], message: 'Graph citation must match the archived original source.' })
    }
  }
})

export type AgentCompletedReviewDetail = z.infer<typeof agentCompletedReviewDetailSchema>

export const agentReferenceSourceSnapshotSchema = z.object({
  source_id: z.string().regex(/^S-\d{3,8}$/),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  source_units: z.array(z.string()).min(1).max(24001),
}).strict().refine(value => value.source_units.reduce((length, unit) => length + unit.length, 0) <= 48000)
export type AgentReferenceSourceSnapshot = z.infer<typeof agentReferenceSourceSnapshotSchema>

export const agentReviewFixInputSchema = z.object({
  finding_id: z.string().regex(/^D-\d{3,8}$/),
  clarification: z.string().trim().max(2000),
}).strict()

const agentReviewEditSchema = z.object({
  unit: z.number().int().positive(),
  before: z.string().min(1).max(4000),
  after: z.string().max(6000),
}).strict().refine(({ before, after }) => before.trim().length > 0 && before !== after && !after.includes('\0'))

export const agentReviewFixSchema = z.object({
  status: z.enum(['patch', 'clarification']),
  explanation: z.string().min(1).max(2000),
  question: z.string().min(1).max(2000).nullable(),
  edits: z.array(agentReviewEditSchema).max(4),
}).strict().refine(({ status, question, edits }) =>
  status === 'patch' ? question === null && edits.length > 0 : Boolean(question) && edits.length === 0)

export type AgentReviewFixInput = z.infer<typeof agentReviewFixInputSchema>
export type AgentReviewFix = z.infer<typeof agentReviewFixSchema>
export type AgentReviewEdit = z.infer<typeof agentReviewEditSchema>

export type AgentSpecReview = z.infer<typeof agentSpecReviewSchema>
export type AgentSpecDecisionReview = z.infer<typeof agentSpecDecisionReviewSchema>
export type AgentSpecDecisionFinding = z.infer<typeof agentSpecDecisionFindingSchema>
export type AgentReferenceReview = z.infer<typeof agentReferenceReviewSchema>
