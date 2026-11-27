import { agentReviewModeLabels, agentSpecReadinessLabels, type AgentSpecReview } from '@web-app-demo/contracts'
import { ArrowDown01Icon, ArrowLeft01Icon, ArrowRight01Icon, Cancel01Icon, File01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { GlassMascot } from '@web-app-demo/mascot'
import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'

import { Alert, AlertDescription } from '@/components/interface/alert'
import { ArcBadge } from '@/components/arc'
import { Button } from '@/components/interface/button'
import { SearchField } from '@/components/interface/search-field'
import { Typography } from '@/components/typography'
import { ReferenceSourceReader } from './ReferenceSourceReader'
import type { ReferenceUpload } from './reference-sources'
import { ReferenceReviewPanel } from './ReferenceReview'
import { DecisionReviewSummary, FindingDecisionReviewBadge } from './DecisionReview'
import { ReviewSourceDocument } from './ReviewSourceDocument'
import { numberAnnotation, sameEvidence, type ReviewAnnotation, type ReviewEvidence } from './review-source'
import { findingDisplayTitle, formatEvidenceLocation, groupReviewFindings, matchGroupedPriorityFinding, requirementNeedsRevision, reviewFindingsWithDecisions, type GroupedReviewFinding, type IndexedReviewFinding } from './review-report'
import './document-review.css'

const severityGroups = [
  { id: 'critical', label: 'Критичные' },
  { id: 'high', label: 'Высокий приоритет' },
  { id: 'medium', label: 'Средний приоритет' },
  { id: 'low', label: 'Низкий приоритет' },
] as const

const priorities = { critical: 'Критично', high: 'Высокий приоритет', medium: 'Средний приоритет', low: 'Низкий приоритет' }
const priorityTones = { critical: 'danger', high: 'warning', medium: 'warning', low: 'info' } as const
const checkLabels = { pass: 'Соответствует', fail: 'Не соответствует', uncertain: 'Уточнить', not_applicable: 'Не применимо' }
const verdictLabels = { ready: 'Готово к разработке', needs_revision: 'Требуется доработка', not_ready: 'Не готово к разработке' }
const availabilityLabels = { present: 'Присутствует', missing: 'Отсутствует', uncertain: 'Не подтверждено', not_applicable: 'Не применимо' }
const EvidenceGraphExplorer = lazy(() => import('./EvidenceGraphExplorer'))
type ReviewTab = 'findings' | 'requirements' | 'set' | 'references' | 'questions' | 'checklist' | 'graph'
type CitationAction = (evidence: ReviewEvidence) => void

function CitationButton({ evidence, source, active, fragment, onShow, primary = false }: { evidence: ReviewEvidence; source: AgentSpecReview['source']; active?: ReviewEvidence; fragment?: number; onShow: CitationAction; primary?: boolean }) {
  const location = formatEvidenceLocation(evidence, source)
  return <Button type="button" size="sm" variant={primary ? 'default' : 'outline'} data-testid="review-citation" className={primary ? 'sr-citation-link sr-inspector-citation' : 'sr-citation-link'} data-selected={sameEvidence(evidence, active)} onClick={() => onShow(evidence)} title={evidence.quote} aria-label={`Показать в документе: ${location}${fragment ? `, фрагмент ${fragment}` : ''}`}>
    <HugeiconsIcon icon={File01Icon} size={15} aria-hidden />
    <Typography as="span" variant="caption">{primary ? 'Показать в документе' : location}{fragment ? ` · ${fragment}` : ''}</Typography>
    <HugeiconsIcon icon={ArrowRight01Icon} size={14} aria-hidden />
  </Button>
}

function EvidenceLinks({ evidence, source, active, onShow, compact = false, showQuote = true, inspector = false }: { evidence: ReviewEvidence[]; source: AgentSpecReview['source']; active?: ReviewEvidence; onShow: CitationAction; compact?: boolean; showQuote?: boolean; inspector?: boolean }) {
  if (!evidence.length) return <Typography as="p" variant="caption" className="sr-reader-no-citation">Нет прямой цитаты: в документе не хватает информации.</Typography>
  const locations = evidence.map((item) => formatEvidenceLocation(item, source))
  return <div className="sr-evidence-links">
    {!compact && <Typography as="span" variant="caption" className="sr-evidence-label">В документе</Typography>}
    <div className="sr-citation-actions">{evidence.map((item, i) => <CitationButton key={i} evidence={item} source={source} active={active} fragment={locations.filter((location) => location === locations[i]).length > 1 ? locations.slice(0, i + 1).filter((location) => location === locations[i]).length : undefined} onShow={onShow} primary={inspector && evidence.length === 1} />)}</div>
    {(showQuote || inspector) && <details className="sr-evidence-text"><Typography as="summary" variant="caption">{inspector ? 'Доказательства' : evidence.length === 1 ? 'Текст цитаты' : 'Тексты цитат'}</Typography>{evidence.map((item, i) => <Typography key={i} as="blockquote" variant="bodySm">«{item.quote}»</Typography>)}</details>}
  </div>
}

function FindingContent({ entry, source, activeEvidence, onShow, showQuote, omitEvidence, inspector = false }: { entry: IndexedReviewFinding; source: AgentSpecReview['source']; activeEvidence?: ReviewEvidence; onShow: CitationAction; showQuote: boolean; omitEvidence?: ReviewEvidence; inspector?: boolean }) {
  const { finding } = entry
  const evidence = finding.evidence.filter((item) => !sameEvidence(item, omitEvidence))
  return <div className="sr-reader-finding-content">
    {finding.severity_provisional && <Typography as="p" variant="caption">Критичность предварительная. Требуется уточнить последствия дефекта.</Typography>}
    <div><Typography as="h4" variant="bodySmMedium">Проблема</Typography><Typography as="p" variant="bodySm">{finding.issue}</Typography></div>
    <div><Typography as="h4" variant="bodySmMedium">Что исправить</Typography><Typography as="p" variant="bodySm">{finding.recommendation}</Typography></div>
    {finding.criteria?.length ? <Typography as="p" variant="caption">Критерии: {finding.criteria.join(' · ')}</Typography> : null}
    {(!omitEvidence || evidence.length > 0) && <EvidenceLinks evidence={evidence} source={source} active={activeEvidence} onShow={onShow} compact showQuote={showQuote} inspector={inspector} />}
  </div>
}

function FindingRow({ entry, number, selected, source, onSelect, itemRef }: { entry: GroupedReviewFinding; number: number; selected: boolean; source: AgentSpecReview['source']; onSelect: () => void; itemRef?: React.Ref<HTMLElement> }) {
  const { finding } = entry
  const locations = [...new Set(finding.evidence.map(item => formatEvidenceLocation(item, source)))].join(' · ')
  return <article ref={itemRef} className="sr-finding-list-row" data-testid="review-finding-row" data-selected={selected} data-severity={finding.severity}>
    <button type="button" aria-pressed={selected} onClick={onSelect} className="sr-finding-list-button">
      <span className="sr-finding-priority" role="img" title={priorities[finding.severity]} aria-label={priorities[finding.severity]} />
      <Typography as="span" variant="caption" className="sr-finding-id">{numberAnnotation(number)}</Typography>
      <span className="sr-finding-list-copy"><Typography as="span" variant="bodySmMedium">{findingDisplayTitle(finding)}</Typography>{locations && <Typography as="span" variant="caption">{locations}</Typography>}</span>
    </button>
  </article>
}

function FindingInspector({ entry, index, total, number, source, activeEvidence, onShow, showQuote, onPrevious, onNext, onBack }: { entry?: GroupedReviewFinding; index: number; total: number; number?: number; source: AgentSpecReview['source']; activeEvidence?: ReviewEvidence; onShow: CitationAction; showQuote: boolean; onPrevious: () => void; onNext: () => void; onBack: () => void }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0 }, [entry?.id])
  return <section className="sr-reader-inspector" tabIndex={-1} aria-label="Подробности замечания" data-testid="review-finding-detail">
    <div className="sr-inspector-mobile-back"><Button type="button" size="sm" variant="ghost" onClick={onBack}><HugeiconsIcon icon={ArrowLeft01Icon} size={16} aria-hidden />Все замечания</Button></div>
    {entry ? <>
      <div className="sr-inspector-toolbar">
        <Button variant="outline" size="icon-sm" type="button" aria-label="Предыдущее замечание" disabled={index <= 0} onClick={onPrevious}><HugeiconsIcon icon={ArrowLeft01Icon} size={17} aria-hidden /></Button>
        <div className="sr-inspector-position"><Typography as="span" variant="caption" aria-live="polite">Замечание {numberAnnotation(number ?? index + 1)}</Typography><Typography as="span" variant="caption" className="sr-inspector-position-count">{index + 1} из {total}</Typography></div>
        <Button variant="outline" size="icon-sm" type="button" aria-label="Следующее замечание" disabled={index >= total - 1} onClick={onNext}><HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button>
      </div>
      <div className="sr-inspector-scroll" ref={scrollRef}>
        <div className="sr-margin-heading">
          <Typography as="span" variant="caption" tone="muted">{activeEvidence ? 'Отмечено в документе' : 'Замечание по требованиям'}</Typography>
          <span className="sr-margin-mascot" aria-hidden><GlassMascot size={42} followPointer={false} /></span>
        </div>
        <div className="sr-inspector-content" key={entry.id} data-severity={entry.finding.severity}>
          <Typography as="p" variant="caption" className="sr-inspector-ids">{numberAnnotation(number ?? index + 1)} / {entry.members.map(member => member.id).join(' · ')}</Typography>
          <Typography as="h3" variant="h4" className="sr-inspector-title">{findingDisplayTitle(entry.finding)}</Typography>
          <div className="sr-inspector-meta"><ArcBadge size="sm" tone={priorityTones[entry.finding.severity]}>{priorities[entry.finding.severity]}</ArcBadge>{entry.finding.evidence.length === 1 && <Typography as="span" variant="caption" className="sr-inspector-location">{formatEvidenceLocation(entry.finding.evidence[0]!, source)}</Typography>}</div>
          <FindingContent entry={entry} source={source} activeEvidence={activeEvidence} onShow={onShow} showQuote={showQuote} inspector />
        </div>
      </div>
    </> : <div className="sr-reader-empty"><Typography as="p" variant="bodySm">Нет замечаний для разбора.</Typography></div>}
  </section>
}

