import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import * as chat from '../src/features/agent/AgentThread'

test('source disclosure is omitted without citations and groups passages from the same file', () => {
  expect(renderToStaticMarkup(createElement(chat.AgentSources, { sources: [] }))).toBe('')
  const html = renderToStaticMarkup(createElement(chat.AgentSources, { sources: [
    { filename: 'spec.txt', snippet: 'строка 9: 100 000' },
    { filename: 'spec.txt', snippet: 'строка 12: 150 000' },
    { filename: 'Отчёт: spec.txt', snippet: 'Согласовать лимиты' },
  ] }))
  expect(html).toContain('<details')
  expect(html).not.toContain(' open=')
  expect(html.match(/>spec\.txt</g)).toHaveLength(1)
  expect(html).toContain('строка 12: 150 000')
})

function identity(running: boolean, hasText: boolean) {
  expect(typeof chat.AgentResponseIdentity).toBe('function')
  return renderToStaticMarkup(createElement(chat.AgentResponseIdentity, { running, hasText }))
}

test('assistant identity is animated before the first token and while streaming text', () => {
  const waiting = identity(true, false)
  expect(waiting).toContain('data-response-state="waiting"')
  expect(waiting).toContain('data-mascot-state="thinking"')
  const streaming = identity(true, true)
  expect(streaming).toContain('data-response-state="streaming"')
  expect(streaming).toContain('data-mascot-state="speaking"')
})

test('assistant identity remains visible after the run ends without continuous animation', () => {
  const settled = identity(false, true)
  expect(settled).toContain('data-response-state="idle"')
  expect(settled).toContain('data-mascot-state="success"')
  expect(settled).not.toContain('mo-always')
})


test('failed assistant responses use the error expression without fake active motion', () => {
  const html = renderToStaticMarkup(createElement(chat.AgentResponseIdentity, { running: false, hasText: false, failed: true }))
  expect(html).toContain('data-mascot-state="error"')
  expect(html).not.toContain('mo-always')
})
