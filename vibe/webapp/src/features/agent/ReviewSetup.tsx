import { AGENT_DEFAULT_REVIEW_MODE, agentReviewModeLabels, type AgentReviewSubmissionMode } from '@web-app-demo/contracts'
import {
  ArrowRight01Icon, ArrowUp02Icon, BookOpen01Icon, CheckmarkCircle02Icon,
  Clock01Icon, File01Icon, Search01Icon, Tick02Icon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { motion, useReducedMotion } from 'motion/react'
import { GlassMascot } from '@web-app-demo/mascot'
import { useId, useRef, useState, type FormEvent } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { ReferenceSourcesInput } from './ReferenceSourcesInput'
import { type ReferenceDraft } from './reference-sources'
import { deriveReviewSteps, formatReviewElapsed, type ReviewPhase } from './review-workflow'

const stepLabels = [
  'Документ', 'Настройки', 'Отчёт',
] as const

// Adapted from Skiper UI's free ArrowIcon (Skiper99):
// https://skiper-ui.com/v1/skiper99 — the expanding shaft follows step state rather than hover.
function StepConnector() {
  return <span className="sr-step-arrow" aria-hidden>
    <span className="sr-connector-icon">
      <HugeiconsIcon icon={ArrowRight01Icon} className="sr-connector-head" size={20} strokeWidth={1.9} />
      <span className="sr-connector-shaft" />
    </span>
  </span>
}

export function ReviewSteps({ hasFile, phase }: { hasFile: boolean; phase: ReviewPhase }) {
  const states = deriveReviewSteps(hasFile, phase)
  return <ol className="sr-steps" data-phase={phase} aria-label="Шаги проверки">
    {stepLabels.map((title, index) => <li key={title} data-state={states[index]} aria-current={states[index] === 'current' ? 'step' : undefined}>
      <div className="sr-step-card">
        <span className="sr-step-indicator" aria-hidden>{states[index] === 'done' ? <HugeiconsIcon icon={Tick02Icon} size={16} strokeWidth={2.3} /> : <Typography as="span" variant="control">{index + 1}</Typography>}</span>
        <Typography as="strong" variant="bodySmMedium">{title}</Typography>
      </div>
      {index < stepLabels.length - 1 && <StepConnector />}
    </li>)}
  </ol>
}

type SetupProps = {
  file?: File
  pendingRecovery?: boolean
  reviewMode?: AgentReviewSubmissionMode
  onReviewModeChange?: (mode: AgentReviewSubmissionMode) => void
  references: ReferenceDraft[]
  fileError?: string
  referenceError?: string
  requestError?: string
  cancelled: boolean
  loadingExample: boolean
  onFileSelect: (file?: File) => void
  onReferencesChange: (sources: ReferenceDraft[]) => void
  onReferenceError: (error?: string) => void
  onViewExample: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function ReviewSetup({
  file, pendingRecovery = false, reviewMode = AGENT_DEFAULT_REVIEW_MODE, onReviewModeChange, references, fileError, referenceError, requestError, cancelled, loadingExample,
  onFileSelect, onReferencesChange, onReferenceError, onViewExample, onSubmit,
}: SetupProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const reduce = useReducedMotion()
  if (pendingRecovery) return <form className="sr-panel sr-setup-form" onSubmit={onSubmit}>
    <section className="sr-form-section" aria-label="Возобновление проверки">
      <Alert><AlertTitle>Связь с проверкой прервана</AlertTitle><AlertDescription>Проверка могла продолжиться на сервере. Исходный документ и настройки сохранены при принятии задания. Повторная загрузка не требуется.</AlertDescription></Alert>
      {file && <Typography as="p" variant="bodySm">{file.name}</Typography>}
      <div className="sr-form-footer"><Button className="sr-primary sr-press" type="submit">Возобновить проверку <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button></div>
    </section>
  </form>
  return <div className="sr-setup-layout">
    <form className="sr-panel sr-setup-form" onSubmit={onSubmit}>
      <section className="sr-form-section" aria-labelledby={`${inputId}-document`}>
        <div className="sr-section-heading">
          <div>
            <Typography as="h2" variant="h2" id={`${inputId}-document`}>1. Загрузите техническое задание</Typography>
          </div>
          {file && <Typography as="span" variant="caption" className="sr-tag" data-tone="pass">Выбран</Typography>}
        </div>
        <input ref={inputRef} id={inputId} aria-label="Файл ТЗ" className="sr-only" accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.jpg,.jpeg,.png" onChange={(event) => {
          if (event.currentTarget.files?.[0]) onFileSelect(event.currentTarget.files[0])
          event.currentTarget.value = ''
        }} type="file" />
        <label htmlFor={inputId} className="sr-file-drop" data-dragging={dragging} data-selected={Boolean(file)}
          onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files[0]) onFileSelect(event.dataTransfer.files[0]) }}>
          <span className="sr-file-glyph"><HugeiconsIcon icon={file ? File01Icon : ArrowUp02Icon} size={file ? 25 : 29} strokeWidth={1.8} aria-hidden /></span>
          {file ? <span className="sr-file-description"><Typography as="strong" variant="bodySmMedium">{file.name}</Typography><Typography as="small" variant="caption">{Math.max(1, Math.round(file.size / 1024))} КБ</Typography></span> : <span className="sr-file-description"><Typography as="strong" variant="bodySmMedium">Перетащите файл сюда</Typography><Typography as="small" variant="caption">или нажмите, чтобы выбрать на компьютере</Typography></span>}
          <Typography as="span" variant="controlXs" className="sr-file-action">{file ? 'Заменить' : 'Выбрать файл'}</Typography>
        </label>
        <div className="sr-file-meta">
          <Typography as="p" variant="caption">PDF, DOCX, XLSX, CSV, TXT, MD, JPG, PNG · до 25 МиБ · 150 страниц PDF / 100 000 знаков</Typography>
          {file && <Button type="button" variant="ghost" size="sm" onClick={() => onFileSelect(undefined)}>Убрать файл</Button>}
        </div>
        {fileError && <Alert variant="destructive"><AlertTitle>Файл не подходит</AlertTitle><AlertDescription>{fileError}</AlertDescription></Alert>}
      </section>

      <section className="sr-form-section sr-config-section" aria-labelledby={`${inputId}-settings`}>
        <div className="sr-section-heading">
          <div>
            <Typography as="h2" variant="h2" id={`${inputId}-settings`}>2. Настройте проверку</Typography>
          </div>
        </div>
        <motion.div className="sr-config-body" initial={reduce ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduce ? 0 : .2, ease: [0.16, 1, 0.3, 1] }}>
          <div className="sr-default-check">
            <span className="sr-check-symbol"><HugeiconsIcon icon={CheckmarkCircle02Icon} size={22} aria-hidden /></span>
            <div><Typography as="strong" variant="bodySmMedium">Качество и согласованность ТЗ</Typography></div>
            <Typography as="span" variant="caption" className="sr-default-label">Включено</Typography>
          </div>
          <fieldset className="sr-review-mode-options">
            <Typography as="legend" variant="bodySmMedium">Режим проверки</Typography>
            <label><input type="radio" name={`${inputId}-review-mode`} checked={reviewMode === 'multi_agent'} onChange={() => onReviewModeChange?.('multi_agent')} /><span><Typography as="strong" variant="bodySmMedium">{agentReviewModeLabels.multi_agent}</Typography><Typography as="span" variant="caption">Три парных валидатора сверяют выводы экспертов перед общей проверкой.</Typography></span></label>
            <label><input type="radio" name={`${inputId}-review-mode`} checked={reviewMode === 'multi_agent_direct'} onChange={() => onReviewModeChange?.('multi_agent_direct')} /><span><Typography as="strong" variant="bodySmMedium">{agentReviewModeLabels.multi_agent_direct}</Typography><Typography as="span" variant="caption">Общий валидатор получает выводы каждого эксперта напрямую.</Typography></span></label>
          </fieldset>
          {file && <ReferenceSourcesInput references={references} disabled={false} onChange={onReferencesChange} onFileError={onReferenceError} />}
          {referenceError && <Alert variant="destructive"><AlertTitle>Проверьте источники</AlertTitle><AlertDescription>{referenceError}</AlertDescription></Alert>}
        </motion.div>
        {requestError && <Alert variant="destructive"><AlertTitle>Проверка не завершена</AlertTitle><AlertDescription>{requestError}</AlertDescription>{file && <Button className="sr-error-action" type="button" variant="ghost" size="sm" onClick={() => inputRef.current?.click()}>Выбрать другой документ</Button>}</Alert>}
        {cancelled && <Typography as="p" variant="bodySm" role="status" className="sr-cancel-notice">Ожидание остановлено. Настройки сохранены, проверку можно запустить снова.</Typography>}
        <div className="sr-form-footer">
          <Typography as="small" variant="caption">Документы передаются AI-модели.</Typography>
          <Button className="sr-primary sr-press" disabled={!file || loadingExample} type="submit">Проверить ТЗ <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button>
        </div>
      </section>
    </form>

    <aside className="sr-guide" aria-label="Что будет в отчёте">
      <div className="sr-guide-heading">
        <div><Typography as="h2" variant="h2">Что вы получите</Typography></div>
        <div className="sr-guide-mascot" aria-hidden><GlassMascot size={104} spinOnHover ambient /></div>
      </div>
      <ul className="sr-benefits">
        <li><HugeiconsIcon icon={Search01Icon} size={20} aria-hidden /><div><Typography as="strong" variant="bodySmMedium">Проблемные требования</Typography></div></li>
        <li><HugeiconsIcon icon={BookOpen01Icon} size={20} aria-hidden /><div><Typography as="strong" variant="bodySmMedium">Доказательства из ТЗ</Typography></div></li>
        <li><HugeiconsIcon icon={CheckmarkCircle02Icon} size={20} aria-hidden /><div><Typography as="strong" variant="bodySmMedium">Приоритеты исправлений</Typography></div></li>
      </ul>
      <div className="sr-guide-example">
        <div className="sr-example-label"><Typography as="span" variant="caption">ПРИМЕР ЗАМЕЧАНИЯ</Typography><Typography as="span" variant="caption" className="sr-tag" data-tone="uncertain">Непроверяемо</Typography></div>
        <Typography as="blockquote" variant="bodySm" className="sr-example-quote">«Оплата должна выполняться быстро.»</Typography>
        <Typography as="p" variant="bodySm">Укажите точное время выполнения операции.</Typography>
      </div>
      <Button variant="outline" className="sr-guide-cta sr-press" type="button" onClick={onViewExample} disabled={loadingExample}>Посмотреть пример отчёта <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button>
    </aside>
  </div>
}

