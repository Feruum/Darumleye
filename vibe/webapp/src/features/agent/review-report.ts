import { agentSpecFindingId, type AgentSpecDecisionFinding, type AgentSpecReview } from '@web-app-demo/contracts'

export type IndexedReviewFinding = {
  id: string
  finding: AgentSpecReview['findings'][number]
  assessment?: AgentSpecDecisionFinding
}

export type GroupedReviewFinding = IndexedReviewFinding & { members: IndexedReviewFinding[] }

const severityRank = { critical: 3, high: 2, medium: 1, low: 0 }
const normalize = (text: string) => text.trim().replace(/\s+/g, ' ')

function evidenceKey(evidence: AgentSpecReview['findings'][number]['evidence'][number]) {
  return JSON.stringify([evidence.start, evidence.end, normalize(evidence.quote)])
}

function uniqueEvidence(evidence: AgentSpecReview['findings'][number]['evidence']) {
  return [...new Map(evidence.map((item) => [evidenceKey(item), item])).values()]
}

function findingScope(finding: IndexedReviewFinding['finding']) {
  return JSON.stringify([finding.kind, [...new Set(finding.requirement_ids)].sort(), uniqueEvidence(finding.evidence).map(evidenceKey).sort()])
}

function issueSimilarity(left: string, right: string) {
  // Compare word prefixes so Russian inflections do not separate near-identical findings.
  const terms = (value: string) => new Set((value.toLowerCase().replace(/ё/g, 'е').match(/[\p{L}\p{N}]+/gu) ?? [])
    .filter((word) => word.length >= 4 || /^\d/u.test(word)).map((word) => word.slice(0, 5)))
  const a = terms(left)
  const b = terms(right)
  if (!a.size || !b.size) return 0
  return 2 * [...a].filter((word) => b.has(word)).length / (a.size + b.size)
}

function equivalentIssue(left: IndexedReviewFinding['finding'], right: IndexedReviewFinding['finding']) {
  if (findingScope(left) !== findingScope(right)) return false
  if (normalize(left.issue) === normalize(right.issue) && normalize(left.recommendation) === normalize(right.recommendation)) return true
  // Without shared evidence, do not infer that differently worded findings are duplicates.
  if (!left.evidence.length) return false
  const numbers = (value: string) => (value.match(/\d+(?:[.,]\d+)*/g) ?? []).sort().join('|')
  return numbers(left.issue) === numbers(right.issue) && issueSimilarity(left.issue, right.issue) >= 0.75
}

export function groupReviewFindings(entries: readonly IndexedReviewFinding[]): GroupedReviewFinding[] {
  const groups: GroupedReviewFinding[] = []
  for (const entry of entries) {
    // Require every member to match, avoiding accidental chains of similar but distinct issues.
    const existing = groups.find((group) => group.members.every((member) => equivalentIssue(member.finding, entry.finding)))
    if (!existing) {
      groups.push({ ...entry, finding: { ...entry.finding, evidence: uniqueEvidence(entry.finding.evidence) }, members: [entry] })
      continue
    }
    existing.members.push(entry)
    const higherLevel = severityRank[entry.finding.severity] > severityRank[existing.finding.severity]
    const groundedAtSameLevel = entry.finding.severity === existing.finding.severity
      && existing.finding.severity_provisional && entry.finding.severity_provisional === false
    if (higherLevel || groundedAtSameLevel) {
      existing.finding = { ...entry.finding, evidence: uniqueEvidence(entry.finding.evidence) }
      existing.assessment = entry.assessment
    }
  }
  return groups
}

