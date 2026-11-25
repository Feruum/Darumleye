import type { AgentSpecReview } from '@web-app-demo/contracts'
import { ArrowLeft01Icon, ArrowRight01Icon, ArrowUpRight01Icon, Cancel01Icon, File01Icon, FullScreenIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'

import { Typography } from '@/components/typography'
import { Button } from '@/components/interface/button'
import { formatEvidenceLocation } from './review-report'
import { isEvidenceLine, isImageReviewSource, needsExtractedReviewSource, numberAnnotation, paginateReviewText, pdfCitationUrl, quoteFragments, resolveReviewSourcePresentation, sameEvidence, splitReviewLines, type ReviewAnnotation, type ReviewEvidence, type ReviewSourcePresentation } from './review-source'

type Preview = { file: File; url: string; lines?: string[]; error?: boolean }
export type DocumentAnnotationProps = { evidence?: ReviewEvidence; jump: number; annotations: ReviewAnnotation[]; activeAnnotationId?: string; onAnnotationSelect: (annotation: ReviewAnnotation) => void }
const PdfReviewDocument = lazy(() => import('./PdfReviewDocument').then((module) => ({ default: module.PdfReviewDocument })))

function markerLabel(annotation: ReviewAnnotation, source: AgentSpecReview['source']) {
  return `Замечание ${numberAnnotation(annotation.number)} · ${formatEvidenceLocation(annotation.evidence, source)}`
}

export function DocumentMarker({ annotation, active, source, onSelect }: { annotation: ReviewAnnotation; active: boolean; source: AgentSpecReview['source']; onSelect: (annotation: ReviewAnnotation) => void }) {
  return <button type="button" className="sr-document-marker" data-active={active} data-severity={annotation.severity} onClick={() => onSelect(annotation)} aria-label={markerLabel(annotation, source)}><Typography as="span" variant="caption">{numberAnnotation(annotation.number)}</Typography></button>
}

export function DocumentZoom({ zoom, onZoom }: { zoom: number; onZoom: (value: number) => void }) {
  return <div className="sr-document-zoom" aria-label="Масштаб документа">
    <button type="button" aria-label="Уменьшить масштаб" disabled={zoom <= 60} onClick={() => onZoom(Math.max(60, zoom - 10))}><Typography as="span" variant="bodySmMedium">−</Typography></button>
    <button type="button" aria-label="Сбросить масштаб" onClick={() => onZoom(100)}><Typography as="span" variant="caption">{zoom}%</Typography></button>
    <button type="button" aria-label="Увеличить масштаб" disabled={zoom >= 160} onClick={() => onZoom(Math.min(160, zoom + 10))}><Typography as="span" variant="bodySmMedium">+</Typography></button>
  </div>
}

export function DocumentPageControls({ page, total, onPage, kind = 'page' }: { page: number; total: number; onPage: (page: number) => void; kind?: 'page' | 'part' }) {
  return <div className="sr-document-page-controls" aria-label={kind === 'page' ? 'Страницы документа' : 'Части документа'}>
    <button type="button" aria-label={kind === 'page' ? 'Предыдущая страница' : 'Предыдущая часть'} disabled={page <= 1} onClick={() => onPage(page - 1)}><HugeiconsIcon icon={ArrowLeft01Icon} size={17} aria-hidden /></button>
    <Typography as="span" variant="caption">{page} из {total}</Typography>
    <button type="button" aria-label={kind === 'page' ? 'Следующая страница' : 'Следующая часть'} disabled={page >= total} onClick={() => onPage(page + 1)}><HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden /></button>
  </div>
}

function TextDocument({ lines, source, unreadableUnits = [], evidence, jump, annotations, activeAnnotationId, onAnnotationSelect }: DocumentAnnotationProps & { lines: readonly string[]; unreadableUnits?: readonly number[]; source: AgentSpecReview['source'] }) {
  const pages = useMemo(() => source === 'pdf' ? lines.map((line, index) => ({ start: index + 1, end: index + 1, lines: [line] })) : paginateReviewText(lines), [lines, source])
  const citationKey = `${evidence?.start ?? 0}:${evidence?.end ?? 0}:${evidence?.quote ?? ''}:${jump}`
  const defaultPage = Math.max(1, pages.findIndex((part) => isEvidenceLine(evidence?.start ?? 0, { start: part.start, end: part.end })) + 1)
  const [manual, setManual] = useState<{ key: string; page: number }>()
  const page = manual?.key === citationKey ? manual.page : defaultPage
  const [zoom, setZoom] = useState(100)
  const current = pages[page - 1]
  const scrollRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLDivElement>(null)
  const thumbnailRef = useRef<HTMLButtonElement>(null)
  const highlights = useMemo(() => {
    if (!evidence || !current) return new Map<number, { start: number; end: number }>()
    const start = Math.max(evidence.start, current.start)
    const end = Math.min(evidence.end, current.end)
    if (end < start) return new Map()
    return new Map(quoteFragments(lines.slice(start - 1, end), evidence.quote).map((fragment) => [start + fragment.index, fragment]))
  }, [evidence, current, lines])

  useEffect(() => {
    if (!scrollRef.current) return
    if (activeRef.current && evidence && page === defaultPage && manual?.key !== citationKey) {
      const root = scrollRef.current
      const target = activeRef.current
      const top = target.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
      // Keep surrounding context when the citation is already visible.
      if (top < root.scrollTop + 20 || top > root.scrollTop + root.clientHeight - 100) root.scrollTop = Math.max(0, top - root.clientHeight * .28)
    } else scrollRef.current.scrollTop = 0
  }, [citationKey, page, defaultPage, manual?.key, evidence])

  useEffect(() => {
    if (thumbnailRef.current) {
      const node = thumbnailRef.current
      const rail = node.parentElement
      if (rail && (node.offsetTop < rail.scrollTop || node.offsetTop + node.offsetHeight > rail.scrollTop + rail.clientHeight)) rail.scrollTop = Math.max(0, node.offsetTop - 8)
    }
  }, [page])

  if (!current) return <div className="sr-reader-unavailable"><Typography as="p" variant="bodySm">В документе нет текста.</Typography></div>
  const selectPage = (next: number) => setManual({ key: citationKey, page: next })
  const firstTextLine = lines.findIndex((line) => line.trim()) + 1
  return <div className="sr-document-viewer" data-single-page={pages.length === 1}>
    <nav className="sr-document-rail" aria-label="Навигация по тексту">
      {pages.map((part, index) => <button ref={page === index + 1 ? thumbnailRef : undefined} type="button" key={part.start} className="sr-document-thumbnail" data-selected={page === index + 1} aria-pressed={page === index + 1} aria-label={source === 'pdf' ? `Страница ${index + 1}` : `Часть ${index + 1}, строки ${part.start}–${part.end}`} onClick={() => selectPage(index + 1)}>
        <span className="sr-text-thumbnail-paper" aria-hidden>{part.lines.slice(0, 14).map((line, i) => <Typography as="span" variant="bodyXs" key={i}>{line || '\u00a0'}</Typography>)}{annotations.some(({ evidence }) => evidence.start <= part.end && evidence.end >= part.start) && <span className="sr-thumbnail-dot" />}</span>
        <Typography as="span" variant="caption">{index + 1}</Typography>
      </button>)}
    </nav>
    <div className="sr-document-canvas">
      <div className="sr-document-canvas-toolbar"><DocumentPageControls page={page} total={pages.length} onPage={selectPage} kind={source === 'pdf' ? 'page' : 'part'} /><Typography as="span" variant="caption" className="sr-document-loc">{formatEvidenceLocation(evidence ?? { start: current.start, end: current.end }, source)}</Typography></div>
      <div ref={scrollRef} className="sr-document-scroll" tabIndex={0} aria-label="Текст исходного документа">
        <article className="sr-document-sheet" style={{ '--sr-doc-scale': zoom / 100 } as CSSProperties} data-testid="review-document-sheet">
          {current.lines.map((line, index) => {
            const number = current.start + index
            const active = isEvidenceLine(number, evidence)
            const match = highlights.get(number)
            const markers = annotations.filter((annotation) => annotation.evidence.start === number)
            const related = annotations.some((annotation) => isEvidenceLine(number, annotation.evidence))
            const unreadable = unreadableUnits.includes(number)
            const heading = !unreadable && source !== 'pdf' && (number === firstTextLine || /^\s*(?:#{1,6}\s|\d+\.\s+[^\d])/u.test(line))
            return <div key={number} ref={number === evidence?.start ? activeRef : undefined} className="sr-document-text-line" data-source-line={number} data-highlighted={active} data-related={!active && related} data-heading={heading} data-empty={!line.trim() && !unreadable}>
              <Typography as="span" variant="caption" className="sr-document-line-number" aria-hidden>{number}</Typography>
              <Typography as="span" variant="bodySm" className="sr-document-line-content">{unreadable ? <Typography as="span" variant="caption" tone="destructive">Изображение не распознано</Typography> : match ? <>{line.slice(0, match.start)}<Typography as="mark" variant="bodySm" className="sr-document-highlight">{line.slice(match.start, match.end)}</Typography>{line.slice(match.end)}</> : line || '\u00a0'}</Typography>
              <span className="sr-document-line-markers">{markers.map((annotation, markerIndex) => <DocumentMarker key={`${annotation.id}:${markerIndex}`} annotation={annotation} active={annotation.id === activeAnnotationId && sameEvidence(annotation.evidence, evidence)} source={source} onSelect={onAnnotationSelect} />)}</span>
            </div>
          })}
          <div className="sr-document-page-number"><Typography as="span" variant="caption">{page}</Typography></div>
        </article>
      </div>
      <DocumentZoom zoom={zoom} onZoom={setZoom} />
    </div>
  </div>
}

export function ReviewSourceDocument({ file, sourceUnits, sourceLoading = false, ocrPages, unreadableUnits, filename, source, mobileVisible, focusOnJump = false, focusMode = false, onToggleFocus, unavailableMessage, renderFallback, ...annotationProps }: { file?: File; sourceUnits?: readonly string[]; sourceLoading?: boolean; ocrPages?: readonly number[]; unreadableUnits?: readonly number[]; filename: string; source: AgentSpecReview['source']; mobileVisible: boolean; focusOnJump?: boolean; focusMode?: boolean; onToggleFocus?: () => void; unavailableMessage?: string; renderFallback?: () => ReactNode } & DocumentAnnotationProps) {
  const { evidence, jump } = annotationProps
  const [preview, setPreview] = useState<Preview>()
  const [manualPresentation, setManualPresentation] = useState<ReviewSourcePresentation>()
  const panelRef = useRef<HTMLElement>(null)
  const current = preview?.file === file ? preview : undefined
  const serverText = needsExtractedReviewSource(source)
  const photo = isImageReviewSource(source)
  const showExtracted = resolveReviewSourcePresentation(source, file, ocrPages ?? [], evidence, jump, manualPresentation)
  const showOriginalPhoto = photo && Boolean(file && !showExtracted && current?.url)
  const useExtracted = Boolean(sourceUnits && !showOriginalPhoto && (!file || serverText || showExtracted))
  function choosePresentation(extracted: boolean) { if (file) setManualPresentation({ file, jump, extracted }) }

  useEffect(() => {
    if (!file) return
    let cancelled = false
    let url: string | undefined
    async function load() {
      if (!file) return
      try {
        const lines = source === 'pdf' || needsExtractedReviewSource(source) ? undefined : splitReviewLines(await file.text())
        await Promise.resolve()
        if (cancelled) return
        url = URL.createObjectURL(source === 'pdf' && file.type !== 'application/pdf' ? new Blob([file], { type: 'application/pdf' }) : isImageReviewSource(source) ? new Blob([file], { type: source === 'png' ? 'image/png' : 'image/jpeg' }) : file)
        setPreview({ file, url, lines })
      } catch {
        if (!cancelled) setPreview({ file, url: '', error: true })
      }
    }
    void load()
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url) }
  }, [file, source])

  useEffect(() => {
    if (!mobileVisible || !jump || !panelRef.current) return
    const layoutWidth = panelRef.current.parentElement?.getBoundingClientRect().width
    if (focusOnJump || (layoutWidth && layoutWidth <= 820)) panelRef.current.focus({ preventScroll: true })
  }, [jump, mobileVisible, focusOnJump])

  const pdfUrl = current?.url && source === 'pdf' ? pdfCitationUrl(current.url, evidence?.start ?? 1) : undefined
  return <section ref={panelRef} tabIndex={-1} className="sr-reader-document" aria-label="Исходный документ">
    <header className="sr-reader-pane-header">
      <div className="sr-reader-document-title"><HugeiconsIcon icon={File01Icon} size={19} aria-hidden /><div className="min-w-0"><Typography as="h3" variant="bodySmMedium" title={filename}>{filename}</Typography>{useExtracted && <Typography as="p" variant="caption" tone="muted">{source === 'pdf' || serverText ? `Извлечённый текст ${source.toUpperCase()}` : 'Исходный текст'}</Typography>}</div></div>
      <div className="sr-document-header-actions">
        {current?.url && <a className="sr-reader-original" href={pdfUrl ?? current.url} target="_blank" rel="noopener noreferrer" download={source === 'pdf' ? undefined : filename}><Typography as="span" variant="caption">{source === 'pdf' ? 'Открыть оригинал' : 'Скачать файл'}</Typography><HugeiconsIcon icon={ArrowUpRight01Icon} size={15} aria-hidden /></a>}
        {file && photo && <button className="sr-document-fullscreen sr-ocr-toggle" type="button" disabled={!current?.url || !sourceUnits} aria-label={showOriginalPhoto ? 'Показать распознанный текст' : 'Показать исходную фотографию'} aria-pressed={showOriginalPhoto} onClick={() => choosePresentation(showOriginalPhoto)}><Typography as="span" variant="caption">{showOriginalPhoto ? 'Текст OCR' : 'Фото'}</Typography></button>}
        {file && source === 'pdf' && Boolean(ocrPages?.length) && <button className="sr-document-fullscreen sr-ocr-toggle" type="button" disabled={!sourceUnits} aria-label={showExtracted ? 'Показать оригинал PDF' : 'Показать распознанный текст OCR'} aria-pressed={showExtracted} onClick={() => choosePresentation(!showExtracted)}><Typography as="span" variant="caption">{showExtracted ? 'PDF' : 'Текст OCR'}</Typography></button>}
        {onToggleFocus && <Button type="button" variant="outline" size="sm" className="sr-document-fullscreen" onClick={onToggleFocus} aria-label={focusMode ? 'Выйти из полноэкранного режима' : 'Открыть документ на весь экран'} title={focusMode ? 'Выйти из полноэкранного режима' : 'На весь экран'}><HugeiconsIcon icon={focusMode ? Cancel01Icon : FullScreenIcon} size={17} aria-hidden /><Typography as="span" variant="caption" className="sr-fullscreen-label">{focusMode ? 'Выйти' : 'На весь экран'}</Typography></Button>}
      </div>
    </header>
    {showOriginalPhoto && current?.url ? <div className="sr-reader-photo"><img src={current.url} alt={`Оригинал: ${filename}`} /></div> : useExtracted && sourceUnits ? <TextDocument lines={sourceUnits} source={source} unreadableUnits={unreadableUnits} {...annotationProps} /> : serverText ? <div className="sr-reader-unavailable" role={sourceLoading ? 'status' : undefined}><HugeiconsIcon icon={File01Icon} size={34} aria-hidden /><Typography as="p" variant="bodySm">{sourceLoading ? 'Загружаем извлечённый текст…' : (unavailableMessage ?? 'Не удалось открыть извлечённый текст документа.')}</Typography>{!sourceLoading && evidence && <Typography as="blockquote" variant="bodySm">{evidence.quote}</Typography>}{!sourceLoading && renderFallback?.()}</div> : !file || current?.error ? <div className="sr-reader-unavailable" role={sourceLoading ? 'status' : undefined}><HugeiconsIcon icon={File01Icon} size={34} aria-hidden /><Typography as="p" variant="bodySm">{sourceLoading ? 'Загружаем извлечённый текст…' : current?.error ? 'Не удалось открыть исходный файл.' : (unavailableMessage ?? 'Исходный файл недоступен для просмотра.')}</Typography>{!sourceLoading && evidence && <Typography as="blockquote" variant="bodySm">{evidence.quote}</Typography>}{!sourceLoading && renderFallback?.()}</div> : !current ? <div className="sr-reader-unavailable" role="status"><Typography as="p" variant="bodySm">Открываем документ…</Typography></div> : pdfUrl ? <Suspense fallback={<div className="sr-reader-unavailable" role="status"><Typography as="p" variant="bodySm">Открываем PDF…</Typography></div>}><PdfReviewDocument key={current.url} file={file} originalUrl={pdfUrl} {...annotationProps} /></Suspense> : source !== 'pdf' && <TextDocument key={current.url} lines={current.lines ?? []} source={source} {...annotationProps} />}
  </section>
}
