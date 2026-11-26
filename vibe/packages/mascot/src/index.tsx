import { useGaze } from '@blobatar/react/gaze'
import { happy, idle, sad, sleepy, surprised, thinking, unsure, wink, type Expression } from 'blobatar/expression'
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react'
import { useMascotMood, type MascotMood, type MascotState } from './performance'
export { mascotPerformance, type MascotMood, type MascotState } from './performance'
import { useId, useRef, useState, type CSSProperties } from 'react'
import bodyUrl from './glass-mascot-body.webp'
import 'blobatar/motion.css'
import 'blobatar/gaze.css'
import './mascot.css'

/** Muted glass ranges match the violet brand, neutral surfaces and report status colours. */
export const mascotPalettes = {
  violet: { hue: 0, saturation: 1, accent: '#8a55e7', eyes: '#fff7ff' },
  glacier: { hue: -55, saturation: .85, accent: '#5b82d9', eyes: '#f2faff' },
  jade: { hue: -120, saturation: .77, accent: '#399988', eyes: '#f1fffa' },
  amber: { hue: 135, saturation: .77, accent: '#bd8b4a', eyes: '#fffaf0' },
  rose: { hue: 85, saturation: .74, accent: '#c46d93', eyes: '#fff5f8' },
  graphite: { hue: 0, saturation: .12, accent: '#797c89', eyes: '#fafafa' },
} as const
export type MascotPalette = keyof typeof mascotPalettes
const paletteNames = Object.keys(mascotPalettes) as MascotPalette[]
const statePalettes: Record<MascotState, MascotPalette> = {
  idle: 'violet', thinking: 'glacier', speaking: 'violet', success: 'jade', error: 'rose',
}

function seedHash(seed: string) {
  let hash = 2166136261
  for (const char of seed) hash = Math.imul(hash ^ char.codePointAt(0)!, 16777619)
  return hash >>> 0
}

const expressions: Record<MascotMood, Expression> = {
  neutral: idle, wink, curious: unsure, bored: { ...sleepy, p: { ...sleepy.p, esy: .48, esx: 1.14, edy: 2.4, bdy: 1.1 } },
  yawn: sleepy, sleepy, wake: surprised, ponder: thinking, inspect: unsure, spark: surprised,
  speaking: idle, joy: happy, error: sad,
}

const mouthPaths: Record<MascotMood, string> = {
  neutral: 'M47.9 63.3 Q50.7 65 53.5 63.3',
  wink: 'M47.9 62.9 Q50.7 66.1 54 63',
  curious: 'M49.1 64 Q51.2 65.2 53.3 63.6',
  bored: 'M48.4 64.7 H53.1',
  yawn: 'M50.7 61.8 C53.2 61.8 53.2 66.6 50.7 66.6 C48.2 66.6 48.2 61.8 50.7 61.8Z',
  sleepy: 'M49.4 64 Q50.7 64.7 52 64',
  wake: 'M50.7 62 C52.5 62 52.5 65.5 50.7 65.5 C48.9 65.5 48.9 62 50.7 62Z',
  ponder: 'M48.2 63.7 Q50.3 65 53.1 63.4',
  inspect: 'M48.8 64.4 Q51.1 62.9 53.6 64',
  spark: 'M47.8 63 Q50.7 66.2 53.8 63',
  speaking: 'M48.1 63.8 Q50.7 66.1 53.3 63.8',
  joy: 'M47.2 62.8 Q50.7 66.5 54.2 62.8',
  error: 'M48 65 Q50.7 62.6 53.4 65',
}

/** The approved shell is shared; account fallbacks get a small deterministic face variation. */
function eyeGeometry(seed?: string) {
  if (!seed) return { gap: 11, width: 4.65, height: 8.9, y: 55.9 }
  const hash = seedHash(seed)
  return {
    gap: 10.4 + (hash >>> 0) % 9 * .15,
    width: 4.35 + ((hash >>> 8) % 6) * .12,
    height: 8.2 + ((hash >>> 16) % 9) * .18,
    y: 55.5 + ((hash >>> 24) % 5) * .15,
  }
}

const gesturePoses = {
  rest: { scaleX: 1, scaleY: 1, y: 0 },
  hover: { scaleX: 1.06, scaleY: .97, y: -1.5 },
  press: { scaleX: 1.16, scaleY: .86, y: 3 },
} as const
// Blooby's app presets pair the axes and settle the face after the chassis.
const glassGesturePoses = {
  rest: gesturePoses.rest,
  hover: { scaleX: 1.025, scaleY: 1 / 1.025, y: -1.5 },
  press: { scaleX: 1.09, scaleY: 1 / 1.09, y: 2 },
} as const
const elasticSpring = { type: 'spring' as const, duration: .5, bounce: .24 }

