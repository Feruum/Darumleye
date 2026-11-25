import { agentSpecFindingId, type AgentReviewEdit, type AgentReviewFix, type AgentSpecReview } from '@web-app-demo/contracts'
import { InformationCircleIcon, Tick02Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useId, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { Textarea } from '@/components/interface/textarea'
import { Typography } from '@/components/typography'
import type { AuthenticatedTransport } from '@/platform/api'
import { proposeReviewFix } from './api'
import { cancelRepeatReview, createRepeatReviewJobStore, resumeRepeatReview, submitRepeatReview } from './repeat-review-job'
import { completedReviewQueryOptions } from './queries'
import { downloadReview, formatEvidenceLocation } from './review-report'
import { applyReviewEdits, canFixReview, reviewFixErrorMessage, revisedSpecificationFilename } from './review-fix'
import { matchOriginalReferenceFiles, referenceRecheckSelection, type OriginalReferenceRestore, type OriginalReferenceSource, type ReferenceUpload } from './reference-sources'

const verdicts = { ready: 'Готово', needs_revision: 'Нужна доработка', not_ready: 'Не готово' }
const severities = { critical: 'Критично', high: 'Высокая', medium: 'Средняя', low: 'Низкая' }
const emptySources: readonly OriginalReferenceSource[] = []
const emptyReferences: readonly ReferenceUpload[] = []
const emptyRestore: OriginalReferenceRestore = { matches: [], rejected: [] }

function downloadDraft(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function ReviewComparison({ original, revised }: { original: AgentSpecReview; revised: AgentSpecReview }) {
  const rows = [
    ['Требования', original.statistics.requirements, revised.statistics.requirements],
    ['Замечания', original.statistics.violations, revised.statistics.violations],
    ['Критичные', original.statistics.critical, revised.statistics.critical],
    ['Нужна доработка', original.statistics.needs_revision, revised.statistics.needs_revision],
    ['Готовность', verdicts[original.verdict.status], verdicts[revised.verdict.status]],
  ]
  const originalReferences = original.reference_review
  const revisedReferences = revised.reference_review
  const comparableReferences = originalReferences?.status === 'completed' && revisedReferences?.status === 'completed' &&
    originalReferences.sources.length === revisedReferences.sources.length &&
    originalReferences.sources.every((source, index) => source.sha256 === revisedReferences.sources[index]?.sha256)
  if (comparableReferences) rows.push(['Замечания по регламентам', originalReferences.observations.length, revisedReferences.observations.length])
  return <div className="sr-fix-comparison" data-testid="fix-comparison">
    <Typography as="h3" variant="h3">Результат повторной проверки</Typography>
    <table><thead><tr><Typography as="th" scope="col" variant="bodySmMedium">Показатель</Typography><Typography as="th" scope="col" variant="bodySmMedium">До</Typography><Typography as="th" scope="col" variant="bodySmMedium">После</Typography></tr></thead>
      <tbody>{rows.map(([label, before, after]) => <tr key={label}><Typography as="th" scope="row" variant="bodySmMedium">{label}</Typography><Typography as="td" variant="bodySm">{before}</Typography><Typography as="td" variant="bodySm">{after}</Typography></tr>)}</tbody></table>
    <Typography as="p" variant="bodySm">Сравнение основных показателей двух независимых проверок. Число замечаний может измениться из-за различий в анализе модели; это не подтверждение закрытия каждого исходного дефекта.</Typography>
    {originalReferences && !revisedReferences && <Alert><AlertDescription>В новом отчёте нет проверки по регламентам. Сравнение доступно только для проверки качества ТЗ.</AlertDescription></Alert>}
    {originalReferences && revisedReferences && !comparableReferences && <Alert><AlertDescription>Сравнение замечаний по регламентам недоступно: проверка материалов не завершена или набор источников отличается.</AlertDescription></Alert>}
    {original.passport.model !== revised.passport.model && <Typography as="p" variant="caption">В проверках использованы разные модели.</Typography>}
    <details><Typography as="summary" variant="bodySmMedium">Замечания после доработки · {revised.findings.length}</Typography>
      <ul>{revised.findings.map((finding, index) => <Typography as="li" variant="bodySm" key={index}><Typography as="span" variant="caption" className="sr-tag" data-tone={finding.severity}>{severities[finding.severity]}</Typography> {finding.issue}</Typography>)}</ul>
    </details>
    <div className="sr-fix-actions">
      {revised.passport.run_id && <Button asChild><Link to="/app/assistant" search={{ reviewId: revised.passport.run_id }}>Открыть новый отчёт</Link></Button>}
      <Button type="button" variant="outline" onClick={() => downloadReview(revised)}>Скачать новый отчёт</Button>
    </div>
  </div>
}

export function ReviewReferenceRestorePanel({ sources, restore, restoring, documentOnly, disabled, onSelectFiles, onDocumentOnly }: {
  sources: readonly OriginalReferenceSource[]
  restore: OriginalReferenceRestore
  restoring: boolean
  documentOnly: boolean
  disabled: boolean
  onSelectFiles: (files: File[]) => void
  onDocumentOnly: (value: boolean) => void
}) {
  const inputId = useId()
  const selection = referenceRecheckSelection(sources, restore.matches, false)
  const missing = new Set(selection.missing.map(source => source.id))
  const busy = restoring || disabled
  return <div className="sr-reference-input grid gap-2" aria-label="Регламенты исходной проверки" data-testid="fix-reference-restore">
    <Typography as="h3" variant="bodySmMedium">Регламенты исходной проверки</Typography>
    <Typography as="p" variant="caption">Выберите оригинальные файлы для повторного сопоставления. Совпадение проверяется локально по SHA-256; совпавшие файлы отправляются только при запуске проверки.</Typography>
    <Typography as="p" variant="caption" role="status">{restoring ? 'Сверяем файлы с исходной проверкой…' : `Восстановлено ${sources.length - missing.size} из ${sources.length}`}</Typography>
    <ul className="grid gap-2">{sources.map(source => <li className="flex flex-wrap items-start justify-between gap-2" key={source.id}>
      <div className="min-w-0 break-words"><Typography as="p" variant="bodySmMedium">{source.title}</Typography><Typography as="p" variant="caption">{source.filename}</Typography></div>
      <Typography as="span" variant="caption" className="sr-tag" data-tone={missing.has(source.id) ? 'medium' : 'pass'}>{missing.has(source.id) ? 'Требуется оригинал' : 'Восстановлен'}</Typography>
    </li>)}</ul>
    <input id={inputId} aria-label="Оригиналы регламентов исходной проверки" type="file" className="sr-only" multiple accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.jpg,.jpeg,.png" disabled={busy} onChange={event => {
      const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ''; if (files.length) onSelectFiles(files)
    }} />
    <label htmlFor={inputId} className="sr-reference-add" data-disabled={busy}><Typography as="span" variant="caption">Выбрать оригиналы</Typography></label>
    {restore.rejected.length > 0 && <Alert><AlertDescription>Не совпали по SHA-256 или не прочитаны; эти файлы не будут отправлены: {restore.rejected.join(', ')}.</AlertDescription></Alert>}
    {(restore.error || (selection.mode === 'blocked' && selection.error)) && <Alert variant="destructive"><AlertDescription>{restore.error || (selection.mode === 'blocked' && selection.error)}</AlertDescription></Alert>}
    <label className="flex items-start gap-2"><input className="mt-0.5" type="checkbox" checked={documentOnly} disabled={busy} onChange={event => onDocumentOnly(event.currentTarget.checked)} /><Typography as="span" variant="bodySm">Проверить только качество ТЗ, без регламентов</Typography></label>
    {documentOnly && <Typography as="p" variant="caption">Результаты проверок по регламентам не будут сопоставимы.</Typography>}
  </div>
}

