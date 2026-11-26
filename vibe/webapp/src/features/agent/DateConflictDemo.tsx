import { ArrowRight01Icon, Tick02Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import {
  applyDateRuleEdit, checkDateConflict, originalRule, originalScenario, proposeDateRuleEdit, type DateRule,
} from './date-conflict-demo'
import './date-conflict-demo.css'

const initial = checkDateConflict()
const choices = [
  { id: 'purchase', label: 'По дате покупки', detail: '4 000 ₸ попадут в январь' },
  { id: 'posting', label: 'По дате проведения', detail: '4 000 ₸ попадут в февраль' },
] as const

export function DateConflictDemo() {
  const [rule, setRule] = useState<DateRule>()
  const [draft, setDraft] = useState<string>()
  const [result, setResult] = useState<ReturnType<typeof checkDateConflict>>()
  const edit = rule ? proposeDateRuleEdit(rule) : undefined
  const sourceLine = rule === 'purchase' ? 64 : 38

  useEffect(() => {
    const previousTitle = document.title
    document.title = 'Конфликт дат · Spec Review'
    return () => { document.title = previousTitle }
  }, [])

  useEffect(() => {
    if (result) document.getElementById('date-comparison')?.focus()
  }, [result])

  function selectRule(value: DateRule) {
    setRule(value)
    setDraft(undefined)
    setResult(undefined)
  }

  function reset() {
    setRule(undefined)
    setDraft(undefined)
    setResult(undefined)
    document.getElementById('date-demo-title')?.focus()
  }

  return <main className="sr-date-demo">
    <header className="sr-date-header">
      <Link to="/" className="sr-date-brand"><Typography as="span" variant="emphasis">Spec Review</Typography></Link>
      <Typography as="span" variant="caption">Демо · BCC HUB × MNU</Typography>
    </header>
    <div className="sr-date-content">
      <div className="sr-date-intro">
        <Typography as="span" variant="caption" className="sr-date-issue"><span aria-hidden />Противоречие в ТЗ</Typography>
        <Typography as="h1" variant="h1" id="date-demo-title" tabIndex={-1}>В какой месяц попадает покупка?</Typography>
        <Typography as="p" variant="body" tone="muted">Одна покупка на 4 000 ₸. Правило относит её к январю, а критерий приёмки — к февралю.</Typography>
      </div>

      <section className="sr-date-evidence" aria-label="Два фрагмента исходного ТЗ">
        <article>
          <div className="sr-date-location"><Typography as="strong" variant="emphasis">Правило Ф‑2</Typography><Typography as="span" variant="caption">Строка 38</Typography></div>
          <Typography as="div" variant="h2" className="sr-date-month">{initial.ruleMonth}</Typography>
          <Typography as="blockquote" variant="bodySm">«{originalRule}»</Typography>
        </article>
        <article>
          <div className="sr-date-location"><Typography as="strong" variant="emphasis">Критерий приёмки</Typography><Typography as="span" variant="caption">Строка 64</Typography></div>
          <Typography as="div" variant="h2" className="sr-date-month">{initial.exampleMonth}</Typography>
          <Typography as="blockquote" variant="bodySm">«{originalScenario.slice(2, -2)}»</Typography>
        </article>
      </section>

      <section className="sr-date-decision" aria-labelledby="date-decision">
        <fieldset>
          <Typography as="legend" variant="h2" id="date-decision">Какое правило принимаем?</Typography>
          <div className="sr-date-options">
            {choices.map(choice => <label key={choice.id}>
              <input type="radio" name="month-rule" value={choice.id} checked={rule === choice.id} onChange={() => selectRule(choice.id)} />
              <span><Typography as="strong" variant="emphasis">{choice.label}</Typography><Typography as="small" variant="caption" tone="muted">{choice.detail}</Typography></span>
            </label>)}
          </div>
        </fieldset>

        {edit && <div className="sr-date-edit">
          <div className="sr-date-location"><Typography as="strong" variant="emphasis">Предложенная правка</Typography><Typography as="span" variant="caption">Строка {sourceLine}</Typography></div>
          <div className="sr-date-edit-lines">
            <Typography as="p" variant="bodySm"><Typography as="span" variant="caption" aria-hidden>−</Typography><Typography as="del" variant="bodySm">{edit.before}</Typography></Typography>
            <Typography as="p" variant="bodySm"><Typography as="span" variant="caption" aria-hidden>+</Typography><Typography as="ins" variant="bodySm">{edit.after}</Typography></Typography>
          </div>
        </div>}

        <div className="sr-date-actions">
          <Typography as="p" variant="caption" tone="muted" role="status">
            {draft ? 'Правка применена к демо-копии.' : edit ? 'Проверьте формулировку и примените правку.' : 'Выберите дату, по которой считаем месяц.'}
          </Typography>
          {!draft ? <Button type="button" disabled={!rule} onClick={() => { if (rule) setDraft(applyDateRuleEdit(rule)) }}>
            Применить правку <HugeiconsIcon icon={ArrowRight01Icon} size={17} aria-hidden />
          </Button> : <Button type="button" disabled={Boolean(result)} onClick={() => setResult(checkDateConflict(draft))}>
            {result ? 'Сверка завершена' : 'Проверить повторно'} <HugeiconsIcon icon={result ? Tick02Icon : ArrowRight01Icon} size={17} aria-hidden />
          </Button>}
        </div>
      </section>

      {result && <section className="sr-date-comparison" aria-labelledby="date-comparison">
        <header>
          <div>
            <Typography as="span" variant="caption" className="sr-date-issue" data-state={result.hasConflict ? undefined : 'resolved'}><span aria-hidden />{result.hasConflict ? 'Конфликт остался' : 'Конфликт снят'}</Typography>
            <Typography as="h2" variant="h2" id="date-comparison" tabIndex={-1}>До и после правки</Typography>
          </div>
          <Button variant="outline" type="button" onClick={reset}>Начать заново</Button>
        </header>
        <div className="sr-date-compare-grid">
          {[{ title: 'До', check: initial }, { title: 'После', check: result }].map(({ title, check }) => <article key={title}>
            <Typography as="h3" variant="bodySmMedium">{title}</Typography>
            <dl>
              <div><Typography as="dt" variant="bodySm">Месяц по правилу</Typography><Typography as="dd" variant="bodySm">{check.ruleMonth}</Typography></div>
              <div><Typography as="dt" variant="bodySm">Месяц в примере</Typography><Typography as="dd" variant="bodySm">{check.exampleMonth}</Typography></div>
            </dl>
            <Typography as="strong" variant="emphasis" data-state={check.hasConflict ? undefined : 'resolved'}>{check.hasConflict ? 'Месяцы расходятся' : 'Месяцы совпадают'}</Typography>
          </article>)}
        </div>
      </section>}
      <footer className="sr-date-footer">
        <Typography as="span" variant="caption">Локальная сверка одного конфликта. Без запуска AI‑ревью.</Typography>
        <a href="https://github.com/BCC-HUB/ai-requirements-validator-task/blob/HEAD/docs/requirements.md" target="_blank" rel="noreferrer"><Typography as="span" variant="caption">Исходное ТЗ ↗</Typography></a>
      </footer>
    </div>
  </main>
}
