import { getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type PDFPageProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/web/pdf_viewer.css'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'

import { Typography } from '@/components/typography'
import { DocumentMarker, DocumentPageControls, DocumentZoom, type DocumentAnnotationProps } from './ReviewSourceDocument'
import { quoteFragments, sameEvidence, type ReviewAnnotation, type ReviewEvidence } from './review-source'
import { formatEvidenceLocation } from './review-report'

GlobalWorkerOptions.workerSrc = workerUrl

type Rect = { left: number; top: number; width: number; height: number }
type Mark = { annotation: ReviewAnnotation; rectangles: Rect[]; active: boolean }
type Geometry = { selected: Rect[]; marks: Mark[] }

function textPosition(element: HTMLElement, offset: number): { node: Text; offset: number } | undefined {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  let remaining = offset
  let last: Text | undefined
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text
    last = text
    if (remaining <= text.length) return { node: text, offset: remaining }
    remaining -= text.length
  }
  return last ? { node: last, offset: last.length } : undefined
}

function quoteRectangles(layer: TextLayer, quote: string, root: HTMLElement): Rect[] {
  const rootRect = root.getBoundingClientRect()
  return quoteFragments(layer.textContentItemsStr, quote).flatMap(({ index, start, end }) => {
    const element = layer.textDivs[index]
    if (!element) return []
    const from = textPosition(element, start)
    const to = textPosition(element, end)
    if (!from || !to) return []
    const range = document.createRange()
    range.setStart(from.node, from.offset)
    range.setEnd(to.node, to.offset)
    return Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0).map((rect) => ({
      left: rect.left - rootRect.left, top: rect.top - rootRect.top, width: rect.width, height: rect.height,
    }))
  })
}

function PdfThumbnail({ doc, pageNumber, selected, hasIssues, onSelect }: { doc: PDFDocumentProxy; pageNumber: number; selected: boolean; hasIssues: boolean; onSelect: () => void }) {
  const button = useRef<HTMLButtonElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(selected)
  useEffect(() => {
    if (!button.current || visible) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '120px' })
    observer.observe(button.current)
    return () => observer.disconnect()
  }, [visible])

  useEffect(() => {
    if (!visible && !selected) return
    let cancelled = false
    let render: RenderTask | undefined
    void doc.getPage(pageNumber).then(async (page) => {
      if (cancelled || !canvas.current) return
      const initial = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: 72 / initial.width })
      const target = canvas.current
      target.width = Math.ceil(viewport.width)
      target.height = Math.ceil(viewport.height)
      render = page.render({ canvas: target, viewport })
      await render.promise
    }).catch(() => { /* a thumbnail failure does not block document rendering */ })
    return () => { cancelled = true; render?.cancel() }
  }, [doc, pageNumber, selected, visible])
  return <button ref={button} className="sr-document-thumbnail" type="button" data-selected={selected} aria-pressed={selected} aria-label={`Страница ${pageNumber}`} onClick={onSelect}>
    <span className="sr-pdf-thumbnail-paper" aria-hidden><canvas ref={canvas} />{hasIssues && <span className="sr-thumbnail-dot" />}</span>
    <Typography as="span" variant="caption">{pageNumber}</Typography>
  </button>
}

