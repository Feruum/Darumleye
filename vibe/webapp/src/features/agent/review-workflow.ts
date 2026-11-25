import type { IndexedReviewFinding } from './review-report'

export type ReviewPhase = 'setup' | 'running' | 'report'
export type ReviewStepState = 'upcoming' | 'current' | 'done'
export type SeverityFilter = 'all' | IndexedReviewFinding['finding']['severity']
export type AssessmentFilter = 'all' | 'supported' | 'uncertain' | 'unsupported'

export function deriveReviewSteps(hasFile: boolean, phase: ReviewPhase): [ReviewStepState, ReviewStepState, ReviewStepState] {
  if (phase === 'report') return ['done', 'done', 'done']
  if (phase === 'running') return ['done', 'done', 'current']
  return hasFile ? ['done', 'current', 'upcoming'] : ['current', 'upcoming', 'upcoming']
}

export function formatReviewElapsed(seconds: number) {
  const elapsed = Math.max(0, Math.floor(seconds))
  return `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`
}

export function filterReportFindings(entries: readonly IndexedReviewFinding[], severity: SeverityFilter, assessment: AssessmentFilter) {
  return entries.filter((entry) => (severity === 'all' || entry.finding.severity === severity)
    && (assessment === 'all' || entry.assessment?.status === assessment))
}
