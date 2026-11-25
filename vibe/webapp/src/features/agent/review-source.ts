import { agentSpecFindingId, type AgentSpecReview, type AgentReferenceSourceSnapshot } from '@web-app-demo/contracts'

export type ReviewEvidence = AgentSpecReview['requirements'][number]['evidence']
export type ReviewAnnotation = { id: string; number: number; evidence: ReviewEvidence; severity: AgentSpecReview['findings'][number]['severity'] }

export function needsExtractedReviewSource(source: AgentSpecReview['source']) {
  return source === 'docx' || source === 'xlsx' || source === 'csv' || isImageReviewSource(source)
}

export function isImageReviewSource(source: AgentSpecReview['source']) {
  return source === 'jpg' || source === 'jpeg' || source === 'png'
}

export type ReviewSourcePresentation = { file: File; jump: number; extracted: boolean }

export function resolveReviewSourcePresentation(source: AgentSpecReview['source'], file: File | undefined, ocrPages: readonly number[], evidence: ReviewEvidence | undefined, jump: number, manual?: ReviewSourcePresentation) {
  // A manual original/derived choice applies to this document and this
  // citation. A new jump must show OCR text rather than a blank image layer.
  if (manual && manual.file === file && manual.jump === jump) return manual.extracted
  return needsExtractedReviewSource(source) || (source === 'pdf' && ocrPages.some(page => isEvidenceLine(page, evidence)))
}

export function findingHeading(issue: string) {
  // A display heading is a shortened verbatim clause, never a generated claim.
  const first = issue.trim().split(/(?<=[.!?])\s+|[,;]\s+(?=однако|но|при этом|также)/iu, 1)[0] ?? issue
  const clean = first.replace(/[.!?;:]+$/, '')
  if (clean.length <= 66) return clean
  const boundary = clean.lastIndexOf(' ', 66)
  return `${clean.slice(0, boundary > 30 ? boundary : 66).trimEnd()}…`
}

export function numberAnnotation(number: number) {
  return String(number).padStart(2, '0')
}

export function buildAnnotations(findings: AgentSpecReview['findings']): ReviewAnnotation[] {
  return findings.flatMap((finding, index) => finding.evidence.map((evidence) => ({ id: agentSpecFindingId(index), number: index + 1, evidence, severity: finding.severity })))
}

export function sameEvidence(first: ReviewEvidence, second?: ReviewEvidence) {
  return Boolean(second && first.start === second.start && first.end === second.end && first.quote === second.quote)
}

export type QuoteFragment = { index: number; start: number; end: number }

// A PDF text layer may split a word into consecutive spans. Match the quoted
// characters regardless of whitespace inserted by extraction, but never match
// a different letter or punctuation. Return original UTF-16 offsets for DOM Range.
export function quoteFragments(parts: readonly string[], quote: string): QuoteFragment[] {
  const needle = quote.replace(/\s/gu, '')
  if (!needle) return []
  const chars: string[] = []
  const locations: { index: number; offset: number }[] = []
  parts.forEach((part, index) => {
    for (let offset = 0; offset < part.length; offset++) {
      const char = part[offset]!
      if (!/\s/u.test(char)) { chars.push(char); locations.push({ index, offset }) }
    }
  })
  const start = chars.join('').indexOf(needle)
  if (start < 0) return []
  const result: QuoteFragment[] = []
  for (const location of locations.slice(start, start + needle.length)) {
    const previous = result.at(-1)
    if (previous?.index === location.index) previous.end = location.offset + 1
    else result.push({ index: location.index, start: location.offset, end: location.offset + 1 })
  }
  return result
}

export type TextReviewPage = { start: number; end: number; lines: readonly string[] }

export function paginateReviewText(lines: readonly string[]): TextReviewPage[] {
  const pages: TextReviewPage[] = []
  let start = 0
  let size = 0
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    if (i > start && (i - start >= 32 || size + line.length > 2400)) {
      pages.push({ start: start + 1, end: i, lines: lines.slice(start, i) })
      start = i
      size = 0
    }
    size += line.length + 1
  }
  if (start < lines.length) pages.push({ start: start + 1, end: lines.length, lines: lines.slice(start) })
  return pages
}

// Match Python str.splitlines(), including empty intervening lines and no phantom
// final line after a trailing separator. The server removes the UTF-8 BOM.
export function splitReviewLines(text: string): string[] {
  const source = text.replace(/^\uFEFF/, '')
  if (!source) return []
  // eslint-disable-next-line no-control-regex -- Python splitlines recognizes these Unicode control separators.
  const lines = source.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
  if (lines.at(-1) === '') lines.pop()
  return lines
}

export function isEvidenceLine(line: number, evidence?: Pick<ReviewEvidence, 'start' | 'end'>) {
  return Boolean(evidence && line >= evidence.start && line <= evidence.end)
}

export function pdfCitationUrl(url: string, page: number) {
  return `${url}#page=${Math.max(1, Math.floor(page))}&view=FitH`
}


/** Don't point a quote at a different file, a stale extraction, or unreadable units. */
export function verifiedReferenceUnits(
  snapshot: AgentReferenceSourceSnapshot,
  source: NonNullable<AgentSpecReview['reference_review']>['sources'][number],
  evidence: ReviewEvidence,
): readonly string[] | undefined {
  if (snapshot.source_id !== source.id || snapshot.sha256 !== source.sha256) return undefined
  const { start, end, quote } = evidence
  if (start < 1 || end < start || end > snapshot.source_units.length ||
      [...source.unreadable_pages, ...(source.unreadable_units ?? [])].some(unit => unit >= start && unit <= end)) return undefined
  // Python str.split() treats these Unicode whitespace separators alike.
  // eslint-disable-next-line no-control-regex -- match the archive validator.
  const whitespace = /[ \t\n\r\f\v\x1c-\x1f\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu
  const normalize = (value: string) => value.replace(whitespace, ' ').replace(/^ +| +$/g, '')
  const normalized = normalize(quote)
  return normalized && normalize(snapshot.source_units.slice(start - 1, end).join(' ')).includes(normalized) ? snapshot.source_units : undefined
}