function PdfPageCanvas({ page, scale, annotations, activeAnnotationId, evidence, jump, onAnnotationSelect, onMatched, scrollRef }: DocumentAnnotationProps & { page: PDFPageProxy; scale: number; onMatched: (matched: boolean) => void; scrollRef: React.RefObject<HTMLDivElement | null> }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const textContainer = useRef<HTMLDivElement>(null)
  const paper = useRef<HTMLDivElement>(null)
  const [layer, setLayer] = useState<TextLayer>()
  const [geometry, setGeometry] = useState<Geometry>({ selected: [], marks: [] })
  const [error, setError] = useState(false)
  const viewport = useMemo(() => page.getViewport({ scale }), [page, scale])

  useEffect(() => {
    if (!canvas.current || !textContainer.current) return
    const target = canvas.current
    const text = textContainer.current
    let cancelled = false
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    target.width = Math.ceil(viewport.width * ratio)
    target.height = Math.ceil(viewport.height * ratio)
    target.style.width = `${viewport.width}px`
    target.style.height = `${viewport.height}px`
    const render = page.render({ canvas: target, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] })
    const textLayer = new TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport })
    void Promise.all([render.promise, textLayer.render()]).then(() => {
      if (!cancelled) setLayer(textLayer)
    }).catch(() => {
      if (!cancelled) setError(true)
    })
    return () => { cancelled = true; render.cancel(); textLayer.cancel(); text.replaceChildren() }
  }, [page, viewport])

  useEffect(() => {
    if (!paper.current || !layer) return
    const root = paper.current
    const here = (item: ReviewEvidence) => item.start <= page.pageNumber && item.end >= page.pageNumber
    const selected = evidence && here(evidence) ? quoteRectangles(layer, evidence.quote, root) : []
    const marks = annotations.filter(({ evidence }) => here(evidence)).map((annotation) => ({
      annotation, rectangles: quoteRectangles(layer, annotation.evidence.quote, root),
      active: annotation.id === activeAnnotationId && sameEvidence(annotation.evidence, evidence),
    }))
    setGeometry({ selected, marks })
    onMatched(Boolean(selected.length))
  }, [layer, annotations, activeAnnotationId, evidence, page.pageNumber, onMatched])

  const selectedTop = geometry.selected[0]?.top
  useEffect(() => {
    if (selectedTop === undefined || !scrollRef.current || !paper.current) return
    const root = scrollRef.current
    const paperTop = paper.current.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    const top = paperTop + selectedTop
    if (top < root.scrollTop + 20 || top > root.scrollTop + root.clientHeight - 90) root.scrollTop = Math.max(0, top - root.clientHeight * .28)
  }, [selectedTop, jump, scrollRef])

  const selectedRects = geometry.selected
  return <div ref={paper} className="sr-pdf-page" style={{ width: viewport.width, height: viewport.height, '--total-scale-factor': viewport.scale * viewport.userUnit, '--scale-round-x': '1px', '--scale-round-y': '1px' } as CSSProperties} data-testid="pdf-review-page" data-page={page.pageNumber}>
    <canvas ref={canvas} className="sr-pdf-canvas" />
    <div ref={textContainer} className="textLayer sr-pdf-text-layer" />
    <div className="sr-pdf-annotations">
      {geometry.marks.filter((mark) => !mark.active).flatMap(({ annotation, rectangles }) => rectangles.map((rect, i) => <span key={`${annotation.id}:${annotation.evidence.quote}:${i}`} aria-hidden className="sr-pdf-related-mark" style={rect} />))}
      {selectedRects.map((rect, i) => <span key={i} aria-hidden className="sr-pdf-active-mark" style={rect} />)}
      {geometry.marks.map(({ annotation, rectangles, active }, i) => {
        const first = rectangles[0]
        if (!first) return null
        const earlier = geometry.marks.slice(0, i).filter((mark) => Math.abs((mark.rectangles[0]?.top ?? -100) - first.top) < 7).length
        return <div className="sr-pdf-marker-position" key={`${annotation.id}:${i}`} style={{ top: Math.max(0, first.top - 3), left: Math.max(4, first.left - 30 - earlier * 23) }}><DocumentMarker annotation={annotation} active={active} source="pdf" onSelect={onAnnotationSelect} /></div>
      })}
    </div>
    {error && <div className="sr-reader-unavailable sr-pdf-error"><Typography as="p" variant="bodySm">Не удалось отобразить страницу. Откройте оригинал PDF.</Typography></div>}
  </div>
}

