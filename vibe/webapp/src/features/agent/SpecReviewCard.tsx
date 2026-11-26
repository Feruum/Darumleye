import type { AgentSpecReview } from '@web-app-demo/contracts'
import { ArrowDown01Icon, ArrowLeft01Icon, ArrowRight01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery } from '@tanstack/react-query'
import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type FormEvent } from 'react'

import { Alert, AlertDescription } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { ApiRequestError } from '@/platform/api'
import { useAuth } from '@/features/auth'
import { isSupportedSpecification } from './api'
import { ReviewRunning, ReviewSetup, ReviewSteps } from './ReviewSetup'
import { SpecReviewReport } from './SpecReviewReport'
import { canFixReview } from './review-fix'
import { referenceSelectionError, type ReferenceUpload } from './reference-sources'
import { downloadReview } from './review-report'
import { createExampleFile, loadExampleReport } from './review-example'
import { type ReviewPhase } from './review-workflow'
import { useReviewRun } from './review-run-context'
import { readPendingReviewJob } from './review-run'
import { completedReviewQueryOptions } from './queries'
import { needsExtractedReviewSource } from './review-source'
import './spec-review.css'

function reviewErrorMessage(error: unknown) {
  switch (error instanceof ApiRequestError ? error.code : undefined) {
    case 'PAYLOAD_TOO_LARGE': return 'Файл слишком большой. Загрузите ТЗ до 25 МиБ. Дополнительные источники — до 10 МиБ суммарно.'
    case 'REVIEW_TEXT_LIMIT': return 'В документе слишком много текста или страниц. Для ТЗ — до 100 000 знаков, 150 страниц или изображений; фото до 30 Мп. Для дополнительных источников — до 48 000 знаков суммарно.'
    case 'REVIEW_NO_TEXT': return 'В файле не удалось найти или распознать текст. Попробуйте более чёткий скан или исходный документ.'
    case 'REVIEW_INVALID_DOCUMENT': return 'Не удалось прочитать файл. Проверьте формат, отсутствие пароля и корректность документа.'
    case 'LLM_NOT_CONFIGURED': return 'Для проверки ТЗ администратору нужно подключить AI-модель.'
    case 'RATE_LIMITED': return 'AI-сервис временно достиг лимита запросов. Повторите проверку чуть позже.'
    case 'BAD_REQUEST': return 'Проверьте формат файлов и заполнение метаданных дополнительных источников.'
    default: return 'Не удалось получить отчёт. Попробуйте ещё раз.'
  }
}

