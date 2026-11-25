import type { Page, Route } from '@playwright/test'
import type { AgentChatRequest, AgentCompletedReview, AgentMemory } from '@web-app-demo/contracts'
import { readFileSync } from 'node:fs'
import { e2ePassword, expect, test, uniqueEmail } from '../helpers/test'

function response(text = 'The guide says 42.') {
  const events = [
    ['tool_status', { id: 'search-1', tool: 'search_user_review', status: 'running' }],
    ['token', { content: text }],
    ['source', { filename: 'guide.pdf', page: 2, snippet: 'The measured value is 42.' }],
    ['tool_status', { id: 'search-1', tool: 'search_user_review', status: 'ok' }],
    ['end', {}],
  ]
  return events.map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join('')
}

const completedReviews: AgentCompletedReview[] = [
  { id: '0196f6f8-6600-7000-8000-000000000001', filename: 'guide.pdf', created_at: '2026-11-25T12:00:00+00:00', verdict: 'not_ready', requirements: 3, findings: 2 },
  { id: '0196f6f8-6600-7000-8000-000000000002', filename: 'other.md', created_at: '2026-11-24T12:00:00+00:00', verdict: 'ready', requirements: 1, findings: 0 },
]

function submittedReviewJobId(route: Route): string {
  const id = /name="job_id"\r\n\r\n([a-f0-9-]{36})\r\n/i.exec(route.request().postDataBuffer()?.toString() ?? '')?.[1]
  if (!id) throw new Error('Expected an uploaded review job UUID')
  return id
}

