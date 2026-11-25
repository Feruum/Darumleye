import type { AgentReferenceReview, AgentSpecReview } from '@web-app-demo/contracts'
import { ArrowRight01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'

import { Typography } from '@/components/typography'
import { referenceCategoryLabels } from './reference-sources'
import { formatEvidenceLocation } from './review-report'
import type { ReviewEvidence } from './review-source'

const applicabilityLabels = { relevant: 'Относится к ТЗ', unclear: 'Уточнить применимость', not_applicable: 'Не относится к ТЗ', not_reviewed: 'Не проверен' }
const applicabilityTones = { relevant: 'pass', unclear: 'uncertain', not_applicable: 'not_applicable', not_reviewed: 'uncertain' }
const observationLabels = { potential_discrepancy: 'Возможное несоответствие', missing_evidence: 'Недостаточно информации', unclear_applicability: 'Уточнить применимость' }

function ReferenceQuote({ evidence, source, onShow }: { evidence: ReviewEvidence; source: AgentSpecReview['source']; onShow?: (evidence: ReviewEvidence) => void }) {
  const location = formatEvidenceLocation(evidence, source)
  return onShow ? <button type="button" className="sr-reader-citation" aria-label={`Показать в документе: ${location}`} onClick={() => onShow(evidence)}>
    <span className="sr-reader-citation-label"><Typography as="span" variant="caption">{location}</Typography><Typography as="span" variant="caption" className="sr-reader-jump-text">Показать</Typography><HugeiconsIcon icon={ArrowRight01Icon} size={15} aria-hidden /></span>
    <Typography as="span" variant="bodySm" className="sr-reader-citation-quote">«{evidence.quote}»</Typography>
  </button> : <Typography as="blockquote" variant="bodySm" className="sr-reader-reference-quote"><Typography as="small" variant="caption">{location}</Typography><Typography as="p" variant="bodySm">{evidence.quote}</Typography></Typography>
}

export function ReferenceReviewPanel({ review, specificationSource, onShowEvidence, onShowReference }: { review: AgentReferenceReview; specificationSource: AgentSpecReview['source']; onShowEvidence?: (evidence: ReviewEvidence) => void; onShowReference?: (sourceId: string, evidence: ReviewEvidence) => void }) {
  const sources = new Map(review.sources.map((source) => [source.id, source] as const))
  const coverage = new Map(review.retrieval?.sources.map((source) => [source.source_id, source] as const))
  const partial = review.retrieval?.sources.some((source) => source.partial)
  return <section className="sr-reader-reference-report" aria-label="Регламенты и стандарты" data-testid="spec-reference-review" data-status={review.status}>
    {review.status !== 'completed' ? <div className="sr-reader-empty"><Typography as="h4" variant="bodySmMedium">Не удалось сопоставить регламенты</Typography><Typography as="p" variant="bodySm">Основной отчёт по ТЗ доступен. Выводов по регламентам нет.</Typography></div> : <>
      {partial && <Typography as="p" variant="caption" className="sr-reader-reference-note">Проверена часть материалов. Перед согласованием проверьте остальные разделы самостоятельно.</Typography>}
      <Typography as="p" variant="caption" className="sr-reader-reference-note">Потенциальные расхождения требуют подтверждения специалистом. Это не заключение о юридическом соответствии.</Typography>
      {review.observations.length === 0 && <div className="sr-reader-empty"><Typography as="h4" variant="bodySmMedium">Отдельных замечаний не выявлено</Typography><Typography as="p" variant="bodySm">{partial ? 'В проверенных фрагментах материалов.' : 'В предоставленных документах.'} Это не заключение о соответствии.</Typography></div>}
      {review.observations.map((observation) => {
        const reference = sources.get(observation.source_id)
        if (!reference) return null
        return <article className="sr-reader-reference-entry" key={observation.id}>
          <div className="sr-reader-row-meta"><Typography as="span" variant="caption" className="sr-tag" data-tone="uncertain">{observationLabels[observation.status]}</Typography></div>
          <Typography as="h4" variant="bodySmMedium">{observation.issue}</Typography>
          <div className="sr-reader-reference-group"><Typography as="strong" variant="bodySmMedium">Что уточнить</Typography><Typography as="p" variant="bodySm">{observation.recommendation}</Typography></div>
          <div className="sr-reader-reference-group"><Typography as="strong" variant="bodySmMedium">Где в ТЗ</Typography>{observation.specification_evidence.map((evidence, index) => <ReferenceQuote key={index} evidence={evidence} source={specificationSource} onShow={onShowEvidence} />)}</div>
          <div className="sr-reader-reference-group"><Typography as="strong" variant="bodySmMedium">На что ссылается замечание: {reference.title}</Typography>{observation.reference_evidence.map((evidence, index) => <ReferenceQuote key={index} evidence={evidence} source={reference.source} onShow={onShowReference ? item => onShowReference(reference.id, item) : undefined} />)}</div>
        </article>
      })}
    </>}
    <details className="sr-reader-reference-sources">
      <Typography as="summary" variant="bodySmMedium">Приложенные источники · {review.sources.length}</Typography>
      {review.sources.map((source) => <article className="sr-reader-source-entry" key={source.id}>
        <div className="sr-reader-row-meta"><Typography as="span" variant="caption" className="sr-tag" data-tone={applicabilityTones[source.applicability]}>{applicabilityLabels[source.applicability]}</Typography></div>
        <Typography as="h4" variant="bodySmMedium">{source.title}</Typography>
        <Typography as="p" variant="caption">{referenceCategoryLabels[source.category]} · {source.version}</Typography>
        <Typography as="p" variant="bodySm">{source.summary}</Typography>
        {coverage.get(source.id)?.partial && <Typography as="p" variant="caption">Проверена часть текста источника.</Typography>}
        {source.unreadable_pages.length > 0 && <Typography as="p" variant="caption" tone="destructive">Не прочитаны страницы: {source.unreadable_pages.join(', ')}.</Typography>}
        {Boolean(source.ocr_pages?.length) && <Typography as="p" variant="caption" tone="muted">OCR: {source.ocr_pages?.length} стр. Сверяйте цитаты с оригиналом.</Typography>}
        {Boolean(source.unreadable_units?.length) && <Typography as="p" variant="caption" tone="destructive">Не распознаны изображения в блоках: {source.unreadable_units?.join(', ')}.</Typography>}
        {Boolean(source.ocr_units?.length) && <Typography as="p" variant="caption" tone="muted">OCR изображений: блоки {source.ocr_units?.join(', ')}. Сверяйте цитаты с оригиналом.</Typography>}
        <details><Typography as="summary" variant="bodySmMedium">Об источнике</Typography><dl><Typography as="dt" variant="caption">Орган / владелец</Typography><Typography as="dd" variant="bodyXs">{source.issuer}</Typography><Typography as="dt" variant="caption">Область действия</Typography><Typography as="dd" variant="bodyXs">{source.scope}</Typography></dl>{source.source_url && <a href={source.source_url} target="_blank" rel="noopener noreferrer"><Typography as="span" variant="bodySm">Открыть источник</Typography> <HugeiconsIcon icon={ArrowRight01Icon} size={14} aria-hidden /></a>}</details>
      </article>)}
    </details>
  </section>
}