export function findingDisplayTitle(finding: IndexedReviewFinding['finding']) {
  const evidence = finding.evidence.map(({ quote }) => quote).join(' ')
  const unclear = finding.kind === 'ambiguous' || finding.kind === 'missing'
  if (unclear && /тайм[-\s]?аут/iu.test(evidence) && /повтор/iu.test(evidence) && /списан/iu.test(evidence)) {
    if (finding.evidence.length > 1 && /статус|результат/iu.test(finding.issue) && !/двойн|дублир/iu.test(finding.issue)) return 'Неясный статус платежа после тайм-аута'
    if (/неизвест|неопредел|двойн|дублир/iu.test(finding.issue)) return 'Риск двойного списания'
    if (/правил|услов|предел|количеств|попыт/iu.test(finding.issue)) return 'Не заданы условия повторного списания'
  }
  if (finding.kind === 'conflict' && /лимит|порог/iu.test(finding.issue) && /лимит/iu.test(evidence)) return 'Противоречие в лимитах'
  if (finding.kind === 'untestable' && /срок|скорост|времен/iu.test(finding.issue) && /быстро|скорост|времен|срок/iu.test(evidence)) return /оплат|платеж|платёж/iu.test(evidence) ? 'Не определён срок оплаты' : 'Не определён срок выполнения'
  if (unclear && /результат/iu.test(finding.issue) && /результат/iu.test(evidence) && /платеж|платёж|оплат/iu.test(evidence)) return 'Неясный результат платежа'
  const sentence = finding.issue.trim().split(/(?<=[.!?;])\s+|,\s+(?=однако|но|поэтому|при этом|а также)/iu, 1)[0]?.replace(/[.!?;]+$/u, '')
  if (sentence && sentence.length >= 12 && sentence.length <= 110) return sentence
  if (unclear && /лимит|порог/iu.test(finding.issue) && /лимит/iu.test(evidence)) return 'Неясные правила лимита'
  return {
    missing: 'Не хватает информации',
    ambiguous: 'Неясное требование',
    conflict: 'Противоречивые требования',
    untestable: 'Нет критерия проверки',
    other: 'Замечание к требованию',
  }[finding.kind]
}

export function matchGroupedPriorityFinding(priority: string, groups: readonly GroupedReviewFinding[]) {
  const matches = groups.filter(({ members }) => members.some(({ finding }) => normalize(finding.recommendation) === normalize(priority)))
  return matches.length === 1 ? matches[0] : undefined
}

export function reviewFindingsWithDecisions(report: Pick<AgentSpecReview, 'findings' | 'decision_review'>): IndexedReviewFinding[] {
  const assessments = new Map(report.decision_review?.status === 'completed'
    ? report.decision_review.findings.map((assessment) => [assessment.id, assessment] as const)
    : [])
  return report.findings.map((finding, index) => {
    const id = agentSpecFindingId(index)
    return { id, finding, assessment: assessments.get(id) }
  })
}

export function matchPriorityFinding(priority: string, findings: readonly IndexedReviewFinding[]) {
  const matches = findings.filter(({ finding }) => normalize(finding.recommendation) === normalize(priority))
  // A recommendation may describe several findings. Link only an unambiguous match.
  return matches.length === 1 ? matches[0] : undefined
}

export function requirementNeedsRevision(checks: AgentSpecReview['requirements'][number]['checks']) {
  return checks.some(({ status }) => status === 'fail' || status === 'uncertain')
}

export function formatEvidenceLocation(evidence: { start: number; end: number }, source: AgentSpecReview['source']) {
  const single = evidence.start === evidence.end
  const label = source === 'pdf' ? (single ? 'Страница' : 'Страницы') : (source === 'jpg' || source === 'jpeg' || source === 'png') ? (single ? 'Изображение' : 'Изображения') : source === 'docx' ? (single ? 'Блок' : 'Блоки') : source === 'xlsx' ? (single ? 'Строка выгрузки' : 'Строки выгрузки') : (single ? 'Строка' : 'Строки')
  return `${label} ${evidence.start}${single ? '' : `–${evidence.end}`}`
}

export function reviewDownloadFilename(filename: string) {
  const basename = filename.split(/[\\/]/).at(-1)?.replace(/\.[^.]*$/, '').replace(/[<>:"|?*]/g, '').split('').filter((char) => char.charCodeAt(0) >= 32).join('').trim()
  return `${basename && !/^\.+$/.test(basename) ? basename : 'specification'}-review.md`
}

export function downloadReview(report: AgentSpecReview) {
  const url = URL.createObjectURL(new Blob([report.report_markdown], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = reviewDownloadFilename(report.filename)
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
