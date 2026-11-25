import { AGENT_SPEC_REVIEW_MAX_CHARACTERS, type AgentReviewEdit, type AgentSpecReview } from '@web-app-demo/contracts'
import { ApiRequestError } from '@/platform/api'

export function canFixReview(report: Pick<AgentSpecReview, 'passport' | 'findings' | 'unreadable_pages' | 'unreadable_units'>, archivedId?: string) {
  return Boolean(archivedId ?? report.passport.run_id) && report.findings.length > 0 && report.unreadable_pages.length === 0 && !report.unreadable_units?.length
}

export function reviewFixErrorMessage(error: unknown, action: 'proposal' | 'verify'): string {
  const retained = action === 'verify' ? ' Принятые правки сохранены в текущем сеансе.' : ' Введённое уточнение сохранено.'
  if (error instanceof TypeError) return 'Не удалось соединиться с сервером. Проверьте подключение и повторите запрос.' + retained
  if (error instanceof ApiRequestError) {
    switch (error.code) {
      case 'RATE_LIMITED': return 'AI-сервис временно достиг лимита запросов. Повторите чуть позже.' + retained
      case 'AGENT_UNAVAILABLE': return 'AI-сервис временно недоступен. Попробуйте ещё раз.' + retained
      case 'LLM_NOT_CONFIGURED': return 'Для работы с исправлениями администратору нужно подключить AI-модель.'
      case 'UNAUTHORIZED': return 'Сессия завершена. Войдите в аккаунт повторно.'
      case 'NOT_FOUND': return 'Не удалось найти исходную проверку. Вернитесь к отчётам.'
    }
  }
  return (action === 'proposal'
    ? 'Агент не смог подготовить корректную правку. Уточните требование или повторите запрос.'
    : 'Не удалось повторно проверить документ. Попробуйте ещё раз.') + retained
}

/** Every edit is anchored to the same immutable source, not to prior replacements. */
export function applyReviewEdits(units: readonly string[], edits: readonly AgentReviewEdit[]): string {
  const ranges = new Map<number, { start: number; end: number; after: string }[]>()
  for (const { unit, before, after } of edits) {
    const text = units[unit - 1]
    if (text === undefined || !before.trim() || before === after) throw new Error('Некорректная правка.')
    const start = text.indexOf(before)
    if (start < 0 || text.indexOf(before, start + 1) >= 0) throw new Error('Не удалось однозначно найти исходный фрагмент.')
    const end = start + before.length
    const existing = ranges.get(unit) ?? []
    if (existing.some(range => start < range.end && end > range.start)) throw new Error('Правки пересекаются. Отмените одну из них.')
    ranges.set(unit, [...existing, { start, end, after }])
  }
  const result = units.map((unit, index) => {
    for (const { start, end, after } of (ranges.get(index + 1) ?? []).sort((a, b) => b.start - a.start)) {
      unit = unit.slice(0, start) + after + unit.slice(end)
    }
    return unit
  }).join('\n')
  if (!result.trim()) throw new Error('Доработанный документ не может быть пустым.')
  if (result.includes('\0')) throw new Error('Доработанный документ содержит недопустимые символы.')
  if (Array.from(result).length > AGENT_SPEC_REVIEW_MAX_CHARACTERS) throw new Error('Доработанный текст превышает лимит 100 000 знаков.')
  return result
}

export function revisedSpecificationFilename(filename: string) {
  const basename = filename.replace(/\\/g, '/').split('/').pop() ?? 'specification'
  const stem = basename.replace(/\.(pdf|md|txt|docx|xlsx|csv)$/i, '') || 'specification'
  return `${stem}-revised.${/\.md$/i.test(basename) ? 'md' : 'txt'}`
}
