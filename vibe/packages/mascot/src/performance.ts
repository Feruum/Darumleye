import { useEffect, useState } from 'react'

export type MascotState = 'idle' | 'thinking' | 'speaking' | 'success' | 'error'
export type MascotMood = 'neutral' | 'wink' | 'curious' | 'bored' | 'yawn' | 'sleepy' | 'wake' | 'ponder' | 'inspect' | 'spark' | 'speaking' | 'joy' | 'error'

type Frame = { mood: MascotMood; nextInMs: number | null }
const idleSequence: readonly [end: number, mood: MascotMood][] = [
  [7200, 'neutral'], [8200, 'wink'], [12800, 'curious'], [21000, 'bored'], [23000, 'yawn'], [Infinity, 'sleepy'],
]
const thoughtCycle = 7800
const thoughtSequence: readonly [end: number, mood: MascotMood][] = [
  [3200, 'ponder'], [5800, 'inspect'], [6900, 'spark'], [thoughtCycle, 'ponder'],
]
const baseMoods: Record<MascotState, MascotMood> = { idle: 'neutral', thinking: 'ponder', speaking: 'speaking', success: 'joy', error: 'error' }

/** Only decorative performances are timed. Operational state and palette stay with the caller. */
export function mascotPerformance(state: MascotState, elapsedMs: number, playful: boolean): Frame {
  const elapsed = Math.max(0, elapsedMs)
  const sequence = state === 'thinking' ? thoughtSequence : state === 'idle' && playful ? idleSequence : null
  if (!sequence) return { mood: baseMoods[state], nextInMs: null }
  const position = state === 'thinking' ? elapsed % thoughtCycle : elapsed
  const [end, mood] = sequence.find(([until]) => position < until) ?? sequence[sequence.length - 1]!
  return { mood, nextInMs: Number.isFinite(end) ? Math.max(1, end - position) : null }
}

const drowsy = new Set<MascotMood>(['bored', 'yawn', 'sleepy'])

/** One timeout per performance, never an animation frame loop in React. */
export function useMascotMood(state: MascotState, playful: boolean, override: MascotMood | undefined, reduce: boolean): MascotMood {
  const [snapshot, setSnapshot] = useState<{ state: MascotState; mood: MascotMood }>({ state, mood: baseMoods[state] })
  const automatic = override === undefined && !reduce && (state === 'thinking' || (state === 'idle' && playful))
  useEffect(() => {
    if (!automatic) return
    let startedAt = performance.now()
    let wakeUntil = 0
    let current = baseMoods[state]
    let timer: ReturnType<typeof setTimeout> | undefined
    function stopTimer() { if (timer !== undefined) { clearTimeout(timer); timer = undefined } }
    function publish(mood: MascotMood) {
      current = mood
      setSnapshot(previous => previous.state === state && previous.mood === mood ? previous : { state, mood })
    }
    function tick() {
      stopTimer()
      if (document.hidden) return
      const now = performance.now()
      const frame = wakeUntil > now ? { mood: 'wake' as const, nextInMs: wakeUntil - now } : mascotPerformance(state, now - startedAt, playful)
      publish(frame.mood)
      if (frame.nextInMs !== null) timer = setTimeout(tick, Math.ceil(frame.nextInMs))
    }
    function activity() {
      if (document.hidden) return
      const now = performance.now()
      startedAt = now
      if (drowsy.has(current)) { wakeUntil = now + 820; tick() }
      else if (current !== 'neutral' && current !== 'wake') { wakeUntil = 0; tick() }
      else if (timer === undefined) tick()
      // Neutral pointer movement only resets the timestamp, not the React tree or timer.
    }
    function visibility() {
      if (document.hidden) { stopTimer(); return }
      if (state === 'idle') { startedAt = performance.now(); wakeUntil = 0 }
      tick()
    }
    if (state === 'idle') {
      window.addEventListener('pointermove', activity, { passive: true })
      window.addEventListener('pointerdown', activity, { passive: true })
      window.addEventListener('keydown', activity)
    }
    document.addEventListener('visibilitychange', visibility)
    tick()
    return () => {
      stopTimer()
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('pointermove', activity)
      window.removeEventListener('pointerdown', activity)
      window.removeEventListener('keydown', activity)
    }
  }, [state, playful, automatic])
  return override ?? (automatic && snapshot.state === state ? snapshot.mood : baseMoods[state])
}