export type GlassMascotProps = {
  state?: MascotState
  /** Explicit decorative pose, useful in the animation preview. */
  mood?: MascotMood
  palette?: MascotPalette
  /** Only defaults for actual accounts; the assistant always has one fixed identity. */
  seed?: string
  followPointer?: boolean
  ambient?: boolean
  spinOnHover?: boolean
  /** Opt into the large assistant’s reactive screen and gestures. */
  expressive?: boolean
  size?: number
  className?: string
}

/** Local glass character with Blobatar 2.7's pose, blink, breathing and gaze layers.
 * The blank raster shell preserves the approved industrial artwork; all facial features
 * are actual SVG elements, not an animated crop or baked expression. The SVG rig matches
 * the pinned Blobatar motion/gaze styles and provides a transparent silhouette for survey().
 */
export function GlassMascot({
  state = 'idle', mood: moodOverride, palette, seed, followPointer = true, ambient = false,
  spinOnHover = false, expressive = false, size = 48, className,
}: GlassMascotProps) {
  const reduce = useReducedMotion() === true
  const playful = ambient || spinOnHover || expressive
  const gesturesEnabled = playful && !reduce
  const [gesture, setGesture] = useState<keyof typeof gesturePoses>('rest')
  const [pulse, setPulse] = useState(0)
  const lensId = useId()
  const pressed = useRef(false)
  const leanTarget = useMotionValue(0)
  const lean = useSpring(leanTarget, { duration: .5, bounce: .22 })
  // Project the face across a shallow curved surface, with a softer spring than the body.
  const faceAngle = useSpring(leanTarget, { stiffness: 120, damping: 16 })
  const faceX = useTransform(faceAngle, value => Math.sin(value * Math.PI / 42) * 5)
  const faceWidth = useTransform(faceAngle, value => .9 + .1 * Math.cos(value * Math.PI / 24))
  const pointerBounds = useRef<DOMRect | null>(null)
  const mood = useMascotMood(state, playful, moodOverride, reduce)
  const { ref } = useGaze({ travel: 5, lookAt: followPointer ? 'pointer' : null })
  const { gap, width, height, y } = eyeGeometry(seed)
  const expression = expressions[mood]
  const paletteName = palette ?? (seed ? paletteNames[seedHash(seed) % paletteNames.length]! : statePalettes[state])
  const colour = mascotPalettes[paletteName]
  const active = state === 'thinking' || state === 'speaking'
  const style = {
    '--mo-head': colour.accent, '--mo-eye': colour.eyes,
    '--gm-hue': `${colour.hue}deg`, '--gm-saturation': colour.saturation,
    '--mo-blink': '4900ms', '--mo-blink-phase': '-1900ms',
    '--mo-look-x': '.9', '--mo-look-mx': '-.9', '--mo-look-y': '-.5', '--mo-look-my': '.5',
    '--mo-saccade': '5800ms', '--mo-saccade-phase': '-2500ms',
    ...expression.vars(expression.p),
  } as CSSProperties
  const eyeCenter = 50.9
  return <svg
    ref={followPointer ? ref : undefined}
    xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width={size} height={size}
    className={['glass-mascot', className].filter(Boolean).join(' ')}
    style={style} aria-hidden="true" focusable="false"
    data-mascot="glass" data-mascot-state={state} data-mascot-mood={mood} data-mascot-palette={paletteName}
    data-playful={playful || undefined} data-expressive={expressive || undefined} data-gesture={gesturesEnabled ? gesture : undefined}
    data-gaze={followPointer ? 'pointer' : undefined} data-spin={spinOnHover || undefined}
  >
    {expressive && <defs>
      <clipPath id={`${lensId}-screen`}>
        <path d="M19 63 C20 56 34 44 49 35 C58 30 65 28 70 36 C73 41 77 52 77 58 C78 66 71 70 65 71 C55 73 44 74 35 74 C26 74 18 71 19 63Z" />
      </clipPath>
      <linearGradient id={`${lensId}-light`}>
        <stop offset="0" stopColor={colour.eyes} stopOpacity="0" />
        <stop offset=".5" stopColor={colour.eyes} stopOpacity=".28" />
        <stop offset="1" stopColor={colour.eyes} stopOpacity="0" />
      </linearGradient>
    </defs>}
    <motion.g className="gm-elastic" initial={false} style={{ transformBox: 'view-box', originX: .5, originY: .8 }}
      animate={gesturesEnabled ? (expressive ? glassGesturePoses : gesturePoses)[gesture] : gesturePoses.rest}
      transition={gesturesEnabled ? elasticSpring : { duration: 0 }}
      onPointerEnter={gesturesEnabled ? event => {
        if (event.pointerType !== 'touch') { pointerBounds.current = event.currentTarget.ownerSVGElement?.getBoundingClientRect() ?? null; setGesture('hover') }
      } : undefined}
      onPointerMove={gesturesEnabled ? event => {
        const bounds = pointerBounds.current
        if (event.pointerType !== 'touch' && bounds && bounds.width > 0) {
          const dx = (event.clientX - (bounds.left + bounds.width / 2)) / (bounds.width / 2)
          leanTarget.set(Math.max(-1, Math.min(1, dx)) * 7)
        }
      } : undefined}
      onPointerDown={gesturesEnabled ? event => {
        if (!event.isPrimary || event.button !== 0) return
        pressed.current = true
        setGesture('press')
      } : undefined}
      onPointerUp={gesturesEnabled ? event => {
        if (!event.isPrimary || !pressed.current) return
        pressed.current = false
        setGesture(event.pointerType === 'touch' ? 'rest' : 'hover')
        if (expressive) setPulse(value => value + 1)
      } : undefined}
      onPointerCancel={gesturesEnabled ? () => { pressed.current = false; pointerBounds.current = null; leanTarget.set(0); setGesture('rest') } : undefined}
      onPointerLeave={gesturesEnabled ? () => { pressed.current = false; pointerBounds.current = null; leanTarget.set(0); setGesture('rest') } : undefined}>
    <motion.g className="gm-lean" style={{ transformBox: 'view-box', originX: .5, originY: .8, rotate: gesturesEnabled ? lean : 0 }}>
    <g className="gm-story"><g className="gm-spin">
      <g className={`mo-root${active || ambient ? ' mo-always' : ''}${expression !== idle ? ' mo-expr' : ''}`}>
        <g className="mo-breathe"><g className="mo-bob">
          <g className="gm-body">
            <image className="gm-shell" href={bodyUrl} x="0" y="0" width="100" height="100" preserveAspectRatio="xMidYMid meet" />
            {/* Geometric proxy lets Blobatar's gaze survey measure the chassis locally. */}
            <path d="M5.6 67 C5.6 56 22 38 34 23 C42 13 49 11 55 11 C67 11 75 24 82 39 L91 58 Q100 79 86 85 Q77 91 52 89 L23 86 Q6 84 5.6 67Z" fill="transparent" pointerEvents="none" />
          </g>
          {expressive && !reduce && <g clipPath={`url(#${lensId}-screen)`} pointerEvents="none">
            <g className="gm-lens-scan">
              <rect x="0" y="23" width="26" height="57" transform="skewX(-19)" fill={`url(#${lensId}-light)`} />
            </g>
            {pulse > 0 && <ellipse key={pulse} className="gm-lens-pulse" cx="50" cy="59" rx="8" ry="6" fill="none" stroke={colour.eyes} strokeWidth=".8" />}
          </g>}
          <motion.g style={expressive && !reduce ? { x: faceX, scaleX: faceWidth, transformBox: 'view-box', originX: .51, originY: .6 } : undefined}>
          <g className="mo-eyes" fill={colour.eyes}>
            {[-1, 1].map((side) => {
              const x = eyeCenter + side * gap
              return <g key={side} className="mo-eye" style={{ '--mo-wrap': side, '--mo-lean': 0, transformOrigin: `${x}px ${y}px` } as CSSProperties}>
                <rect x={x - width / 2} y={y - height / 2} width={width} height={height} rx={width / 2} className="gm-eye-shape" />
              </g>
            })}
            <path d={mouthPaths[mood]} className="gm-mouth" fill="none" stroke={colour.eyes} strokeWidth=".95" strokeLinecap="round" />
          </g>
          </motion.g>
        </g></g>
      </g>
    </g></g>
    </motion.g>
    </motion.g>
    {playful && (state === 'thinking' && (mood === 'inspect' || mood === 'spark')) && <g className="gm-thought-dots" fill={colour.accent}>
      <circle cx="76" cy="18" r="1.25" /><circle cx="81" cy="14" r="1.8" /><circle cx="87" cy="10" r="2.3" />
    </g>}
    {playful && mood === 'sleepy' && <g className="gm-sleep-marks" fill="none" stroke={colour.accent} strokeWidth=".9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M75 23 H79 L75 27 H79" /><path d="M82 14 H88 L82 20 H88" />
    </g>}
  </svg>
}
