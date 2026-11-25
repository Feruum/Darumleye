import {
  AGENT_REFERENCE_MAX_BYTES,
  AGENT_REFERENCE_MAX_FILES,
  AGENT_REFERENCE_TOTAL_MAX_BYTES,
  AGENT_SPEC_REVIEW_FORMATS,
  agentReferenceMetadataSchema,
  type AgentReferenceCategory,
  type AgentReferenceMetadata,
  type AgentReferenceReview,
} from '@web-app-demo/contracts'

export type ReferenceMetadataDraft = Omit<AgentReferenceMetadata, 'category'> & { category: AgentReferenceCategory | '' }
export type ReferenceUpload = { file: File; metadata: ReferenceMetadataDraft }
export type ReferenceDraft = ReferenceUpload & { id: string }

export const referenceCategoryLabels: Record<AgentReferenceCategory, string> = {
  quality_standard: 'Стандарт качества требований',
  bank_policy: 'Внутренний регламент банка',
  api_testing: 'Стандарты API и тестирования',
  payments_data: 'Платежи и персональные данные РК',
  security_regulation: 'Информационная безопасность',
}

export const referencePresets: ReadonlyArray<{ id: string; label: string; metadata: AgentReferenceMetadata }> = [
  { id: 'iso-29148', label: 'ISO/IEC/IEEE 29148:2018', metadata: { category: 'quality_standard', title: 'ISO/IEC/IEEE 29148:2018', issuer: 'ISO / IEC / IEEE', version: '2018', scope: 'Инженерия требований к системам и программному обеспечению', source_url: 'https://www.iso.org/standard/72089.html' } },
  { id: 'ieee-830', label: 'IEEE 830-1998 (исторический)', metadata: { category: 'quality_standard', title: 'IEEE Std 830-1998', issuer: 'IEEE', version: '1998 (заменён)', scope: 'Историческая рекомендация по спецификациям требований к ПО', source_url: 'https://standards.ieee.org/ieee/830/1222/' } },
  { id: 'bank-policy', label: 'Внутренний регламент подготовки ТЗ', metadata: { category: 'bank_policy', title: 'Внутренний регламент подготовки ТЗ', issuer: '', version: '', scope: '', source_url: null } },
  { id: 'api-testing', label: 'Внутренний стандарт API / QA', metadata: { category: 'api_testing', title: 'Внутренний стандарт API и тестирования', issuer: '', version: '', scope: '', source_url: null } },
  { id: 'openapi', label: 'OpenAPI Specification 3.1.1', metadata: { category: 'api_testing', title: 'OpenAPI Specification', issuer: 'OpenAPI Initiative', version: '3.1.1', scope: 'Описание HTTP API; применимые требования к спецификациям API', source_url: 'https://spec.openapis.org/oas/v3.1.1.html' } },
  { id: 'iso-29119', label: 'ISO/IEC/IEEE 29119-2:2021', metadata: { category: 'api_testing', title: 'ISO/IEC/IEEE 29119-2:2021', issuer: 'ISO / IEC / IEEE', version: '2021', scope: 'Процессы тестирования программного обеспечения', source_url: 'https://www.iso.org/standard/79428.html' } },
  { id: 'payments-law', label: 'РК: платежи и платёжные системы', metadata: { category: 'payments_data', title: 'Закон РК о платежах и платёжных системах № 11-VI', issuer: 'Республика Казахстан', version: '', scope: 'Платежи и платёжные системы', source_url: 'https://adilet.zan.kz/rus/docs/Z1600000011' } },
  { id: 'personal-data', label: 'РК: персональные данные', metadata: { category: 'payments_data', title: 'Закон РК о персональных данных и их защите № 94-V', issuer: 'Республика Казахстан', version: '', scope: 'Персональные данные и их защита', source_url: 'https://adilet.zan.kz/rus/docs/Z1300000094' } },
  { id: 'financial-security', label: 'РК: информационная безопасность', metadata: { category: 'security_regulation', title: 'Требования к обеспечению информационной безопасности в финансовой сфере, № 38336', issuer: 'АРРФР Республики Казахстан', version: '', scope: 'Информационная безопасность в финансовой сфере', source_url: 'https://adilet.zan.kz/rus/docs/V2600038336' } },
  { id: 'continuity', label: 'РК: непрерывность инфраструктуры', metadata: { category: 'security_regulation', title: 'Требования к непрерывности работы информационно-коммуникационной инфраструктуры, № 38303', issuer: 'АРРФР Республики Казахстан', version: '', scope: 'Непрерывность работы инфраструктуры финансовых организаций', source_url: 'https://adilet.zan.kz/rus/docs/V2600038303' } },
]

export function createReferenceDraft(file: File): ReferenceDraft {
  return { id: crypto.randomUUID(), file, metadata: { category: '', title: file.name.slice(0, 200), issuer: '', version: '', scope: '', source_url: null } }
}