function ReviewFixClarification({ question, explanation }: { question: string | null; explanation: string }) {
  return <div className="sr-fix-question" role="status" data-testid="fix-clarification">
    <span className="sr-fix-question-icon"><HugeiconsIcon icon={InformationCircleIcon} size={24} strokeWidth={1.7} aria-hidden /></span>
    <Typography as="p" variant="caption" className="sr-fix-question-label">Нужно решение аналитика</Typography>
    <Typography as="h3" variant="h4" className="sr-fix-question-title">{question}</Typography>
    <Typography as="p" variant="bodySm" className="sr-fix-question-explanation">{explanation}</Typography>
  </div>
}

export function ReviewFixFindingList({ findings, selected, accepted, declined, proposals, disabled, onSelect }: {
  findings: AgentSpecReview['findings']
  selected: number
  accepted: Record<string, AgentReviewEdit[]>
  declined: Record<string, boolean>
  proposals: Record<string, AgentReviewFix>
  disabled: boolean
  onSelect: (index: number) => void
}) {
  const acceptedCount = findings.filter((_, index) => accepted[agentSpecFindingId(index)]).length
  return <aside className="sr-panel sr-fix-queue" aria-label="Замечания для доработки">
    <div className="sr-fix-queue-heading">
      <Typography as="h2" variant="bodySmMedium">Замечания</Typography>
      {acceptedCount > 0
        ? <span className="sr-fix-queue-progress" title="Принятые правки"><HugeiconsIcon icon={Tick02Icon} size={15} strokeWidth={2.2} aria-hidden /><Typography as="span" variant="caption">{acceptedCount} из {findings.length}</Typography></span>
        : <Typography as="span" variant="caption" className="sr-fix-count">{findings.length}</Typography>}
    </div>
    <div className="sr-fix-queue-list">
      {findings.map((finding, index) => {
        const id = agentSpecFindingId(index)
        const state = accepted[id] ? 'accepted' : declined[id] ? 'declined' : proposals[id]?.status === 'clarification' ? 'clarification' : proposals[id] ? 'proposal' : 'pending'
        const status = state === 'declined' ? 'Отклонено' : state === 'clarification' ? 'Уточнить' : state === 'proposal' ? 'Предложение' : undefined
        return <button key={id} type="button" className="sr-fix-queue-item" aria-pressed={selected === index} data-state={state} disabled={disabled} data-testid="fix-finding" onClick={() => onSelect(index)}>
          <span className="sr-fix-queue-meta">
            <Typography as="span" variant="caption" className="sr-fix-queue-id">{id}</Typography>
            <Typography as="span" variant="caption" className="sr-tag" data-tone={finding.severity}>{severities[finding.severity]}</Typography>
            {state === 'accepted' && <span className="sr-fix-queue-accepted"><HugeiconsIcon icon={Tick02Icon} size={14} strokeWidth={2.3} aria-hidden /><Typography as="span" variant="caption">Принято</Typography></span>}
          </span>
          <Typography as="span" variant="bodySmMedium" className="sr-fix-queue-issue">{finding.issue}</Typography>
          {status && <Typography as="span" variant="caption" className="sr-fix-queue-status">{status}</Typography>}
        </button>
      })}
    </div>
  </aside>
}