export function SpecReviewCard({ active = true, onStartFix }: { active?: boolean; onStartFix: (reviewId: string, file: File | undefined, references: readonly ReferenceUpload[]) => void }) {
  const { run, snapshot, submit: startReview } = useReviewRun()
  const { transport, user } = useAuth()
  const { file, references } = snapshot
  const running = snapshot.status === 'running'
  const pendingRecovery = Boolean(snapshot.status === 'failed' && user?.id && readPendingReviewJob(user.id))
  const report = snapshot.status === 'completed' ? snapshot.report : undefined
  const needsSource = Boolean(report && (!file || needsExtractedReviewSource(report.source) || report.ocr_pages?.length))
  const reportId = report?.passport.run_id ?? ''
  const extractedSource = useQuery({ ...completedReviewQueryOptions(transport, user?.id, reportId), enabled: needsSource && Boolean(user?.id && reportId) })
  const [fileError, setFileError] = useState<string>()
  const [referenceError, setReferenceError] = useState<string>()
  const [clock, setClock] = useState(Date.now)
  const [exampleOpen, setExampleOpen] = useState(false)
  const [exampleReport, setExampleReport] = useState<AgentSpecReview>()
  const [exampleFile, setExampleFile] = useState<File>()
  const [exampleError, setExampleError] = useState<string>()
  const [exampleLoading, setExampleLoading] = useState(false)
  const [sampleLoading, setSampleLoading] = useState(false)
  const sampleRequest = useRef<AbortController | null>(null)
  const exampleRequest = useRef<AbortController | null>(null)
  const titleRef = useRef<HTMLDivElement>(null)
  const reduce = useReducedMotion()
  const phase: ReviewPhase = running ? 'running' : report ? 'report' : 'setup'
  const elapsed = snapshot.status === 'running' ? Math.max(0, Math.floor((clock - snapshot.startedAt) / 1000)) : 0
  const view = exampleOpen ? 'example' : phase

  useEffect(() => () => {
    sampleRequest.current?.abort()
    exampleRequest.current?.abort()
  }, [])

  useEffect(() => {
    if (!running) return
    const interval = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [running])

  useEffect(() => {
    if (active && (view === 'report' || view === 'example' || view === 'running')) {
      titleRef.current?.focus({ preventScroll: true })
      titleRef.current?.scrollIntoView({ behavior: 'instant', block: 'nearest' })
    }
  }, [view, active])

  function selectFile(selectedFile?: File) {
    if (run.getSnapshot().status === 'running') return
    // Selecting a real file supersedes any in-flight local demo download.
    sampleRequest.current?.abort()
    sampleRequest.current = null
    setSampleLoading(false)
    if (selectedFile && !isSupportedSpecification(selectedFile)) {
      run.setFile(undefined)
      setFileError('Выберите непустой PDF, DOCX, XLSX, CSV, TXT, MD, JPG или PNG размером до 25 МиБ.')
      return
    }
    run.setFile(selectedFile)
    setFileError(undefined)
  }

  function loadSample() {
    if (sampleRequest.current || run.getSnapshot().status === 'running') return
    const controller = new AbortController()
    sampleRequest.current = controller
    setSampleLoading(true)
    try {
      const sample = createExampleFile()
      if (controller.signal.aborted) return
      sampleRequest.current = null
      selectFile(sample)
      setExampleOpen(false)
    } catch {
      if (!controller.signal.aborted) setFileError('Не удалось загрузить учебный пример. Вы можете выбрать свой документ.')
    } finally {
      if (sampleRequest.current === controller) sampleRequest.current = null
      if (!controller.signal.aborted) setSampleLoading(false)
    }
  }

  function viewExample() {
    setExampleOpen(true)
    if (exampleReport || exampleRequest.current) return
    const controller = new AbortController()
    exampleRequest.current = controller
    setExampleLoading(true)
    setExampleError(undefined)
    try {
      const result = loadExampleReport()
      if (!controller.signal.aborted) {
        setExampleFile(createExampleFile())
        setExampleReport(result)
      }
    } catch {
      if (!controller.signal.aborted) setExampleError('Не удалось открыть демонстрационный отчёт. Попробуйте ещё раз.')
    } finally {
      if (exampleRequest.current === controller) exampleRequest.current = null
      if (!controller.signal.aborted) setExampleLoading(false)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if ((!file && !pendingRecovery) || run.getSnapshot().status === 'running') return
    if (pendingRecovery) { startReview(); return }
    const error = referenceSelectionError(references)
    setReferenceError(error)
    if (error) return
    setClock(Date.now())
    startReview()
  }


  return <section className="sr-workspace sr-workflow" lang="ru" aria-label="Проверка требований">
    <header className={view === 'report' || view === 'example' ? 'sr-heading sr-reader-topbar' : 'sr-heading'}>
      <div ref={titleRef} tabIndex={-1} className="sr-page-title">
        {view === 'report' && <Typography as="p" variant="caption" tone="muted">Проверки / Результат</Typography>}
        <Typography as="h1" variant="h1">{
          view === 'example' ? 'Пример отчёта' : view === 'report' && report ? report.filename : 'Проверка ТЗ'
        }</Typography>
      </div>
      {view === 'report' && report && <div className="sr-heading-actions">
        <Button variant="outline" onClick={() => downloadReview(report)} type="button"><HugeiconsIcon icon={ArrowDown01Icon} size={17} aria-hidden />Скачать отчёт</Button>
        {canFixReview(report) && <Button className="sr-primary sr-press" type="button" onClick={() => { const id = report.passport.run_id; if (id) onStartFix(id, file, references) }}>Доработать ТЗ <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button>}
      </div>}
      {view === 'example' && <Button variant="outline" type="button" onClick={() => setExampleOpen(false)}><HugeiconsIcon icon={ArrowLeft01Icon} size={17} aria-hidden />К моей проверке</Button>}
    </header>
    {(view === 'setup' || view === 'running') && <ReviewSteps hasFile={Boolean(file) || running} phase={phase} />}
    <motion.div key={view} className="sr-view" initial={reduce || view === 'setup' ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduce ? 0 : .2, ease: [0.16, 1, 0.3, 1] }}>
      {view === 'setup' && <ReviewSetup
        file={file} references={references} pendingRecovery={pendingRecovery} reviewMode={snapshot.reviewMode} onReviewModeChange={run.setReviewMode} fileError={fileError} referenceError={referenceError}
        requestError={snapshot.status === 'failed' ? reviewErrorMessage(snapshot.error) : undefined}
        cancelled={snapshot.status === 'cancelled'} loadingExample={sampleLoading} showDateConflictDemo
        onFileSelect={selectFile} onReferencesChange={(next) => { run.setReferences(next); setReferenceError(undefined) }}
        onReferenceError={setReferenceError} onViewExample={() => void viewExample()} onSubmit={submit}
      />}
      {view === 'running' && <ReviewRunning filename={file?.name ?? (snapshot.status === 'running' ? snapshot.filename : undefined) ?? 'Техническое задание'} elapsed={elapsed} hasReferences={references.length > 0} onCancel={run.cancel} />}
      {view === 'report' && report && <SpecReviewReport key={report.passport.run_id ?? report.passport.run_at} report={report} sourceFile={file} references={references} sourceUnits={extractedSource.data?.source_units} sourceLoading={needsSource && Boolean(reportId) && extractedSource.isPending} />}
      {view === 'example' && <>
        <div className="sr-example-banner">
          <div><Typography as="strong" variant="bodySmMedium">Учебный пример</Typography><Typography as="p" variant="bodySm">Не относится к вашей проверке.</Typography></div>
          {!file && <Button type="button" variant="outline" disabled={sampleLoading} onClick={() => void loadSample()}>Взять это ТЗ <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></Button>}
        </div>
        {exampleLoading && <div className="sr-panel sr-example-loading" role="status"><Typography variant="bodySm">Загружаем пример отчёта…</Typography></div>}
        {exampleError && <Alert variant="destructive"><AlertDescription>{exampleError}</AlertDescription><Button type="button" variant="ghost" onClick={() => void viewExample()}>Повторить</Button></Alert>}
        {fileError && <Alert variant="destructive"><AlertDescription>{fileError}</AlertDescription></Alert>}
        {exampleReport && <SpecReviewReport report={exampleReport} sourceFile={exampleFile} />}
      </>}
    </motion.div>
  </section>
}
