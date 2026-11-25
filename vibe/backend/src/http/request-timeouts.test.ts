import { expect, test } from 'bun:test'
import { applyRequestTimeout } from './request-timeouts'

test('chat stream stays connected while the model is silent longer than Bun idle timeout', async () => {
  const encoder = new TextEncoder()
  const server = Bun.serve({
    hostname: '127.0.0.1', port: 0, idleTimeout: 1,
    fetch(request, server) {
      applyRequestTimeout(request, server)
      let cancelled = false
      const body = new ReadableStream<Uint8Array>({
        async start(controller) {
          controller.enqueue(encoder.encode('event: module\ndata: {}\n\n'))
          await Bun.sleep(6600)
          if (cancelled) return
          controller.enqueue(encoder.encode('event: token\ndata: {"content":"hello"}\n\nevent: end\ndata: {}\n\n'))
          controller.close()
        },
        cancel() { cancelled = true },
      })
      return new Response(body, { headers: { 'content-type': 'text/event-stream' } })
    },
  })
  try {
    const response = await fetch(new URL('/api/agent/chat/stream', server.url), { method: 'POST' })
    expect(response.status).toBe(200)
    const content = await response.text()
    expect(content).toContain('"content":"hello"')
    expect(content).toContain('event: end')
  } finally { server.stop(true) }
}, 12000)

test('only long-running POST routes override the default timeout', () => {
  const calls: number[] = []
  const server = { timeout(_request: Request, seconds: number) { calls.push(seconds) } }
  for (const [method, path] of [['GET', '/api/agent/chat/stream'], ['POST', '/api/auth/login'], ['GET', '/health']]) {
    applyRequestTimeout(new Request(`http://localhost${path}`, { method }), server)
  }
  expect(calls).toEqual([])
  applyRequestTimeout(new Request('http://localhost/api/agent/spec-reviews', { method: 'POST' }), server)
  expect(calls).toEqual([0])
})

test('fix proposals have a longer timeout without changing unrelated routes', () => {
  const calls: number[] = []
  const server = { timeout: (_request: Request, seconds: number) => { calls.push(seconds) } }
  applyRequestTimeout(new Request('http://localhost/api/agent/spec-reviews/0196f6f8-6600-7000-8000-000000000001/fix', { method: 'POST' }), server)
  expect(calls).toEqual([255])
})

test('review job upload has a bounded timeout while polling keeps the normal timeout', () => {
  const calls: number[] = []
  const server = { timeout: (_request: Request, seconds: number) => { calls.push(seconds) } }
  applyRequestTimeout(new Request('http://localhost/api/agent/spec-review-jobs/0196f6f8-6600-7000-8000-000000000001'), server)
  expect(calls).toEqual([])
  applyRequestTimeout(new Request('http://localhost/api/agent/spec-review-jobs', { method: 'POST' }), server)
  expect(calls).toEqual([120])
})