export function ReviewFixWorkspace({ active, report, transport, ownerId, archivedId, references = emptyReferences, onVerified }: {
  active: boolean
  report: AgentSpecReview
  transport: AuthenticatedTransport
  ownerId?: string
  archivedId?: string
  references?: readonly ReferenceUpload[]
  onVerified?: (report: AgentSpecReview) => void
}) {
  const reviewId = archivedId ?? report.passport.run_id
  const [repeatStore] = useState(() => createRepeatReviewJobStore(ownerId, reviewId))
  const [pendingRepeat, setPendingRepeat] = useState(() => repeatStore.read())
  const [selected, setSelected] = useState(0)
  const [proposals, setProposals] = useState<Record<string, AgentReviewFix>>({})
  const [accepted, setAccepted] = useState<Record<string, AgentReviewEdit[]>>({})
  const [declined, setDeclined] = useState<Record<string, boolean>>({})
  const [clarifications, setClarifications] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<'proposal' | 'verify' | 'cancel'>()
  const [error, setError] = useState<string>()
  const [revised, setRevised] = useState<AgentSpecReview>()
  const [manualRestore, setManualRestore] = useState<OriginalReferenceRestore>()
  const [retainedResult, setRetainedResult] = useState<{ sources: readonly OriginalReferenceSource[]; references: readonly ReferenceUpload[]; restore: OriginalReferenceRestore }>()
  const [restoringManualFiles, setRestoringManualFiles] = useState(false)
  const [documentOnly, setDocumentOnly] = useState(false)
  const restoreAttempt = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const originalSources = report.reference_review?.sources ?? emptySources
  const retainedCurrent = retainedResult?.sources === originalSources && retainedResult.references === references
  const retainedRestore = retainedCurrent ? retainedResult.restore : emptyRestore
  const restoringReferences = restoringManualFiles || (active && originalSources.length > 0 && references.length > 0 && !retainedCurrent)
  const referenceRestore = {
    matches: [...(manualRestore?.matches ?? []), ...retainedRestore.matches.filter(match => !manualRestore?.matches.some(manual => manual.sourceId === match.sourceId && manual.sha256 === match.sha256))],
    rejected: manualRestore?.rejected ?? retainedRestore.rejected,
    error: manualRestore ? manualRestore.error : retainedRestore.error,
  }
  const source = useQuery({
    ...completedReviewQueryOptions(transport, ownerId, reviewId ?? ''),
    enabled: active && Boolean(reviewId) && Boolean(ownerId),
  })
  useEffect(() => () => { controller.current?.abort(); restoreAttempt.current++ }, [])
  useEffect(() => {
    if (!active || !originalSources.length || !references.length) return
    let cancelled = false
    void matchOriginalReferenceFiles(originalSources, references.map(reference => reference.file)).then(result => {
      if (!cancelled) setRetainedResult({ sources: originalSources, references, restore: result })
    })
    return () => { cancelled = true }
  }, [active, originalSources, references])

  if (!canFixReview(report, reviewId ?? undefined)) return null

  const id = agentSpecFindingId(selected)
  const finding = report.findings[selected]!
  const reportQuestion = report.clarification_questions?.find(question => question.finding_id === id)
  const proposal = proposals[id]
  const units = source.data?.source_units
  const approvedCount = Object.keys(accepted).length
  let draft: string | undefined
  let draftError: string | undefined
  if (units && approvedCount) {
    try {
      draft = applyReviewEdits(units, Object.values(accepted).flat())
    } catch (reason) {
      draftError = reason instanceof Error ? reason.message : 'Не удалось собрать документ.'
    }
  }
  const filename = revisedSpecificationFilename(report.filename)
  const hasAccepted = Boolean(accepted[id])
  const inputsLocked = Boolean(busy || pendingRepeat)
  const referenceSelection = referenceRecheckSelection(originalSources, referenceRestore.matches, documentOnly)

  async function restoreOriginalFiles(files: File[]) {
    if (restoringReferences || controller.current || pendingRepeat) return
    const attempt = ++restoreAttempt.current
    setRestoringManualFiles(true)
    setDocumentOnly(false)
    const result = await matchOriginalReferenceFiles(originalSources, files, referenceRestore.matches)
    if (restoreAttempt.current !== attempt) return
    setManualRestore(result)
    setRestoringManualFiles(false)
  }

  async function generate() {
    if (!reviewId || !units || controller.current || hasAccepted || pendingRepeat) return
    const request = new AbortController()
    controller.current = request
    setBusy('proposal')
    setError(undefined)
    try {
      const result = await proposeReviewFix(transport, reviewId, { finding_id: id, clarification: clarifications[id] ?? '' }, request.signal)
      if (!request.signal.aborted) {
        if (result.status === 'patch') applyReviewEdits(units, result.edits)
        setProposals(previous => ({ ...previous, [id]: result }))
        setDeclined(previous => ({ ...previous, [id]: false }))
      }
    } catch (reason) {
      if (!request.signal.aborted) setError(reviewFixErrorMessage(reason, 'proposal'))
    } finally {
      if (controller.current === request) {
        controller.current = null
        setBusy(undefined)
      }
    }
  }

  function accept() {
    if (!units || proposal?.status !== 'patch' || pendingRepeat) return
    const next = { ...accepted, [id]: proposal.edits }
    try {
      applyReviewEdits(units, Object.values(next).flat())
      setAccepted(next)
      setDeclined(previous => ({ ...previous, [id]: false }))
      setRevised(undefined)
      setError(undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось применить правку.')
    }
  }

  function reject() {
    if (pendingRepeat) return
    const next = { ...accepted }
    delete next[id]
    setAccepted(next)
    setDeclined(previous => ({ ...previous, [id]: true }))
    setRevised(undefined)
    setError(undefined)
  }

  function completeRepeat(result: AgentSpecReview | undefined) {
    if (result) {
      setRevised(result)
      onVerified?.(result)
    } else setError('Повторная проверка отменена. Принятые правки сохранены в текущем сеансе.')
  }

  async function verify() {
    if (controller.current || pendingRepeat?.cancelling || (!pendingRepeat && (!draft || restoringReferences || referenceSelection.mode === 'blocked'))) return
    const request = new AbortController()
    controller.current = request
    setBusy('verify')
    setError(undefined)
    setRevised(undefined)
    try {
      let result: AgentSpecReview | undefined
      if (pendingRepeat) {
        result = await resumeRepeatReview(repeatStore, transport, pendingRepeat.id, request.signal)
      } else {
        if (!draft || referenceSelection.mode === 'blocked') return
        const file = new File([draft], filename, { type: filename.endsWith('.md') ? 'text/markdown' : 'text/plain' })
        const id = crypto.randomUUID()
        const verification = submitRepeatReview(repeatStore, transport, id, file, request.signal, referenceSelection.references, report.passport.review_mode)
        setPendingRepeat(repeatStore.read())
        result = await verification
      }
      if (!request.signal.aborted) completeRepeat(result)
    } catch (reason) {
      if (!request.signal.aborted) setError(reviewFixErrorMessage(reason, 'verify'))
    } finally {
      if (controller.current === request) {
        controller.current = null
        setPendingRepeat(repeatStore.read())
        setBusy(undefined)
      }
    }
  }

  async function cancelVerification() {
    const pending = repeatStore.read()
    if (!pending || busy === 'cancel') return
    const previous = controller.current
    const request = new AbortController()
    controller.current = request
    setBusy('cancel')
    setError(undefined)
    repeatStore.requestCancellation(pending.id)
    setPendingRepeat(repeatStore.read())
    previous?.abort()
    try {
      const result = await cancelRepeatReview(repeatStore, transport, pending.id, request.signal)
      if (!request.signal.aborted) completeRepeat(result)
    } catch (reason) {
      if (!request.signal.aborted) setError(reviewFixErrorMessage(reason, 'verify'))
    } finally {
      if (controller.current === request) {
        controller.current = null
        setPendingRepeat(repeatStore.read())
        setBusy(undefined)
      }
    }
  }

  return <section className="sr-fix" aria-label="Доработка требований" data-testid="review-fix">
    <div className="sr-fix-intro">
      <Typography as="p" variant="bodySm" tone="muted">Выберите замечание, согласуйте исправление и проверьте новую версию. Исходный отчёт не меняется.</Typography>
      <Typography as="span" variant="bodySmMedium" className="sr-fix-progress">{approvedCount} из {report.findings.length} принято</Typography>
    </div>
    {(['pdf', 'docx', 'xlsx', 'csv'] as const).some(format => format === report.source) && <Typography as="p" variant="caption" tone="muted">Доработки экспортируются как отдельная текстовая копия без исходной вёрстки.</Typography>}
    {pendingRepeat && <Alert><AlertDescription>{pendingRepeat.cancelling ? 'Ожидаем подтверждения отмены повторной проверки.' : 'Повторная проверка уже начата. Настройки и документ менять нельзя до получения результата или отмены.'}</AlertDescription><div className="sr-fix-actions">
      {!pendingRepeat.cancelling && !busy && <Button type="button" onClick={() => void verify()}>Возобновить повторную проверку</Button>}
      <Button type="button" variant="outline" disabled={busy === 'cancel'} onClick={() => void cancelVerification()}>{busy === 'cancel' ? 'Подтверждаем отмену…' : pendingRepeat.cancelling ? 'Повторить отмену' : 'Отменить повторную проверку'}</Button>
    </div></Alert>}
    <div className="sr-fix-body">
      {error && !units && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {source.isPending && <Typography as="p" variant="bodySm" role="status">Загружаем исходный текст…</Typography>}
      {source.isError && <Alert variant="destructive"><AlertDescription>Не удалось загрузить исходный текст из архива.</AlertDescription><Button type="button" variant="outline" onClick={() => void source.refetch()}>Повторить</Button></Alert>}
      {units && <div className="sr-fix-layout">
        <ReviewFixFindingList findings={report.findings} selected={selected} accepted={accepted} declined={declined} proposals={proposals} disabled={inputsLocked} onSelect={index => { setSelected(index); setError(undefined) }} />
        <div className="sr-fix-main">
        <div className="sr-panel sr-fix-editor">
        <div className="sr-fix-finding">
          <Typography as="span" variant="caption" className="sr-tag" data-tone={finding.severity}>{id} · {severities[finding.severity]}</Typography>
          <Typography as="h2" variant="h4">{finding.issue}</Typography>
          <Typography as="p" variant="bodySm" tone="muted">{finding.recommendation}</Typography>
        </div>
        {proposal && <div className="sr-fix-proposal">
          {proposal.status === 'clarification'
            ? <ReviewFixClarification question={proposal.question} explanation={proposal.explanation} />
            : <Typography as="p" variant="bodySm">{proposal.explanation}</Typography>}
          {proposal.edits.map((edit, index) => <div key={index}>
            <Typography as="p" variant="caption">{formatEvidenceLocation({ start: edit.unit, end: edit.unit }, report.source)}</Typography>
            <div className="sr-fix-diff">
              <div><Typography as="h4" variant="bodySmMedium">Было</Typography><Typography as="pre" variant="bodySm">{edit.before}</Typography></div>
              <div><Typography as="h4" variant="bodySmMedium">Предлагается</Typography><Typography as="pre" variant="bodySm">{edit.after || 'Удалить фрагмент'}</Typography></div>
            </div>
          </div>)}
        </div>}
        {!hasAccepted && reportQuestion && proposal?.status !== 'clarification' && <ReviewFixClarification question={reportQuestion.question} explanation={`Уточняющий вопрос ${reportQuestion.id}. Ответ будет передан агенту при подготовке правки.`} />}
        {!hasAccepted && <label className="sr-fix-field">
          <Typography as="span" variant="bodySmMedium">Уточнение аналитика (необязательно)</Typography>
          <Textarea value={clarifications[id] ?? ''} maxLength={2000} disabled={inputsLocked} onChange={event => setClarifications(previous => ({ ...previous, [id]: event.target.value }))} placeholder="Укажите согласованное правило или ответ на вопрос" />
        </label>}
        <div className="sr-fix-actions">
          {!hasAccepted && <Button type="button" onClick={() => void generate()} disabled={inputsLocked}>{busy === 'proposal' ? 'Готовим предложение…' : proposal ? 'Уточнить предложение' : 'Предложить исправление'}</Button>}
          {proposal?.status === 'patch' && !hasAccepted && <Button type="button" variant="outline" disabled={inputsLocked} onClick={accept}>Принять правку</Button>}
          {proposal && <Button type="button" variant="ghost" disabled={inputsLocked || (declined[id] && !hasAccepted)} onClick={reject}>{hasAccepted ? 'Отменить принятие' : 'Отклонить'}</Button>}
          {hasAccepted && <Typography as="span" variant="caption" className="sr-tag" data-tone="pass">Принято</Typography>}
          {busy === 'proposal' && <Button type="button" variant="outline" onClick={() => controller.current?.abort()}>Отмена</Button>}
        </div>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        </div>
        <div className="sr-panel sr-fix-verification">
          <Typography as="h3" variant="bodySmMedium">Принятые правки: {approvedCount} из {report.findings.length}</Typography>
          {draftError && <Alert variant="destructive"><AlertDescription>{draftError}</AlertDescription></Alert>}
          {draft && <details><Typography as="summary" variant="bodySmMedium">Предпросмотр доработанного ТЗ</Typography><Typography as="pre" variant="bodySm" className="sr-fix-preview">{draft}</Typography></details>}
          {originalSources.length > 0 && <ReviewReferenceRestorePanel sources={originalSources} restore={referenceRestore} restoring={restoringReferences} documentOnly={documentOnly} disabled={inputsLocked} onSelectFiles={files => void restoreOriginalFiles(files)} onDocumentOnly={setDocumentOnly} />}
          <div className="sr-fix-actions">
            <Button type="button" variant="outline" disabled={!draft || Boolean(busy)} onClick={() => { if (draft) downloadDraft(filename, draft) }}>Скачать доработанное ТЗ</Button>
            <Button type="button" disabled={!draft || inputsLocked || restoringReferences || referenceSelection.mode === 'blocked'} onClick={() => void verify()}>{busy === 'verify' ? 'Проверяем новую версию…' : documentOnly && originalSources.length ? 'Проверить только ТЗ' : 'Проверить повторно'}</Button>
          </div>

        </div>
        </div>
      </div>}
    </div>
    {revised && <ReviewComparison original={report} revised={revised} />}
  </section>
}
