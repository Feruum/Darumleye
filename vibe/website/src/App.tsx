import { GlassMascot } from '@web-app-demo/mascot'
import { ArrowRight, Pause, Play } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import reviewDemo from '@/assets/review-demo.mp4'
import reviewPoster from '@/assets/review-demo.webp'
import { ButtonLink } from '@/components/interface/button'
import { getWorkspaceActions } from '@/lib/landing-actions'

const stages = [
  ['Загрузите ТЗ', 'Выберите файл. При необходимости добавьте регламенты для сверки.'],
  ['Разберите замечания', 'Откройте цитату рядом с замечанием и проверьте, к чему оно относится.'],
  ['Согласуйте правки', 'Сравните исходный текст с предложением и проверьте обновлённую версию.'],
]

function Brand() {
  return <a href="#top" className="spec-brand" aria-label="Spec Review — на главную">
    <span className="spec-brand-mascot"><GlassMascot size={36} followPointer={false} /></span>
    Spec Review
  </a>
}

function ReviewDemo() {
  const video = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    setReady(true)
    const movie = video.current
    if (!movie) return
    const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (!motionPreference.matches) void movie.play().catch(() => {})
    const pauseForReducedMotion = () => { if (motionPreference.matches) movie.pause() }
    motionPreference.addEventListener('change', pauseForReducedMotion)
    return () => {
      motionPreference.removeEventListener('change', pauseForReducedMotion)
      movie.pause()
    }
  }, [])

  function togglePlayback() {
    const movie = video.current
    if (!movie) return
    if (movie.paused) void movie.play().catch(() => {})
    else movie.pause()
  }

  return <figure className="spec-demo" data-review-example>
    <video ref={video} src={reviewDemo} poster={reviewPoster} width={1200} height={936}
      muted playsInline loop preload="none" aria-hidden="true"
      onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
    {ready && <button className="spec-demo-toggle" type="button" onClick={togglePlayback}
      aria-label={playing ? 'Приостановить анимацию' : 'Воспроизвести анимацию'}>
      {playing ? <Pause size={15} aria-hidden /> : <Play size={15} aria-hidden />}
    </button>}
    <figcaption className="sr-only">Учебный пример. Два агента находят противоречие в формуле лимита и неопределённое условие расчёта. Отчёт готов после сверки исходных строк.</figcaption>
  </figure>
}

export default function App({ year }: { year: number }) {
  const { primary, login } = getWorkspaceActions(import.meta.env.PUBLIC_WEBAPP_URL)
  const primaryLabel = login ? 'Проверить ТЗ' : primary.label
  return <div className="spec-site" id="top">
    <a className="spec-skip" href="#main-content">Перейти к содержанию</a>
    <header className="spec-header">
      <div className="spec-header-content">
        <Brand />
        <nav className="spec-nav" aria-label="Основная навигация">
          <a href="#process">Как работает</a>
          <a href="#example">Пример отчёта</a>
        </nav>
        <div className="spec-header-actions">
          {login && <a className="spec-login" href={login.href}>{login.label}</a>}
          <ButtonLink size="sm" className="spec-action" href={primary.href}>{primaryLabel}</ButtonLink>
        </div>
      </div>
    </header>

    <main id="main-content">
      <section className="spec-wide spec-hero" aria-labelledby="hero-title">
        <div className="spec-hero-copy">
          <p className="spec-eyebrow">Проверка технических заданий</p>
          <h1 id="hero-title">Найдите противоречия в ТЗ до разработки</h1>
          <p className="spec-lead">Загрузите документ и получите замечания с цитатами и предложениями по доработке.</p>
          <div className="spec-hero-actions">
            <ButtonLink size="lg" className="spec-action" href={primary.href}>{primaryLabel}</ButtonLink>
            <ButtonLink size="lg" variant="secondary" className="spec-action spec-action-secondary" href="#example">Смотреть пример</ButtonLink>
          </div>
          <p className="spec-formats">PDF, DOCX, TXT и другие форматы</p>
        </div>
        <ReviewDemo />
      </section>

      <section className="spec-container spec-process" id="process" aria-labelledby="process-title">
        <h2 id="process-title">От документа к понятным правкам</h2>
        <p className="spec-section-intro">Три шага, чтобы разобрать требования и подготовить новую версию.</p>
        <ol className="spec-stages">
          {stages.map(([title, description], index) => <li key={title}>
            <span className="spec-stage-number" aria-hidden="true">0{index + 1}</span>
            <h3>{title}</h3>
            <p>{description}</p>
          </li>)}
        </ol>
      </section>

      <section className="spec-wide spec-example" id="example" aria-labelledby="example-title">
        <div className="spec-example-heading">
          <div>
            <h2 id="example-title">Посмотрите, как выглядит результат</h2>
            <p className="spec-section-intro">Учебный отчёт на примере ТЗ по карточным лимитам.</p>
          </div>
          <ButtonLink size="lg" className="spec-action" href="#example-report">Открыть пример <ArrowRight aria-hidden /></ButtonLink>
        </div>
        <div className="spec-example-paper" id="example-report">
          <div>
            <span className="spec-source-id">BR-03 · Фрагмент ТЗ</span>
            <blockquote>«Лимит составляет 150% от среднемесячных расходов.»</blockquote>
          </div>
          <div>
            <span className="spec-source-id">Замечание</span>
            <h3>Конфликт формулы</h3>
            <p>В документе используются разные периоды расчёта. Укажите единый период и верхнюю границу лимита.</p>
          </div>
        </div>
        <details className="spec-criteria" id="criteria">
          <summary>Что проверяем</summary>
          <ul>
            <li>Понятность и полноту описания.</li>
            <li>Проверяемость каждого требования.</li>
            <li>Согласованность требований между собой.</li>
          </ul>
        </details>
      </section>
    </main>

    <footer className="spec-footer spec-container">
      <Brand />
      <p>© {year} Spec Review · Проект для BCC HUB × MNU FinTech Hackathon</p>
      <a href="#top">Наверх ↑</a>
    </footer>
  </div>
}
