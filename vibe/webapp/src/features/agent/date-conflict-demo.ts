import type { AgentReviewEdit } from '@web-app-demo/contracts'

import { applyReviewEdits } from './review-fix'

export type DateRule = 'purchase' | 'posting'

export const originalRule = 'Месяц определяется по дате совершения покупки в Алматы.'
export const originalScenario = '| Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы | Февральский счётчик вырос на 4 000 ₸ |'
const postingRule = 'Месяц определяется по дате проведения покупки в Алматы.'
const demoSource = [originalRule, originalScenario]

const edits: Record<DateRule, AgentReviewEdit> = {
  purchase: {
    unit: 2,
    before: 'Февральский счётчик вырос на 4 000 ₸',
    after: 'Январский счётчик вырос на 4 000 ₸. Февральский счётчик не изменился.',
  },
  posting: { unit: 1, before: originalRule, after: postingRule },
}

export function proposeDateRuleEdit(rule: DateRule) {
  return edits[rule]
}

export function applyDateRuleEdit(rule: DateRule) {
  return applyReviewEdits(demoSource, [proposeDateRuleEdit(rule)])
}

export function checkDateConflict(text = demoSource.join('\n')) {
  const [rule, scenario] = text.split('\n')
  const [, purchase, posting, result] = scenario?.split('|').map(value => value.trim()) ?? []
  const months = {
    января: { name: 'Январь', counter: 'Январский' },
    февраля: { name: 'Февраль', counter: 'Февральский' },
  } as const
  const purchaseMonth = purchase?.match(/^Покупка \d+ (января|февраля) /)?.[1] as keyof typeof months | undefined
  const postingMonth = posting?.match(/^Провести \d+ (января|февраля) /)?.[1] as keyof typeof months | undefined
  const basis = rule === originalRule ? purchaseMonth : rule === postingRule ? postingMonth : undefined
  const expected = basis && months[basis]
  const actual = Object.values(months).find(month => result?.startsWith(`${month.counter} счётчик вырос на 4 000 ₸`))
  if (!expected || !actual) throw new Error('Не удалось сверить правило и ожидаемый результат.')
  return { ruleMonth: expected.name, exampleMonth: actual.name, hasConflict: expected.name !== actual.name }
}
