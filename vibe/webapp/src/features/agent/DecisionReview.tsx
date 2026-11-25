import type { AgentSpecDecisionFinding, AgentSpecReview } from '@web-app-demo/contracts'
import { Typography } from '@/components/typography'

const labels = {
  supported: 'Поддержано моделью',
  uncertain: 'Нужна перепроверка',
  unsupported: 'Не поддержано моделью',
}
const tones = { supported: 'pass', uncertain: 'uncertain', unsupported: 'fail' }
const statusLabels = { completed: 'Завершена', unavailable: 'Недоступна', not_applicable: 'Не требуется' }

export function DecisionReviewSummary({ audit }: { audit: AgentSpecReview['decision_review'] }) {
  if (!audit) return null
  const counts = { supported: 0, uncertain: 0, unsupported: 0 }
  for (const assessment of audit.findings) counts[assessment.status] += 1

  return <section className="sr-panel sr-summary" aria-label="Независимая проверка Nace Drex" data-testid="spec-decision-review" data-status={audit.status}>
    <Typography as="h3" variant="bodySmMedium">Независимая проверка Nace Drex · {statusLabels[audit.status]}</Typography>
    {audit.status === 'completed' ? <>
      <div className="sr-finding-top">{(Object.keys(counts) as (keyof typeof counts)[]).map((status) => <Typography as="span" variant="caption" className="sr-tag" key={status} data-tone={tones[status]}>{labels[status]}: {counts[status]}</Typography>)}</div>
      <details className="sr-summary-more"><Typography as="summary" variant="bodySmMedium">Как читать оценку Drex</Typography><Typography as="p" variant="caption">Модель: {audit.model}</Typography><Typography as="p" variant="bodySm">{audit.explanation}</Typography></details>
    </> : <Typography as="p" variant="bodySm">{audit.explanation}</Typography>}
  </section>
}

export function FindingDecisionReviewBadge({ assessment }: { assessment?: AgentSpecDecisionFinding }) {
  if (!assessment) return null
  return <Typography as="span" variant="caption" className="sr-tag" data-tone={tones[assessment.status]} title={`Точная оценка: ${String(assessment.probability).replace('.', ',')} из 1`}>
    {assessment.id} · Drex: {labels[assessment.status]} · {new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(assessment.probability * 100)}%
  </Typography>
}