export function referenceFileError(files: readonly Pick<File, 'name' | 'size'>[]): string | undefined {
  if (files.length > AGENT_REFERENCE_MAX_FILES) return 'Можно приложить до 8 дополнительных документов.'
  for (const file of files) {
    const extension = /\.([^.]+)$/.exec(file.name)?.[1]?.toLowerCase()
    if (!AGENT_SPEC_REVIEW_FORMATS.some(format => format === extension) || file.size <= 0 || file.size > AGENT_REFERENCE_MAX_BYTES) return 'Используйте непустые PDF, DOCX, XLSX, CSV, TXT, MD, JPG или PNG до 5 МиБ каждый.'
  }
  if (files.reduce((total, file) => total + file.size, 0) > AGENT_REFERENCE_TOTAL_MAX_BYTES) return 'Общий размер дополнительных документов не должен превышать 10 МиБ.'
  return undefined
}

const fieldLabels: Record<string, string> = { category: 'категорию', title: 'название', issuer: 'орган или владельца', version: 'версию или дату редакции', scope: 'область действия', source_url: 'ссылку (HTTP или HTTPS)' }

export function referenceSelectionError(references: readonly ReferenceUpload[]): string | undefined {
  const fileError = referenceFileError(references.map(({ file }) => file))
  if (fileError) return fileError
  for (const [index, { metadata }] of references.entries()) {
    const parsed = agentReferenceMetadataSchema.safeParse(metadata)
    if (!parsed.success) {
      const field = String(parsed.error.issues[0]?.path[0] ?? '')
      return `Источник ${index + 1}: проверьте ${fieldLabels[field] ?? 'метаданные'}. Неизвестные поля можно указать как «Не указано».`
    }
  }
  return undefined
}

export type OriginalReferenceSource = AgentReferenceReview['sources'][number]
export type OriginalReferenceMatch = { sourceId: string; sha256: string; file: File }
export type OriginalReferenceRestore = { matches: OriginalReferenceMatch[]; rejected: string[]; error?: string }

type ReferenceRecheckSelection =
  | { mode: 'blocked'; missing: OriginalReferenceSource[]; error?: string }
  | { mode: 'with-references' | 'document-only'; missing: OriginalReferenceSource[]; references: ReferenceUpload[] }

function matchingOriginal(source: OriginalReferenceSource, matches: readonly OriginalReferenceMatch[]) {
  return matches.find(match => match.sourceId === source.id && match.sha256 === source.sha256)
}

/** Identify original bytes locally. Keep no rejected File objects. */
export async function matchOriginalReferenceFiles(
  sources: readonly OriginalReferenceSource[], files: readonly File[], previous: readonly OriginalReferenceMatch[] = [],
): Promise<OriginalReferenceRestore> {
  const matches = new Map<string, OriginalReferenceMatch>()
  for (const source of sources) {
    const match = matchingOriginal(source, previous)
    if (match) matches.set(source.id, match)
  }
  const result = (rejected: string[], error?: string): OriginalReferenceRestore => ({
    matches: sources.flatMap(source => { const match = matches.get(source.id); return match ? [match] : [] }), rejected, error,
  })
  // Bound memory before reading candidate files. Their names do not establish identity.
  if (files.length > AGENT_REFERENCE_MAX_FILES) return result([], 'Выберите до 8 файлов за раз.')
  if (files.some(file => file.size <= 0 || file.size > AGENT_REFERENCE_MAX_BYTES)) return result([], 'Выберите непустые файлы до 5 МиБ каждый.')
  if (files.reduce((total, file) => total + file.size, 0) > AGENT_REFERENCE_TOTAL_MAX_BYTES) return result([], 'Общий размер выбранных файлов не должен превышать 10 МиБ.')
  if (!globalThis.crypto?.subtle) return result([], 'Проверка SHA-256 недоступна. Откройте приложение в защищённом контексте браузера.')
  const rejected: string[] = []
  for (const file of files) {
    try {
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
      const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
      const matching = sources.filter(source => source.sha256 === sha256)
      if (!matching.length) rejected.push(file.name)
      for (const source of matching) {
        // Preserve the original name/format while keeping the exact matched bytes.
        const original = new File([file], source.filename, { type: 'application/octet-stream' })
        matches.set(source.id, { sourceId: source.id, sha256, file: original })
      }
    } catch {
      // A read or digest failure must never be treated as a match.
      rejected.push(file.name)
    }
  }
  return result(rejected)
}

/** Only a complete original reference set, or an explicit document-only choice, may be submitted. */
export function referenceRecheckSelection(
  sources: readonly OriginalReferenceSource[], matches: readonly OriginalReferenceMatch[], documentOnly: boolean,
): ReferenceRecheckSelection {
  const missing = sources.filter(source => !matchingOriginal(source, matches))
  if (!sources.length || documentOnly) return { mode: 'document-only', missing, references: [] }
  if (missing.length) return { mode: 'blocked', missing }
  const references: ReferenceUpload[] = sources.map(source => {
    const { category, title, issuer, version, scope, source_url } = source
    const match = matchingOriginal(source, matches)!
    const file = match.file
    return { file, metadata: { category, title, issuer, version, scope, source_url } }
  })
  const error = referenceSelectionError(references)
  return error ? { mode: 'blocked', missing, error } : { mode: 'with-references', missing, references }
}