export function PdfReviewDocument({ file, originalUrl, ...annotationProps }: DocumentAnnotationProps & { file: File; originalUrl: string }) {
  const { evidence, annotations, jump } = annotationProps
  const [loaded, setLoaded] = useState<{ file: File; doc?: PDFDocumentProxy; error?: boolean }>()
  const doc = loaded?.file === file ? loaded.doc : undefined
  const citationKey = `${evidence?.start ?? 1}:${evidence?.end ?? 1}:${evidence?.quote ?? ''}:${jump}`
  const [manual, setManual] = useState<{ key: string; page: number }>()
  const total = doc?.numPages ?? 0
  const defaultPage = Math.max(1, Math.min(total || 1, evidence?.start ?? 1))
  const pageNumber = manual?.key === citationKey ? Math.min(total || 1, manual.page) : defaultPage
  const [pageData, setPageData] = useState<{ doc: PDFDocumentProxy; page: PDFPageProxy }>()
  const currentPage = pageData?.doc === doc && pageData?.page.pageNumber === pageNumber ? pageData.page : undefined
  const [zoom, setZoom] = useState(100)
  const [canvasWidth, setCanvasWidth] = useState(640)
  const [matched, setMatched] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const railRef = useRef<HTMLElement>(null)

  useEffect(() => {
    let cancelled = false
    let task: ReturnType<typeof getDocument> | undefined
    void file.arrayBuffer().then(async (buffer) => {
      if (cancelled) return
      task = getDocument({ data: new Uint8Array(buffer), useSystemFonts: true })
      const result = await task.promise
      if (!cancelled) setLoaded({ file, doc: result })
    }).catch(() => { if (!cancelled) setLoaded({ file, error: true }) })
    return () => { cancelled = true; if (task) void task.destroy().catch(() => undefined) }
  }, [file])

  useEffect(() => {
    if (!doc) return
    let cancelled = false
    void doc.getPage(pageNumber).then((page) => {
      if (!cancelled) setPageData({ doc, page })
    }).catch(() => { /* original PDF remains downloadable */ })
    return () => { cancelled = true }
  }, [doc, pageNumber])

  useEffect(() => {
    if (!pageRef.current) return
    const el = pageRef.current
    const observer = new ResizeObserver(([entry]) => {
      if (entry && entry.contentRect.width > 0) setCanvasWidth(entry.contentRect.width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [doc])

  useEffect(() => {
    if (!railRef.current) return
    const selected = railRef.current.querySelector<HTMLElement>('[data-selected=true]')
    if (selected && (selected.offsetTop < railRef.current.scrollTop || selected.offsetTop + selected.offsetHeight > railRef.current.scrollTop + railRef.current.clientHeight)) railRef.current.scrollTop = Math.max(0, selected.offsetTop - 8)
  }, [pageNumber, doc])

  if (loaded?.file === file && loaded.error) return <div className="sr-reader-unavailable"><Typography as="p" variant="bodySm">Не удалось отобразить PDF. Откройте оригинал по ссылке ниже.</Typography><a className="sr-citation-link" href={originalUrl} target="_blank" rel="noopener noreferrer"><Typography as="span" variant="caption">Открыть оригинал</Typography></a></div>
  if (!doc) return <div className="sr-reader-unavailable" role="status"><Typography as="p" variant="bodySm">Открываем PDF…</Typography></div>
  const originalWidth = currentPage?.getViewport({ scale: 1 }).width ?? 595
  const scale = Math.max(.2, Math.min(1.5, (canvasWidth - 36) / originalWidth)) * zoom / 100
  const onPage = (page: number) => { setManual({ key: citationKey, page }); setMatched(false); if (scrollRef.current) scrollRef.current.scrollTop = 0 }
  const hasCitation = evidence && evidence.start <= pageNumber && evidence.end >= pageNumber
  return <div className="sr-document-viewer" data-single-page={total === 1}>
    <nav className="sr-document-rail" ref={railRef} aria-label="Миниатюры страниц PDF">
      {Array.from({ length: total }, (_, i) => i + 1).map((page) => <PdfThumbnail key={page} doc={doc} pageNumber={page} selected={page === pageNumber} hasIssues={annotations.some(({ evidence }) => evidence.start <= page && evidence.end >= page)} onSelect={() => onPage(page)} />)}
    </nav>
    <div className="sr-document-canvas" ref={pageRef}>
      <div className="sr-document-canvas-toolbar"><DocumentPageControls page={pageNumber} total={total} onPage={onPage} /><Typography as="span" variant="caption" className="sr-document-loc">{evidence ? formatEvidenceLocation(evidence, 'pdf') : `Страница ${pageNumber}`}</Typography></div>
      <div ref={scrollRef} className="sr-document-scroll sr-pdf-scroll" aria-label="Страница PDF" tabIndex={0}>
        {currentPage ? <PdfPageCanvas key={`${pageNumber}:${scale}`} page={currentPage} scale={scale} scrollRef={scrollRef} {...annotationProps} onMatched={setMatched} /> : <div className="sr-reader-unavailable" role="status"><Typography as="p" variant="bodySm">Открываем страницу…</Typography></div>}
      </div>
      {hasCitation && !matched && <div className="sr-pdf-citation"><Typography as="strong" variant="caption">Цитата · {formatEvidenceLocation(evidence, 'pdf')}</Typography><Typography as="p" variant="bodySm">{evidence.quote}</Typography></div>}
      <DocumentZoom zoom={zoom} onZoom={setZoom} />
    </div>
  </div>
}
