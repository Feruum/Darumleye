/** Keep Bun's normal idle timeout for all routes except long-running AI requests. */
export function applyRequestTimeout(request: Request, server: { timeout(request: Request, seconds: number): void }) {
  if (request.method !== 'POST') return
  const path = new URL(request.url).pathname
  if (path === '/api/agent/spec-reviews') {
    // Large document review and OCR have a bounded upstream deadline, but can
    // be silent longer than Bun's maximum 255-second idle timeout.
    server.timeout(request, 0)
    return
  }
  if (path === '/api/agent/spec-review-jobs') {
    // A job response acknowledges the bounded multipart upload, not the AI run.
    server.timeout(request, 120)
    return
  }
  if (path === '/api/agent/chat/stream' ||
      /^\/api\/agent\/spec-reviews\/[a-f0-9-]{36}\/fix$/i.test(path)) {
    // The model can be silent for longer than Bun's default 10s, including before its first token.
    // The review has a bounded deadline in the agent; chat has per-model-call timeouts.
    server.timeout(request, 255)
  }
}