function ExpandableRow({ title, number, marker, tone, expanded, onSelect, children, itemRef }: { title: string; number?: number; marker?: ReactNode; tone?: AgentSpecReview['findings'][number]['severity']; expanded: boolean; onSelect: () => void; children: ReactNode; itemRef?: React.Ref<HTMLElement> }) {
  const id = useId()
  return <article className="sr-reader-row" ref={itemRef} data-selected={expanded} data-severity={tone}>
    <button type="button" className="sr-reader-row-trigger" aria-expanded={expanded} aria-controls={id} onClick={onSelect}>
      {number !== undefined && <Typography as="span" variant="caption" className="sr-annotation-number" data-severity={tone}>{numberAnnotation(number)}</Typography>}
      <span className="sr-reader-row-copy"><Typography as="span" variant="bodySmMedium" className="sr-reader-row-title">{title}</Typography>{marker && (number !== undefined || expanded) && <Typography as="span" variant="caption" className="sr-reader-row-meta">{marker}</Typography>}</span>
      <HugeiconsIcon icon={ArrowDown01Icon} size={16} className="sr-reader-chevron" aria-hidden />
    </button>
    <div id={id} hidden={!expanded} className="sr-reader-row-body">{expanded && children}</div>
  </article>
}

export function SpecReviewReport({ report, sourceFile, sourceUnits, sourceLoading = false, references }: { report: AgentSpecReview; sourceFile?: File; sourceUnits?: readonly string[]; sourceLoading?: boolean; references?: readonly ReferenceUpload[] }) {
  const [tab, setTab] = useState<ReviewTab>(report.findings.length ? 'findings' : 'requirements')
  const [important, setImportant] = useState(false)
  const [onlyIssues, setOnlyIssues] = useState(false)
  const [query, setQuery] = useState('')
  const [findingQuery, setFindingQuery] = useState('')
  const [mobilePane, setMobilePane] = useState<'document' | 'findings'>('findings')
  const [queueOpen, setQueueOpen] = useState(false)
  const queueToggle = useRef<HTMLButtonElement>(null)
  const queueId = useId()
  const [narrowView, setNarrowView] = useState<'list' | 'detail'>('list')
  const [selectedFindingId, setSelectedFindingId] = useState<string>()
  const [selectedRequirementId, setSelectedRequirementId] = useState<string>()
  const [citation, setCitation] = useState<{ scope: string; evidence: ReviewEvidence }>()
  const [referenceCitation, setReferenceCitation] = useState<{ sourceId: string; evidence: ReviewEvidence }>()
  const referenceSource = referenceCitation ? report.reference_review?.sources.find(source => source.id === referenceCitation.sourceId) : undefined
  const [jump, setJump] = useState(0)
  const [focusMode, setFocusMode] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const analysisScroll = useRef<HTMLDivElement>(null)
  const selectedRow = useRef<HTMLElement>(null)
  const contextScroll = useRef<HTMLDivElement>(null)
  const selectedRequirementRow = useRef<HTMLElement>(null)
  const layoutRef = useRef<HTMLDivElement>(null)
  const mobileSwitchRef = useRef<HTMLDivElement>(null)
  const focusTrigger = useRef<HTMLElement | null>(null)
  const nativeFocus = useRef(false)

  const indexed = useMemo(() => groupReviewFindings(reviewFindingsWithDecisions(report)), [report])
  const showQuote = !sourceFile && !sourceUnits?.length
  const indexedNumbers = new Map(indexed.map((entry, index) => [entry.id, index + 1]))
  const priorityItems = report.verdict.priorities.map((text) => ({ text, entry: matchGroupedPriorityFinding(text, indexed) }))
  const importantFindings = indexed.filter(({ finding }) => finding.severity === 'high' || finding.severity === 'critical')
  const findingSearch = findingQuery.toLocaleLowerCase().trim()
  const filtered = (important ? importantFindings : indexed).filter(({ finding, members }) => [...members.map(member => member.id), findingDisplayTitle(finding), finding.issue, finding.recommendation, ...finding.requirement_ids, ...finding.evidence.flatMap(item => [item.quote, formatEvidenceLocation(item, report.source)])].join(' ').toLocaleLowerCase().includes(findingSearch))
  const groups = severityGroups.map(group => ({ ...group, entries: filtered.filter(({ finding }) => finding.severity === group.id) }))
  const queue = groups.flatMap(group => group.entries)
  const selectedFinding = queue.find(({ id }) => id === selectedFindingId) ?? queue[0]
  const selectedIndex = queue.findIndex(({ id }) => id === selectedFinding?.id)
  const annotations: ReviewAnnotation[] = useMemo(() => indexed.flatMap(({ id, finding }, index) => finding.evidence.map((evidence) => ({ id, number: index + 1, evidence, severity: finding.severity }))), [indexed])
  const issuesById = new Map<string, GroupedReviewFinding[]>()
  for (const entry of indexed) for (const id of entry.finding.requirement_ids) issuesById.set(id, [...(issuesById.get(id) ?? []), entry])
  const needsRevision = (requirement: AgentSpecReview['requirements'][number]) => requirementNeedsRevision(requirement.checks) || Boolean(issuesById.get(requirement.id)?.length)
  const visibleRequirements = report.requirements.filter((requirement) => (!onlyIssues || needsRevision(requirement)) && `${requirement.source_id ?? ''} ${requirement.text}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()))
  const selectedRequirement = visibleRequirements.find(({ id }) => id === selectedRequirementId) ?? visibleRequirements[0]
  const scope = tab === 'findings' ? `finding:${selectedFinding?.id ?? ''}` : tab === 'requirements' ? `requirement:${selectedRequirement?.id ?? ''}` : tab
  const defaultEvidence = tab === 'findings' ? selectedFinding?.finding.evidence[0] : tab === 'requirements' ? selectedRequirement?.evidence : undefined
  const activeEvidence = citation?.scope === scope ? citation.evidence : defaultEvidence

  useEffect(() => {
    if (tab !== 'findings' || !analysisScroll.current || !selectedRow.current) return
    const root = analysisScroll.current
    const row = selectedRow.current
    if (row.offsetTop < root.scrollTop || row.offsetTop + row.offsetHeight > root.scrollTop + root.clientHeight) root.scrollTop = Math.max(0, row.offsetTop - 8)
  }, [selectedFinding?.id, tab, important, findingQuery, agentOpen, queueOpen])

  useEffect(() => {
    if (tab !== 'requirements' || !contextScroll.current || !selectedRequirementRow.current) return
    contextScroll.current.scrollTop = Math.max(0, selectedRequirementRow.current.offsetTop - 8)
  }, [tab, selectedRequirement?.id, agentOpen])

  useEffect(() => {
    if (!focusMode) return
    const layout = layoutRef.current
    const leave = () => {
      nativeFocus.current = false
      setFocusMode(false)
      setAgentOpen(false)
      window.requestAnimationFrame(() => focusTrigger.current?.focus({ preventScroll: true }))
    }
    const fullChange = () => {
      if (document.fullscreenElement === layout) nativeFocus.current = true
      else if (nativeFocus.current) leave()
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.fullscreenElement) leave()
    }
    document.addEventListener('fullscreenchange', fullChange)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('fullscreenchange', fullChange)
      document.removeEventListener('keydown', escape)
    }
  }, [focusMode])

  function toggleFocusMode() {
    if (focusMode) {
      if (document.fullscreenElement === layoutRef.current) void document.exitFullscreen().catch(() => undefined)
      nativeFocus.current = false
      setFocusMode(false)
      setAgentOpen(false)
      window.requestAnimationFrame(() => focusTrigger.current?.focus({ preventScroll: true }))
      return
    }
    focusTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setFocusMode(true)
    setAgentOpen(false)
    setMobilePane('document')
    const layout = layoutRef.current
    if (layout?.requestFullscreen) void layout.requestFullscreen().then(() => { nativeFocus.current = true }).catch(() => undefined)
  }

  function revealMobileReport() {
    if (focusMode || !layoutRef.current || layoutRef.current.clientWidth > 820) return
    window.requestAnimationFrame(() => mobileSwitchRef.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }))
  }

  const onShow: CitationAction = (evidence) => {
    setReferenceCitation(undefined)
    setCitation({ scope, evidence })
    setJump((previous) => previous + 1)
    setMobilePane('document')
    if (focusMode && (tab === 'graph' || (layoutRef.current?.clientWidth ?? 0) <= 820)) setAgentOpen(false)
    revealMobileReport()
  }

  function onShowReference(sourceId: string, evidence: ReviewEvidence) {
    if (!report.reference_review?.sources.some(source => source.id === sourceId)) return
    setReferenceCitation({ sourceId, evidence })
    setJump(previous => previous + 1)
    setMobilePane('document')
    if (focusMode) setAgentOpen(false)
    revealMobileReport()
  }

  function toggleQueue() {
    setQueueOpen(!queueOpen)
    if (!queueOpen) window.requestAnimationFrame(() => analysisScroll.current?.closest('section')?.querySelector('input')?.focus({ preventScroll: true }))
  }

  function chooseFinding(id: string, fromQueue = false) {
    if (fromQueue && queueOpen) window.requestAnimationFrame(() => queueToggle.current?.focus({ preventScroll: true }))
    setQueueOpen(false)
    setReferenceCitation(undefined)
    setTab('findings')
    setMobilePane('findings')
    setNarrowView('detail')
    setSelectedFindingId(id)
    setCitation(undefined)
    setJump((previous) => previous + 1)
    revealMobileReport()
  }

  function openPriority(entry: IndexedReviewFinding) {
    setTab('findings')
    setImportant(false)
    setFindingQuery('')
    chooseFinding(entry.id)
    setMobilePane('findings')
    window.requestAnimationFrame(() => layoutRef.current?.querySelector<HTMLElement>('.sr-reader-inspector')?.focus({ preventScroll: true }))
  }

  function pickAnnotation(annotation: ReviewAnnotation) {
    setQueueOpen(false)
    setTab('findings')
    if (!filtered.some(({ id }) => id === annotation.id)) { setImportant(false); setFindingQuery('') }
    setSelectedFindingId(annotation.id)
    setCitation({ scope: `finding:${annotation.id}`, evidence: annotation.evidence })
    setJump((previous) => previous + 1)
    setMobilePane('findings')
    setNarrowView('detail')
    if (focusMode) setAgentOpen(true)
    revealMobileReport()
  }

  function changeTab(next: ReviewTab) {
    setQueueOpen(false)
    if (next === 'findings') setNarrowView('list')
    if (next === tab) { setMobilePane('findings'); return }
    setReferenceCitation(undefined)
    setTab(next)
    setMobilePane('findings')
    // A section switch should not move the document. Only choosing a specific
    // issue, requirement or citation navigates to its source.
    if (next === 'findings') {
      const nextEvidence = selectedFinding?.finding.evidence[0]
      if (activeEvidence && selectedFinding?.finding.evidence.some(item => sameEvidence(item, activeEvidence))) {
        setCitation({ scope: `finding:${selectedFinding.id}`, evidence: activeEvidence })
      } else {
        setCitation(undefined)
        if (nextEvidence && !sameEvidence(nextEvidence, activeEvidence)) setJump(previous => previous + 1)
      }
    } else {
      const matchingRequirement = next === 'requirements' && activeEvidence ? visibleRequirements.find(({ evidence }) => sameEvidence(evidence, activeEvidence)) : undefined
      if (matchingRequirement) setSelectedRequirementId(matchingRequirement.id)
      const nextScope = next === 'requirements' ? `requirement:${matchingRequirement?.id ?? selectedRequirement?.id ?? ''}` : next
      setCitation(activeEvidence ? { scope: nextScope, evidence: activeEvidence } : undefined)
    }
  }

  function backToFindingList() {
    setNarrowView('list')
    window.requestAnimationFrame(() => selectedRow.current?.querySelector('button')?.focus({ preventScroll: true }))
  }

  const tabs: { id: ReviewTab; label: string; count?: number }[] = [
    { id: 'findings', label: 'Замечания', count: indexed.length },
    { id: 'set', label: 'Итог проверки' },
    { id: 'requirements', label: 'Требования' },
  ]
  if (report.reference_review) tabs.push({ id: 'references', label: 'Регламенты', count: report.reference_review.observations.length })
  const questionCount = (report.clarification_questions?.length ?? 0) + (report.review_notes?.length ?? 0)
  if (questionCount) tabs.push({ id: 'questions', label: report.review_notes?.length ? 'Вопросы и рекомендации' : 'Уточняющие вопросы', count: questionCount })
  if (report.evidence_graph) tabs.push({ id: 'graph', label: 'Граф доказательств' })
  if (report.readiness_checklist?.length) tabs.push({ id: 'checklist', label: 'Чек-лист', count: report.readiness_checklist.length })

  const overviewContent = <div className="sr-overview-inset">
        <div className="sr-overview-detail">
          <div className="sr-overview-conclusion">
            <Typography as="h3" variant="bodySmMedium">Состояние документа</Typography>
            {report.statistics.requirements > 0 && <div className="sr-overview-metric">
              <div><Typography as="strong" variant="h3">{report.statistics.needs_revision}<Typography as="span" variant="bodySm"> / {report.statistics.requirements}</Typography></Typography><Typography as="span" variant="caption">Требуют доработки</Typography></div>
              <progress value={report.statistics.needs_revision} max={report.statistics.requirements} aria-label="Требования, нуждающиеся в доработке" />
            </div>}
            <Typography as="p" variant="bodySm">{report.verdict.reason}</Typography>
            {report.summary !== report.verdict.reason && <Typography as="p" variant="bodySm" className="sr-overview-summary">{report.summary}</Typography>}
          </div>
          {priorityItems.length > 0 && <div className="sr-overview-priorities">
            <Typography as="h3" variant="bodySmMedium">Что исправить в первую очередь</Typography>
            <ol>{priorityItems.map(({ text, entry }, index) => <li key={index}>{entry ? <button type="button" className="sr-overview-action" data-severity={entry.finding.severity} onClick={() => openPriority(entry)} aria-label={`Открыть замечание ${numberAnnotation(indexedNumbers.get(entry.id)!)}: ${text}`}>
              <Typography as="span" variant="caption" className="sr-overview-number">{numberAnnotation(indexedNumbers.get(entry.id)!)}</Typography>
              <span className="sr-overview-action-copy"><Typography as="span" variant="bodySm">{text}</Typography><Typography as="span" variant="caption" className="sr-overview-action-priority">{priorities[entry.finding.severity]}</Typography></span>
              <HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden />
            </button> : <div className="sr-overview-action sr-overview-action-static"><Typography as="span" variant="caption" className="sr-overview-number" aria-hidden>•</Typography><Typography as="p" variant="bodySm">{text}</Typography></div>}</li>)}</ol>
          </div>}
        </div>
  </div>

  return <section className="sr-report sr-reader-report sr-document-review" aria-label="Отчёт по ТЗ" data-testid="spec-review-report">
    {Boolean(report.extraction_gaps?.length) && <Alert><AlertDescription>Полнота извлечения: {report.extraction_gaps?.length} кандидатов на требования не подтверждены реестром. Проверьте источники в разделе «Итог проверки».</AlertDescription></Alert>}
    {report.unreadable_pages.length > 0 && <Alert><AlertDescription>Не удалось прочитать страницы: {report.unreadable_pages.join(', ')}. Проверьте их вручную.</AlertDescription></Alert>}
    {Boolean(report.ocr_pages?.length) && <Alert><AlertDescription>Текст с {report.ocr_pages?.length} стр. распознан с помощью OCR. Сверяйте цитаты с оригиналом: распознавание может содержать ошибки.</AlertDescription></Alert>}
    {Boolean(report.unreadable_units?.length) && <Alert><AlertDescription>Не удалось распознать изображения в блоках: {report.unreadable_units?.join(', ')}. Проверка документа неполная.</AlertDescription></Alert>}
    {Boolean(report.ocr_units?.length) && <Alert><AlertDescription>Текст изображений в блоках {report.ocr_units?.join(', ')} распознан с помощью OCR. Сверяйте цитаты с фотографиями.</AlertDescription></Alert>}
    <div ref={mobileSwitchRef} className="sr-reader-mobile-switch" data-graph-view={tab === 'graph'} aria-label="Область просмотра">
      <button type="button" aria-pressed={mobilePane === 'findings'} onClick={() => setMobilePane('findings')}><Typography as="span" variant="bodySmMedium">{tab === 'graph' ? 'Граф' : 'Отчёт'}</Typography></button>
      <button type="button" aria-pressed={mobilePane === 'document'} onClick={() => setMobilePane('document')}><Typography as="span" variant="bodySmMedium">{referenceSource ? 'Регламент' : 'Документ'}</Typography></button>
    </div>
    <div ref={layoutRef} className="sr-reader-layout" data-tab={tab} data-mobile-pane={mobilePane} data-narrow-view={narrowView} data-focus-mode={focusMode} data-agent-open={agentOpen} data-queue-open={queueOpen}>
      <div className="sr-review-toolbar">
        <div className="sr-tabs sr-reader-tabs" aria-label="Раздел отчёта" data-expanded-tabs={tabs.length > 4}>{tabs.map(({ id, label, count }) => <button type="button" aria-pressed={tab === id} key={id} onClick={() => changeTab(id)}><Typography as="span" variant="bodySmMedium">{label}</Typography>{count !== undefined && id !== 'findings' && <Typography as="span" variant="caption" className="sr-tab-count">{count}</Typography>}</button>)}</div>
        {tab === 'findings' && <Button ref={queueToggle} variant="outline" size="sm" type="button" className="sr-desk-queue-toggle" aria-expanded={queueOpen} aria-controls={queueId} onClick={toggleQueue}>{queueOpen ? 'К замечанию' : `Все замечания · ${indexed.length}`}</Button>}
        <Button variant="ghost" size="sm" type="button" className="sr-toolbar-verdict" data-verdict={report.verdict.status} onClick={() => changeTab('set')} title="Открыть итог проверки"><span aria-hidden className="sr-reader-verdict-dot" /><Typography as="span" variant="caption">{verdictLabels[report.verdict.status]}</Typography></Button>
      </div>
      {referenceSource && referenceCitation ? <ReferenceSourceReader key={`${report.passport.run_id}:${referenceSource.id}`} reviewId={report.passport.run_id ?? undefined} source={referenceSource} evidence={referenceCitation.evidence} jump={jump}
        references={references} mobileVisible={mobilePane === 'document'} focusMode={focusMode} onToggleFocus={toggleFocusMode} /> : <ReviewSourceDocument file={sourceFile} sourceUnits={sourceUnits} sourceLoading={sourceLoading} ocrPages={report.ocr_pages} unreadableUnits={report.unreadable_units} filename={report.filename} source={report.source} evidence={activeEvidence} jump={jump} mobileVisible={mobilePane === 'document'} focusOnJump={tab === 'graph'} annotations={annotations} activeAnnotationId={tab === 'findings' ? selectedFinding?.id : undefined} onAnnotationSelect={pickAnnotation} focusMode={focusMode} onToggleFocus={toggleFocusMode} />}
      {focusMode && <div className="sr-focus-actions"><Button type="button" variant="outline" size="sm" onClick={toggleFocusMode} className="sr-review-full-exit" aria-label="Выйти из полного экрана"><HugeiconsIcon icon={Cancel01Icon} size={15} aria-hidden />Выйти</Button><button type="button" className="sr-focus-agent" aria-label={agentOpen ? 'Только документ' : 'Показать панель проверки'} aria-expanded={agentOpen} onClick={() => { if (!agentOpen && tab === 'graph') setMobilePane('findings'); setAgentOpen((previous) => !previous) }}>
        <span className="sr-focus-agent-mascot" aria-hidden><GlassMascot size={36} spinOnHover /></span>
        <Typography as="span" variant="bodySmMedium">{agentOpen ? 'Только документ' : 'Показать отчёт'}</Typography>
        <Typography as="span" variant="caption" className="sr-focus-agent-count">{indexed.length}</Typography>
      </button></div>}
      <section id={queueId} className="sr-reader-analysis sr-reader-queue" aria-label="Список замечаний" data-testid="review-finding-list">
        <div className="sr-reader-analysis-header">
          <div><Typography as="h3" variant="h4">Замечания</Typography><ArcBadge size="sm" tone="neutral">{indexed.length}</ArcBadge></div>
        </div>
          <div className="sr-findings-search">
            <SearchField label="Поиск замечаний" placeholder="Текст, ID или цитата" value={findingQuery} onValueChange={(value) => { setTab('findings'); setFindingQuery(value); setSelectedFindingId(undefined); setCitation(undefined); setJump(previous => previous + 1) }} />
          </div>
          <div className="sr-reader-toolbar sr-finding-filter">
            <Button variant="ghost" size="sm" type="button" aria-pressed={!important} onClick={() => { if (!important && tab === 'findings') return; setTab('findings'); setImportant(false); setCitation(undefined); setJump((previous) => previous + 1) }}>Все <Typography as="span" variant="caption">{indexed.length}</Typography></Button>
            <Button variant="ghost" size="sm" type="button" aria-pressed={important} onClick={() => { if (important && tab === 'findings') return; setTab('findings'); setImportant(true); setCitation(undefined); setJump((previous) => previous + 1) }}>Важные <Typography as="span" variant="caption">{importantFindings.length}</Typography></Button>
          </div>
        <div className="sr-reader-analysis-scroll" ref={analysisScroll}>
          {groups.map(({ id, label, entries }) => entries.length > 0 && <section className="sr-queue-group" key={id} aria-label={label}>
            <div className="sr-queue-group-heading" data-severity={id}>
              <span className="sr-queue-group-dot" aria-hidden />
              <Typography as="h4" variant="caption">{label}</Typography>
              <Typography as="span" variant="caption">{entries.length}</Typography>
            </div>
            {entries.map((entry) => <FindingRow key={entry.id} entry={entry} number={indexedNumbers.get(entry.id)!} source={report.source} selected={tab === 'findings' && selectedFinding?.id === entry.id} itemRef={selectedFinding?.id === entry.id ? selectedRow : undefined} onSelect={() => chooseFinding(entry.id, true)} />)}
          </section>)}
          {!filtered.length && <div className="sr-reader-empty"><Typography as="h4" variant="bodySmMedium">{findingSearch ? 'Ничего не найдено' : important ? 'Важных замечаний нет' : 'Замечаний не найдено'}</Typography></div>}
        </div>
      </section>
      {tab === 'graph' && report.evidence_graph && <section className="sr-reader-analysis sr-reader-context sr-graph-context" aria-label="Граф доказательств"><Suspense fallback={<Typography as="p" variant="bodySm" role="status">Загружается граф…</Typography>}><EvidenceGraphExplorer graph={report.evidence_graph} report={report} onShowEvidence={onShow} onShowReference={onShowReference} /></Suspense></section>}
      {tab !== 'findings' && tab !== 'graph' && <section className="sr-reader-analysis sr-reader-context" aria-label={tabs.find(item => item.id === tab)?.label} data-testid="review-section-detail">
        <div className="sr-reader-analysis-header"><div><Typography as="h3" variant="h4">{tabs.find(item => item.id === tab)?.label}</Typography></div></div>
        {tab === 'requirements' && <div className="sr-reader-toolbar sr-reader-requirement-toolbar">
          <div className="sr-requirements-search"><SearchField label="Поиск требований" placeholder="Найти требование" value={query} onValueChange={(value) => { setQuery(value); setSelectedRequirementId(undefined); setCitation(undefined); setJump((previous) => previous + 1) }} /></div>
          <label className="sr-reader-checkbox"><input type="checkbox" checked={onlyIssues} onChange={(event) => { setOnlyIssues(event.target.checked); setSelectedRequirementId(undefined); setCitation(undefined); setJump((previous) => previous + 1) }} /><Typography as="span" variant="caption">Только с замечаниями</Typography></label>
        </div>}
        <div className="sr-reader-analysis-scroll" key={tab} ref={contextScroll}>
          {tab === 'requirements' && <>
            {visibleRequirements.map((requirement) => <ExpandableRow key={requirement.id} title={requirement.text} marker={<Typography as="span" variant="caption" className="sr-tag" data-tone={needsRevision(requirement) ? 'uncertain' : 'pass'}>{needsRevision(requirement) ? 'Доработать' : 'Без замечаний'}</Typography>} expanded={selectedRequirement?.id === requirement.id} itemRef={selectedRequirement?.id === requirement.id ? selectedRequirementRow : undefined} onSelect={() => { setSelectedRequirementId(requirement.id); setCitation(undefined); setJump((previous) => previous + 1) }}>
              <div className="sr-reader-finding-content">
                {(issuesById.get(requirement.id) ?? []).map((entry) => <div className="sr-reader-related" key={entry.id}><div className="sr-reader-related-heading"><Typography as="h4" variant="bodySmMedium">{findingDisplayTitle(entry.finding)}</Typography><Typography as="span" variant="caption" className="sr-tag" data-tone={entry.finding.severity}>{priorities[entry.finding.severity]}</Typography></div><FindingContent entry={entry} source={report.source} activeEvidence={activeEvidence} onShow={onShow} showQuote={showQuote} omitEvidence={requirement.evidence} /></div>)}
                <EvidenceLinks evidence={[requirement.evidence]} source={report.source} active={activeEvidence} onShow={onShow} compact showQuote={showQuote} />
                <details className="sr-reader-criteria"><Typography as="summary" variant="bodySmMedium">Критерии качества · {requirement.checks.length}</Typography><div className="sr-reader-checks">{requirement.checks.map((check) => <div key={check.criterion}><div><Typography as="strong" variant="bodySmMedium">{check.criterion}</Typography><Typography as="p" variant="bodySm">{check.comment}</Typography></div><Typography as="span" variant="caption" className="sr-tag" data-tone={check.status}>{checkLabels[check.status]}</Typography></div>)}</div></details>
              </div>
            </ExpandableRow>)}
            {!visibleRequirements.length && <div className="sr-reader-empty"><Typography as="p" variant="bodySm">Нет требований по этому запросу.</Typography></div>}
          </>}
          {tab === 'set' && <>{overviewContent}<Typography as="h4" variant="bodySmMedium" className="sr-set-title">Проверка ТЗ целиком</Typography><div className="sr-reader-checks sr-reader-set-checks">{report.set_checks.map((check) => <article key={check.criterion}><div className="sr-reader-check-heading"><Typography as="h4" variant="bodySmMedium">{check.criterion}</Typography><Typography as="span" variant="caption" className="sr-tag" data-tone={check.status}>{checkLabels[check.status]}</Typography></div><Typography as="p" variant="bodySm">{check.comment}</Typography><EvidenceLinks evidence={check.evidence} source={report.source} active={activeEvidence} onShow={onShow} /></article>)}</div>
            {report.extraction_gaps?.length ? <section className="sr-reader-checks sr-reader-set-checks"><Typography as="h4" variant="bodySmMedium">Полнота извлечения</Typography><EvidenceLinks evidence={report.extraction_gaps} source={report.source} active={activeEvidence} onShow={onShow} /></section> : null}
            <details className="sr-reader-criteria"><Typography as="summary" variant="bodySmMedium">Паспорт прогона и независимая оценка</Typography><div className="sr-reader-checks"><Typography as="p" variant="bodySm">Модель: {report.passport.model}</Typography><Typography as="p" variant="caption">Режим: {agentReviewModeLabels[report.passport.review_mode ?? 'legacy']}</Typography><Typography as="p" variant="caption">Версия: {report.passport.agent_version}</Typography><Typography as="p" variant="caption">Дата: {report.passport.run_at}</Typography>{report.passport.document_sha256 && <Typography as="p" variant="caption" style={{ overflowWrap: 'anywhere' }}>SHA-256: {report.passport.document_sha256}</Typography>}<DecisionReviewSummary audit={report.decision_review} />{report.decision_review?.findings.length ? <div className="sr-reader-checks">{report.decision_review.findings.map(assessment => <FindingDecisionReviewBadge key={assessment.id} assessment={assessment} />)}</div> : null}<Typography as="p" variant="caption">В отчёте {report.findings.length} замечаний; объединено в {indexed.length} карточек.</Typography></div></details>
          </>}
          {tab === 'questions' && <div className="sr-reader-checks sr-reader-set-checks">{report.review_notes?.map(note => (
            <article key={note.observation_ids[0]}>
              <Typography as="h4" variant="bodySmMedium">{note.kind === 'recommendation' ? 'Рекомендация' : 'Вопрос на проверку'}</Typography>
              <Typography as="p" variant="bodySm">{note.text}</Typography>
              <Typography as="p" variant="caption">{note.rationale}</Typography>
              <EvidenceLinks evidence={note.evidence} source={report.source} active={activeEvidence} onShow={onShow} />
              {note.decision_evidence.length > 0 && <><Typography as="p" variant="caption">Контекст решения арбитра</Typography><EvidenceLinks evidence={note.decision_evidence} source={report.source} active={activeEvidence} onShow={onShow} /></>}
            </article>
          ))}{report.clarification_questions?.map(question => <article key={question.id}><div className="sr-reader-check-heading"><Typography as="h4" variant="bodySmMedium">{question.id} · {question.finding_id ?? (question.checklist_id ? agentSpecReadinessLabels[question.checklist_id] : '')}</Typography><Typography as="span" variant="caption" className="sr-tag" data-tone={question.priority}>{priorities[question.priority]}</Typography></div><Typography as="p" variant="bodySm">{question.question}</Typography>{question.requirement_ids.length > 0 && <Typography as="p" variant="caption">{question.requirement_ids.join(' · ')}</Typography>}<EvidenceLinks evidence={question.evidence} source={report.source} active={activeEvidence} onShow={onShow} />{question.finding_id && <Button type="button" variant="outline" size="sm" onClick={() => { const entry = indexed.find(item => item.members.some(member => member.id === question.finding_id)); if (entry) openPriority(entry) }}>Открыть замечание</Button>}</article>)}</div>}
          {tab === 'checklist' && <div className="sr-reader-checks sr-reader-set-checks"><Typography as="p" variant="caption">Оценивается документация, а не факт внешнего согласования или сертификации.</Typography>{report.readiness_checklist?.map(row => <article key={row.id}><div className="sr-reader-check-heading"><Typography as="h4" variant="bodySmMedium">{agentSpecReadinessLabels[row.id]}</Typography><Typography as="span" variant="caption" className="sr-tag" data-tone={row.status}>{checkLabels[row.status]}</Typography></div><Typography as="p" variant="caption">Наличие: {availabilityLabels[row.availability]}</Typography><Typography as="p" variant="bodySm">{row.comment}</Typography>{row.question && <Typography as="p" variant="bodySm">{row.question}</Typography>}<EvidenceLinks evidence={row.evidence} source={report.source} active={activeEvidence} onShow={onShow} /></article>)}</div>}
          {tab === 'references' && report.reference_review && <ReferenceReviewPanel review={report.reference_review} specificationSource={report.source} onShowEvidence={onShow} onShowReference={onShowReference} />}
        </div>
      </section>}
      {tab === 'findings' && <FindingInspector entry={selectedFinding} index={selectedIndex} total={queue.length} number={selectedFinding ? indexedNumbers.get(selectedFinding.id) : undefined} source={report.source} activeEvidence={activeEvidence} onShow={onShow} showQuote={showQuote} onPrevious={() => { const entry = queue[selectedIndex - 1]; if (entry) chooseFinding(entry.id) }} onNext={() => { const entry = queue[selectedIndex + 1]; if (entry) chooseFinding(entry.id) }} onBack={backToFindingList} />}
    </div>
  </section>
}
