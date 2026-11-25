import { expect, test } from 'bun:test'
import type { AgentReferenceReview } from '@web-app-demo/contracts'
import { buildSpecificationReviewFormData } from '../src/features/agent/api'
import { matchOriginalReferenceFiles, referenceRecheckSelection } from '../src/features/agent/reference-sources'

type Source = AgentReferenceReview['sources'][number]

async function source(file: File, id: string, title: string): Promise<Source> {
  const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  const sha256 = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
  return {
    id, filename: file.name, source: 'txt', sha256, title, category: 'bank_policy',
    issuer: 'Владелец', version: '2.0', scope: 'Операции', source_url: 'https://example.com/policy',
    unreadable_pages: [], applicability: 'relevant', summary: 'Проверен.',
  }
}

test('matches raw byte SHA-256 rather than name, then restores original order and metadata', async () => {
  const first = new File(['Политика\n'], 'first.txt')
  const second = new File(['Политика\r\n'], 'second.txt')
  const originals = [await source(first, 'S-001', 'Первая'), await source(second, 'S-002', 'Вторая')]
  const restored = await matchOriginalReferenceFiles(originals, [
    new File([second], 'renamed.txt'), new File([first], 'another.txt'),
  ])
  expect(restored.rejected).toEqual([])
  const selection = referenceRecheckSelection(originals, restored.matches, false)
  expect(selection.mode).toBe('with-references')
  if (selection.mode !== 'with-references') throw new Error('Expected both original files')
  expect(selection.references.map(r => r.file.name)).toEqual(['first.txt', 'second.txt'])
  expect(selection.references.map(r => r.metadata)).toEqual(originals.map(({ category, title, issuer, version, scope, source_url }) => ({ category, title, issuer, version, scope, source_url })))
  expect(await Promise.all(selection.references.map(r => r.file.text()))).toEqual(['Политика\n', 'Политика\r\n'])
  const form = buildSpecificationReviewFormData(new File(['ТЗ'], 'spec.md'), selection.references)
  expect(form.getAll('reference_files').map(file => (file as File).name)).toEqual(['first.txt', 'second.txt'])
})

test('blocks an incomplete match and rejects a same-name modified file without retaining its bytes', async () => {
  const first = new File(['Original'], 'policy.txt')
  const second = new File(['Second original'], 'second.txt')
  const originals = [await source(first, 'S-001', 'First'), await source(second, 'S-002', 'Second')]
  const restored = await matchOriginalReferenceFiles(originals, [first, new File(['Modified'], 'second.txt')])
  expect(restored.matches.map(m => m.sourceId)).toEqual(['S-001'])
  expect(restored.rejected).toEqual(['second.txt'])
  const incomplete = referenceRecheckSelection(originals, restored.matches, false)
  expect(incomplete.mode).toBe('blocked')
  expect(incomplete.missing.map(s => s.filename)).toEqual(['second.txt'])
  expect('references' in incomplete).toBe(false)
  const complete = await matchOriginalReferenceFiles(originals, [second], restored.matches)
  expect(referenceRecheckSelection(originals, complete.matches, false).mode).toBe('with-references')
})

test('document-only recheck is opt-in for reports with archived sources', async () => {
  const original = await source(new File(['Original'], 'policy.txt'), 'S-001', 'First')
  expect(referenceRecheckSelection([original], [], false).mode).toBe('blocked')
  expect(referenceRecheckSelection([original], [], true)).toEqual({ mode: 'document-only', references: [], missing: [original] })
  expect(referenceRecheckSelection([], [], false)).toEqual({ mode: 'document-only', references: [], missing: [] })
})

test('restores all separately annotated original sources even when they share exact bytes', async () => {
  const original = new File(['Rule'], 'one.txt')
  const duplicate = new File([original], 'two.txt')
  const originals = [await source(original, 'S-001', 'First'), await source(duplicate, 'S-002', 'Second')]
  const restored = await matchOriginalReferenceFiles(originals, [original])
  const selection = referenceRecheckSelection(originals, restored.matches, false)
  expect(selection.mode).toBe('with-references')
  if (selection.mode !== 'with-references') throw new Error('Expected both source records')
  expect(selection.references.map(r => [r.file.name, r.metadata.title])).toEqual([['one.txt', 'First'], ['two.txt', 'Second']])
})

test('rejects oversized candidate batches before file reads', async () => {
  const original = new File(['Rule'], 'one.txt')
  const originals = [await source(original, 'S-001', 'First')]
  const oversized = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'one.txt')
  const restored = await matchOriginalReferenceFiles(originals, [oversized])
  expect(restored.matches).toEqual([])
  expect(restored.error).toContain('5 МиБ')
  expect(referenceRecheckSelection(originals, restored.matches, false).mode).toBe('blocked')
})

test('archive restore panel identifies missing sources and the explicit document-only choice', async () => {
  const { createElement } = await import('react')
  const { renderToStaticMarkup } = await import('react-dom/server')
  const { ReviewReferenceRestorePanel } = await import('../src/features/agent/ReviewFixWorkspace')
  const original = await source(new File(['Rule'], 'original.txt'), 'S-001', 'Original policy')
  const markup = renderToStaticMarkup(createElement(ReviewReferenceRestorePanel, {
    sources: [original], restore: { matches: [], rejected: ['wrong.txt'] },
    restoring: false, documentOnly: false, disabled: false,
    onSelectFiles: () => {}, onDocumentOnly: () => {},
  }))
  expect(markup).toContain('original.txt')
  expect(markup).toContain('Требуется оригинал')
  expect(markup).toContain('wrong.txt')
  expect(markup).toContain('не будут отправлены')
  expect(markup).toContain('type="file"')
  expect(markup).toContain('type="checkbox"')
  expect(markup).not.toContain('checked=""')
})