async function openAgent(page: Page, chat: (route: Route) => Promise<void>, openChat = true, reviews = completedReviews) {
  let memories: AgentMemory[] = []
  await page.route('**/api/agent/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/chat/stream')) return chat(route)
    if (url.pathname.endsWith('/modules')) return route.fulfill({ json: { modules: [{ id: 'rag', name: 'RAG', description: 'Search private documents' }] } })
    if (url.pathname.endsWith('/spec-reviews') && route.request().method() === 'GET') {
      expect(route.request().headers()['authorization']?.startsWith('Bearer ')).toBe(true)
      return route.fulfill({ json: { reviews } })
    }
    if (url.pathname.endsWith('/documents') && route.request().method() === 'GET') return route.fulfill({ json: { documents: [] } })
    if (url.pathname.endsWith('/memories')) {
      expect(route.request().headers()['authorization']?.startsWith('Bearer ')).toBe(true)
      if (route.request().method() === 'POST') {
        const payload = route.request().postDataJSON()
        expect(Object.keys(payload)).toEqual(['content'])
        const note: AgentMemory = { id: crypto.randomUUID(), content: payload.content, created_at: new Date().toISOString() }
        memories = [...memories, note]
        return route.fulfill({ json: note })
      }
      return route.fulfill({ json: { memories } })
    }
    if (url.pathname.includes('/memories/') && route.request().method() === 'DELETE') {
      expect(route.request().headers()['authorization']?.startsWith('Bearer ')).toBe(true)
      const id = url.pathname.split('/').at(-1)
      expect(memories.some((note) => note.id === id)).toBe(true)
      memories = memories.filter((note) => note.id !== id)
      return route.fulfill({ json: { deleted: true } })
    }
    return route.abort()
  })
  await page.goto('/signup')
  await page.getByLabel('Email').fill(uniqueEmail('assistant-ui'))
  await page.getByLabel('Password', { exact: true }).fill(e2ePassword)
  await page.getByLabel('Confirm Password').fill(e2ePassword)
  await page.getByRole('button', { name: 'Create Account' }).click()
  await expect(page).toHaveURL(/\/app$/)
  const agentsLink = page.getByTestId(openChat ? 'workspace-nav-/app/chat' : 'workspace-nav-/app/assistant')
  if (!await agentsLink.isVisible()) await page.getByRole('button', { name: 'Toggle Sidebar', exact: true }).click()
  await agentsLink.click()
  await expect(page).toHaveURL(openChat ? /\/app\/chat$/ : /\/app\/assistant$/)
  if (await page.getByRole('dialog').isVisible()) await page.keyboard.press('Escape')
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`Review chat retains citations, switches completed reviews, and manages memory at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    if (viewport.width === 390) await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const sent: AgentChatRequest[] = []
    await openAgent(page, async (route) => {
      expect(route.request().headers()['authorization']?.startsWith('Bearer ')).toBe(true)
      sent.push(route.request().postDataJSON())
      await route.fulfill({ contentType: 'text/event-stream', body: response() })
    })
    const input = page.getByLabel('Вопрос', { exact: true })
    await page.getByTestId('agent-suggestion').first().click()
    await expect(input).not.toHaveValue('')
    await input.fill('What is the measured value?')
    await input.press('Control+Enter')
    await expect(page.getByTestId('agent-assistant-message')).toContainText('The guide says 42.')
    await expect(page.getByTestId('agent-sources')).toContainText('guide.pdf')
    await expect(page.getByTestId('agent-sources')).toContainText('Стр. 2')
    await expect(page.getByTestId('agent-response-identity')).toHaveAttribute('data-response-state', 'idle')
    await expect(input).toHaveValue('')
    expect(sent[0]?.module_id).toBe('review_chat')
    expect(sent[0]?.review_id).toBe(completedReviews[0]?.id)

    await input.fill('Explain the evidence.')
    await page.getByTestId('agent-send').click()
    await expect(page.getByTestId('agent-assistant-message')).toHaveCount(2)
    await expect(page.getByTestId('agent-stop')).toHaveCount(0)
    expect(sent[1]?.thread_id).toBe(sent[0]?.thread_id)
    expect(sent[1]?.module_id).toBe('review_chat')
    expect(sent[1]?.review_id).toBe(completedReviews[0]?.id)
    expect(sent[1]?.messages.at(-1)?.content).toBe('Explain the evidence.')

    await expect(page.getByTestId('agent-document-input')).toHaveCount(0)
    const historySearch = page.getByRole('searchbox', { name: 'Поиск отчёта' })
    await historySearch.fill('other')
    await expect(page.getByTestId('completed-review')).toHaveCount(1)
    await expect(page.getByTestId('agent-assistant-message')).toHaveCount(2)
    await page.getByRole('combobox', { name: 'Статус проверки' }).selectOption('not_ready')
    await expect(page.getByTestId('completed-review')).toHaveCount(0)
    await page.getByRole('combobox', { name: 'Статус проверки' }).selectOption('ready')
    await page.getByTestId('completed-review').click()
    await input.fill('Question about the other review')
    await page.getByTestId('agent-send').click()
    await expect(page.getByTestId('agent-assistant-message')).toHaveCount(1)
    await expect.poll(() => sent.length).toBe(3)
    expect(sent[2]?.thread_id).not.toBe(sent[0]?.thread_id)
    expect(sent[2]?.review_id).toBe(completedReviews[1]?.id)

    await page.getByText('Заметки для ассистента', { exact: true }).click()
    const draft = page.getByLabel('Новая заметка', { exact: true })
    await draft.fill('Synthetic preference: concise replies.')
    await page.getByTestId('agent-memory-save').click()
    await expect(page.getByTestId('agent-memory-note')).toContainText('Synthetic preference: concise replies.')
    await expect(draft).toHaveValue('')
    await page.reload()
    await page.getByText('Заметки для ассистента', { exact: true }).click()
    await expect(page.getByTestId('agent-memory-note')).toContainText('Synthetic preference: concise replies.')
    await page.getByTestId('agent-memory-delete').click()
    await expect(page.getByTestId('agent-memory-note')).toHaveCount(0)
    expect(errors).toEqual([])
    await page.evaluate(() => { window.scrollTo(0, 0); return new Promise<void>((resolve) => requestAnimationFrame(() => resolve())) })
    await page.screenshot({ path: testInfo.outputPath(`assistant-${viewport.width}.png`), fullPage: true })
  })
}

test('review chat does not send questions before the first completed review', async ({ page }) => {
  let calls = 0
  await openAgent(page, async (route) => { calls++; await route.fulfill({ contentType: 'text/event-stream', body: response() }) }, true, [])
  await expect(page.getByTestId('agent-send')).toBeDisabled()
  await expect(page.getByLabel('Вопрос', { exact: true })).toBeDisabled()
  await expect(page.getByTestId('agent-document-input')).toHaveCount(0)
  expect(calls).toBe(0)
})

test('Review chat displays provider errors, cancels a pending request, and accepts the next question', async ({ page }) => {
  let calls = 0
  let release: (() => void) | undefined
  await openAgent(page, async (route) => {
    calls++
    if (calls === 1) return route.fulfill({ contentType: 'text/event-stream', body: 'event: error\ndata: {"error":"LLM_NOT_CONFIGURED"}\n\nevent: end\ndata: {}\n\n' })
    if (calls === 2) {
      await new Promise<void>((resolve) => { release = resolve })
      await route.fulfill({ contentType: 'text/event-stream', body: response('Late response') }).catch(() => {})
      return
    }
    return route.fulfill({ contentType: 'text/event-stream', body: response('Recovered answer') })
  })
  const input = page.getByLabel('Вопрос', { exact: true })
  await input.fill('First question')
  await page.getByTestId('agent-send').click()
  await expect(page.getByTestId('agent-error')).toContainText('LLM_NOT_CONFIGURED')
  await input.fill('Pending question')
  await page.getByTestId('agent-send').click()
  await expect.poll(() => calls).toBe(2)
  await expect(page.getByTestId('agent-response-identity').last()).toHaveAttribute('data-response-state', 'waiting')
  await page.getByTestId('agent-stop').click()
  await expect(input).toBeEnabled()
  await expect(page.getByTestId('agent-response-identity').last()).toHaveAttribute('data-response-state', 'idle')
  release?.()
  await input.fill('Next question')
  await page.getByTestId('agent-send').click()
  await expect(page.getByTestId('agent-assistant-message').last()).toContainText('Recovered answer')
  await expect(page.getByTestId('agent-error')).toHaveCount(1)
})

test('Review chat preserves the actionable message from an HTTP 503 provider error', async ({ page }) => {
  await openAgent(page, async (route) => route.fulfill({ status: 503, json: { error: { code: 'LLM_NOT_CONFIGURED', message: 'Configure a chat model before sending messages.' } } }))
  await page.getByLabel('Вопрос', { exact: true }).fill('Question without a provider')
  await page.getByTestId('agent-send').click()
  await expect(page.getByTestId('agent-error')).toContainText('Configure a chat model before sending messages.')
  await expect(page.getByLabel('Вопрос', { exact: true })).toBeEnabled()
})

test('a pending specification review continues across navigation and reload and notifies on completion', async ({ page }) => {
  await openAgent(page, (route) => route.fulfill({ contentType: 'text/event-stream', body: response() }), false, [])
  const sample = JSON.parse(readFileSync(new URL('../../src/features/agent/examples/fintech-payment-review.json', import.meta.url), 'utf8'))
  const source = readFileSync(new URL('../../src/features/agent/examples/fintech-payment-spec.txt', import.meta.url))
  let attempts = 0
  let polls = 0
  let ready = false
  let jobId: string
  const job = () => ({ id: jobId, filename: sample.filename, started_at: new Date().toISOString() })
  await page.route('**/api/agent/spec-review-jobs', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    attempts++
    jobId = submittedReviewJobId(route)
    await route.fulfill({ status: 202, json: { ...job(), status: 'running' } })
  })
  await page.route('**/api/agent/spec-review-jobs/*', async route => {
    polls++
    return route.fulfill({ json: ready ? { ...job(), status: 'completed', review_id: jobId } : { ...job(), status: 'running' } })
  })
  await page.route('**/api/agent/spec-reviews/*', async route => route.fulfill({ json: { id: jobId, report: { ...sample, passport: { ...sample.passport, run_id: jobId } }, source_units: source.toString().split(/\r?\n/) } }))

  await page.getByLabel('Файл ТЗ').setInputFiles({ name: sample.filename, mimeType: 'text/plain', buffer: source })
  await page.getByRole('button', { name: 'Проверить ТЗ', exact: true }).click()
  await expect.poll(() => attempts).toBe(1)
  const indicator = page.getByTestId('review-run-indicator')
  await expect(indicator).toHaveAttribute('data-review-status', 'running')
  await page.getByTestId('workspace-nav-/app').click()
  await expect(page).toHaveURL(/\/app$/)
  await expect(indicator).toHaveAttribute('data-review-status', 'running')
  await indicator.click()
  await expect(page.getByRole('region', { name: 'Выполняется проверка' })).toBeVisible()
  await page.getByTestId('workspace-nav-/app').click()
  await expect(page).toHaveURL(/\/app$/)
  await expect.poll(() => polls).toBeGreaterThan(0)
  await page.reload()
  await expect(indicator).toHaveAttribute('data-review-status', 'running')
  ready = true
  await expect(indicator).toHaveAttribute('data-review-status', 'completed')
  await page.getByRole('button', { name: 'Открыть отчёт', exact: true }).click()
  await expect(page).toHaveURL(/\/app\/assistant$/)
  await expect(page.getByTestId('spec-review-report')).toBeVisible()
  await expect(page.getByTestId('review-document-sheet')).toBeVisible()
  expect(attempts).toBe(1)
})

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`FinTech review presents recoverable errors and cited findings at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await openAgent(page, (route) => route.fulfill({ contentType: 'text/event-stream', body: response() }), false)

    let attempts = 0
    let jobId: string
    const job = () => ({ id: jobId, filename: 'payment.txt', started_at: '2026-11-25T10:00:00Z' })
    await page.route('**/api/agent/spec-review-jobs', async (route) => {
      attempts += 1
      expect(route.request().headers()['authorization']).toMatch(/^Bearer /)
      expect(route.request().postDataBuffer()?.includes(Buffer.from('payment.txt'))).toBe(true)
      jobId = submittedReviewJobId(route)
      await route.fulfill({ status: 202, json: { ...job(), status: 'running' } })
    })
    await page.route('**/api/agent/spec-review-jobs/*', async route => {
      await route.fulfill({ json: attempts === 1 ? { ...job(), status: 'failed', error: { code: 'REVIEW_FAILED', message: 'Test provider rejected the request' } } : { ...job(), status: 'completed', review_id: jobId } })
    })
    await page.route('**/api/agent/spec-reviews/*', async (route) => {
      const report = {
        filename: 'payment.txt',
        source: 'txt',
        unreadable_pages: [],
        summary: 'Нужно уточнить повторы платежей.',
        passport: { document_title: 'Платежи', run_at: '2026-11-25T10:00:00Z', model: 'fixture', agent_version: 'test', run_id: jobId },
        statistics: { requirements: 1, violations: 1, critical: 1, passed: 0, needs_revision: 1 },
        verdict: { status: 'not_ready', reason: 'Нужно уточнить повторы платежей.', priorities: ['Уточнить повторы'] },
        requirements: [{ id: 'REQ-001', source_id: null, text: 'Повторить списание после тайм-аута.', evidence: { start: 2, end: 2, quote: 'Повторить списание после тайм-аута.' }, checks: [{ criterion: 'Полнота', status: 'fail', comment: 'Нет условия повтора.' }] }],
        set_checks: [{ criterion: 'Согласованность', status: 'pass', comment: 'Нет противоречий.', evidence: [] }],
        report_markdown: '# Отчёт по ТЗ',
        findings: [{
          kind: 'missing',
          severity: 'critical',
          requirement_ids: ['REQ-001'],
          issue: 'Возможно двойное списание.',
          recommendation: 'Повторить запрос с тем же ключом идемпотентности.',
          evidence: [{ start: 2, end: 2, quote: 'Повторить списание после тайм-аута.' }],
        }],
      }
      await route.fulfill({ json: { id: jobId, report, source_units: ['Платежи', 'Повторить списание после тайм-аута.'] } })
    })

    const input = page.getByLabel('Файл ТЗ')
    const submit = page.getByRole('button', { name: 'Проверить ТЗ', exact: true })
    await expect(submit).toBeDisabled()
    await input.setInputFiles({ name: 'payment.txt', mimeType: 'text/plain', buffer: Buffer.from('Платежи\nПовторить списание после тайм-аута.') })
    await submit.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('alert')).toContainText('Попробуйте ещё раз')
    await submit.click()
    const report = page.getByRole('region', { name: 'Отчёт по ТЗ' })
    await expect(report).toContainText('Возможно двойное списание.')
    await expect(report).toContainText('Строка 2')
    await expect(report).toContainText('Повторить списание после тайм-аута.')
    await expect(report).toContainText('Повторить запрос с тем же ключом идемпотентности.')
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Скачать отчёт' }).click()
    expect((await download).suggestedFilename()).toBe('payment-review.md')
    const newReview = page.getByTestId('workspace-new-review')
    if (!await newReview.isVisible()) await page.getByRole('button', { name: 'Toggle Sidebar', exact: true }).click()
    await newReview.click()
    await expect(submit).toBeDisabled()
    await expect(report).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width)
    expect(attempts).toBe(2)
    expect(errors).toEqual([])
  })
}
