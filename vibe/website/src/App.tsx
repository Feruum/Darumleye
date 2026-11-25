import { GlassMascot } from '@web-app-demo/mascot'
import { ArrowDown, ArrowRight, FileText, ListChecks, Quote, ScanText } from 'lucide-react'

import { ButtonLink } from '@/components/interface/button'
import { getWorkspaceActions } from '@/lib/landing-actions'

const stages = [
  { title: 'Извлечение', description: 'Читаем текст из PDF, Markdown или TXT.', icon: ScanText },
  { title: 'Декомпозиция', description: 'Выделяем отдельные требования.', icon: ListChecks },
  { title: 'Проверка', description: 'Оцениваем требования и связи между ними.', icon: Quote },
  { title: 'Отчёт', description: 'Готовим выводы и рекомендации в Markdown.', icon: FileText },
]

const criteria = [
  ['Понятность и полнота', 'Достаточно ли чётко описано ожидаемое поведение.'],
  ['Проверяемость', 'Можно ли однозначно проверить выполнение требования.'],
  ['Согласованность', 'Нет ли противоречий и пропусков между требованиями.'],
]

function Brand() {
  return (
    <a href="#top" className="spec-brand" aria-label="Spec Review — на главную">
      <span className="spec-brand-mascot"><GlassMascot size={36} /></span>
      <span>spec<span className="spec-brand-slash">/</span>review</span>
    </a>
  )
}

function ReviewExample() {
  return (
    <figure className="spec-example" aria-labelledby="example-title" data-review-example>
      <div className="spec-example-header">
        <span className="spec-example-document"><span className="spec-example-mascot"><GlassMascot size={36} spinOnHover /></span> requirement.md</span>
        <span className="spec-example-tag">Пример отчёта</span>
      </div>
      <div className="spec-example-content">
        <span className="spec-label">Фрагмент требования</span>
        <blockquote className="spec-example-quote">«Система должна быстро обрабатывать платежи.»</blockquote>
        <div className="spec-example-finding">
          <span className="spec-example-status"><span aria-hidden="true" /> Требует уточнения</span>
          <h2 id="example-title">Не определено время обработки</h2>
          <p>Критерий: проверяемость. Слово «быстро» не задаёт измеримого условия.</p>
        </div>
        <div className="spec-example-recommendation">
          <span className="spec-label">Рекомендация</span>
          <p>Указать допустимое время обработки и условия его измерения.</p>
        </div>
      </div>
      <figcaption>Иллюстрация формата, не результат реальной проверки.</figcaption>
    </figure>
  )
}

export default function App({ year }: { year: number }) {
  const { primary, login } = getWorkspaceActions(import.meta.env.PUBLIC_WEBAPP_URL)
  return (
    <div className="spec-site" id="top">
      <a className="spec-skip" href="#main-content">Перейти к содержанию</a>
      <header className="spec-header">
        <div className="spec-container spec-header-content">
          <Brand />
          <nav className="spec-nav" aria-label="Основная навигация">
            <a href="#process">Как работает</a>
            <a href="#criteria">Критерии</a>
          </nav>
          <div className="spec-header-actions">
            {login && <a className="spec-login" href={login.href}>{login.label}</a>}
            <ButtonLink size="sm" className="spec-action" href={primary.href}>{primary.label}<ArrowRight aria-hidden="true" /></ButtonLink>
          </div>
        </div>
      </header>

      <main id="main-content">
        <section className="spec-container spec-hero" aria-labelledby="hero-title">
          <div className="spec-hero-copy">
            <p className="spec-eyebrow">BCC Hub × MNU · FinTech Hackathon</p>
            <h1 id="hero-title">Проверьте ТЗ<br className="spec-title-break" /> <span className="spec-hero-accent">до разработки.</span></h1>
            <p className="spec-lead">AI-агент анализирует качество требований, выявляет пробелы и противоречия. В отчёте — цитаты из документа и рекомендации по доработке.</p>
            <div className="spec-hero-actions">
              <ButtonLink size="lg" className="spec-action" href={primary.href}>{primary.label}<ArrowRight aria-hidden="true" /></ButtonLink>
              {login && <a className="spec-text-link" href="#process">Как работает <ArrowDown size={15} aria-hidden="true" /></a>}
            </div>
            <p className="spec-formats">PDF <span>·</span> TXT <span>·</span> Markdown</p>
          </div>
          <ReviewExample />
        </section>

        <section className="spec-section" id="process" aria-labelledby="process-title">
          <div className="spec-container">
            <div className="spec-section-heading">
              <div>
                <p className="spec-eyebrow">Процесс</p>
                <h2 id="process-title">От документа к отчёту</h2>
              </div>
              <p>Четыре этапа системной проверки.</p>
            </div>
            <ol className="spec-stages">
              {stages.map(({ title, description, icon: Icon }, index) => (
                <li key={title}>
                  <div className="spec-stage-top"><Icon size={20} strokeWidth={1.7} aria-hidden="true" /><span>0{index + 1}</span></div>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="spec-container spec-criteria" id="criteria" aria-labelledby="criteria-title">
          <div className="spec-criteria-intro">
            <p className="spec-eyebrow">Качество требований</p>
            <h2 id="criteria-title">Не только отдельные формулировки.</h2>
            <p>Проверяем весь набор и связи между требованиями. В отчёте — проверяемые цитаты, критичность замечаний и приоритеты доработки.</p>
          </div>
          <dl className="spec-criteria-list">
            {criteria.map(([name, description]) => <div key={name}><dt>{name}</dt><dd>{description}</dd></div>)}
          </dl>
        </section>

        {login && (
          <section className="spec-bottom" aria-label="Начать работу">
            <div className="spec-container spec-bottom-content">
              <div><h2>Готовы проверить требования?</h2><p>Загрузите документ в рабочую область и получите отчёт.</p></div>
              <ButtonLink size="lg" href={primary.href} className="spec-action spec-action-on-dark">{primary.label}<ArrowRight aria-hidden="true" /></ButtonLink>
            </div>
          </section>
        )}
      </main>

      <footer className="spec-footer"><div className="spec-container spec-footer-content">
        <Brand />
        <p>© {year} Spec Review · Проект для BCC Hub × MNU FinTech Hackathon</p>
        <a href="#top">Наверх ↑</a>
      </div></footer>
    </div>
  )
}