export function ReviewRunning({ filename, elapsed, hasReferences, onCancel }: { filename: string; elapsed: number; hasReferences: boolean; onCancel: () => void }) {
  return <section className="sr-panel sr-running" aria-label="Выполняется проверка">
    <div className="sr-running-main">
      <div className="sr-running-mascot" aria-hidden>
        <GlassMascot state="thinking" size={176} ambient spinOnHover />
      </div>
      <div className="sr-running-content">
        <div className="sr-running-status"><span className="sr-live-dot" aria-hidden /><Typography as="span" variant="caption">Проверка выполняется</Typography><Typography as="span" variant="caption" className="sr-elapsed"><HugeiconsIcon icon={Clock01Icon} size={15} aria-hidden /> {formatReviewElapsed(elapsed)}</Typography></div>
        <Typography as="h2" variant="h2" role="status">Анализируем ваше ТЗ</Typography>
        <Typography as="p" variant="bodySm" className="sr-running-filename">{filename}</Typography>
        <Typography as="p" variant="bodySm">{hasReferences ? 'ТЗ и регламенты' : 'Техническое задание'} · Обычно несколько минут</Typography>
        <Typography as="p" variant="bodySm" tone="muted">Можете перейти в другой раздел. Мы сообщим, когда отчёт будет готов.</Typography>
        <Button type="button" variant="outline" onClick={onCancel}>Отменить ожидание</Button>
      </div>
    </div>
  </section>
}
