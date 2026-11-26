import { useState } from 'react'
import { GlassMascot, mascotPalettes, type MascotPalette, type MascotState, type MascotMood } from '@web-app-demo/mascot'
import { Typography } from '@/components/typography'

const states: { value: MascotState; label: string }[] = [
  { value: 'idle', label: 'Обычный' }, { value: 'thinking', label: 'Думает' },
  { value: 'speaking', label: 'Отвечает' }, { value: 'success', label: 'Готово' },
  { value: 'error', label: 'Ошибка' },
]
const moods: { value: MascotMood; label: string; state: MascotState }[] = [
  { value: 'wink', label: 'Подмигивает', state: 'idle' }, { value: 'curious', label: 'Любопытный', state: 'idle' },
  { value: 'bored', label: 'Скучает', state: 'idle' }, { value: 'yawn', label: 'Зевает', state: 'idle' },
  { value: 'sleepy', label: 'Дремлет', state: 'idle' }, { value: 'wake', label: 'Просыпается', state: 'idle' },
  { value: 'ponder', label: 'Размышляет', state: 'thinking' }, { value: 'inspect', label: 'Сомневается', state: 'thinking' },
  { value: 'spark', label: 'Идея!', state: 'thinking' },
]
const labels: Record<MascotPalette, string> = {
  violet: 'Фиолетовый', glacier: 'Ледяной', jade: 'Мятный',
  amber: 'Янтарный', rose: 'Розовый', graphite: 'Графитовый',
}

export function MascotPreview() {
  const [state, setState] = useState<MascotState>('idle')
  const [mood, setMood] = useState<MascotMood | undefined>()
  const [palette, setPalette] = useState<MascotPalette | undefined>()
  return <main className="preview-page">
    <header><Typography as="span" variant="caption">SPEC REVIEW / DESIGN</Typography><Typography as="a" href="/app/assistant" variant="bodySm">К рабочему кабинету →</Typography></header>
    <Typography as="h1" variant="h1">Живой маскот</Typography>
    <Typography as="p" variant="bodySm" tone="muted">Наведи курсор на маскота или нажми на него. Выбери состояние, чтобы увидеть, как он реагирует.</Typography>
    <section className="preview-grid" aria-label="Предпросмотр маскота">
      <div className="preview-stage">
        <div className="preview-character" data-testid="preview-character"><GlassMascot state={state} mood={mood} palette={palette} size={260} ambient expressive /></div>
        <Typography as="div" variant="caption" className="preview-state" aria-live="polite">{mood ? moods.find(item => item.value === mood)?.label : states.find(item => item.value === state)?.label} · {palette ? labels[palette] : 'Авто'}</Typography>
      </div>
      <div className="preview-controls">
        <Typography as="h2" variant="h4">Состояние</Typography>
        <div className="preview-buttons" role="group" aria-label="Состояние маскота">{states.map(({value, label}) => <button key={value} type="button" aria-pressed={state === value} onClick={() => { setState(value); setMood(undefined) }}><Typography as="span" variant="controlXs">{label}</Typography></button>)}</div>
        <div className="preview-palette-title"><Typography as="h2" variant="h4">Характер</Typography><button type="button" aria-label="Автоматические эмоции" aria-pressed={mood === undefined} onClick={() => setMood(undefined)}><Typography as="span" variant="controlXs">Авто</Typography></button></div>
        <div className="preview-buttons" role="group" aria-label="Эмоции маскота">{moods.map(({value, label, state: performanceState}) => <button key={value} type="button" aria-pressed={mood === value} onClick={() => { setState(performanceState); setMood(value) }}><Typography as="span" variant="controlXs">{label}</Typography></button>)}</div>
        <div className="preview-palette-title"><Typography as="h2" variant="h4">Палитра</Typography><button type="button" aria-label="Автоматическая палитра" aria-pressed={palette === undefined} onClick={() => setPalette(undefined)}><Typography as="span" variant="controlXs">Авто</Typography></button></div>
        <div className="preview-palettes" role="group" aria-label="Цветовая палитра">{(Object.keys(mascotPalettes) as MascotPalette[]).map(name => <button type="button" key={name} className="preview-palette" aria-pressed={palette === name} onClick={() => setPalette(name)}><span><GlassMascot size={66} palette={name} followPointer={false} /></span><Typography as="span" variant="caption">{labels[name]}</Typography></button>)}</div>
      </div>
    </section>
  </main>
}

