import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { Avatar, AvatarFallback, AvatarImage } from '../src/components/interface/avatar'
import { GlassMascot, mascotPerformance } from '@web-app-demo/mascot'
import { UserMenu } from '../src/components/interface/user-menu'

function renderAvatar(seed: string, name = 'Sample account', photo?: string) {
  return renderToStaticMarkup(createElement(Avatar, { seed, name },
    photo ? createElement(AvatarImage, { src: photo, alt: '' }) : null,
    createElement(AvatarFallback, { 'data-testid': 'avatar-fallback' }, 'SA'),
  ))
}

function face(html: string) {
  return html.match(/<svg\b[\s\S]*?<\/svg>/)?.[0]
}

test('an account without a photo receives the local glass mascot', () => {
  const html = renderAvatar('account-1')
  expect(html).toContain('data-mascot-state="idle"')
  expect(html).toContain('data-testid="avatar-fallback"')
  expect(html).not.toContain('blobatar.dev/avatar')
})

test('account identity determines the face and a display-name change does not replace it', () => {
  const original = face(renderAvatar('account-1'))
  expect(original).toBeDefined()
  expect(face(renderAvatar('account-1', 'Renamed account'))).toBe(original)
  expect(face(renderAvatar('account-2'))).not.toBe(original)
})

test('an uploaded account photo takes precedence over its generated fallback', () => {
  const html = renderAvatar('account-1', 'Sample account', 'blob:synthetic-uploaded-photo')
  expect(html).toContain('src="blob:synthetic-uploaded-photo"')
  expect(html).not.toContain('data-mascot=')
})

test('the thinking avatar is decorative and uses the explicit thinking pose', () => {
  const html = renderToStaticMarkup(createElement(GlassMascot, { state: 'thinking' }))
  expect(html).toContain('data-mascot-state="thinking"')
  expect(html).toContain('aria-hidden="true"')
  expect(html).toContain('focusable="false"')
  expect(html).not.toContain('blobatar.dev/avatar')
})

test('pointer gaze is enabled on mascot surfaces and can be disabled for old messages', () => {
  const assistant = renderToStaticMarkup(createElement(GlassMascot, { followPointer: true }))
  expect(assistant).toContain('data-gaze="pointer"')
  expect(renderAvatar('account-1')).toContain('data-gaze="pointer"')
  expect(renderToStaticMarkup(createElement(GlassMascot, { followPointer: false }))).not.toContain('data-gaze="pointer"')
})

test('the profile and account menu use the same face for the same account ID', () => {
  const menu = renderToStaticMarkup(createElement(UserMenu, {
    avatarSeed: 'account-1',
    user: { name: 'Sample account', email: 'sample@example.com' },
    showTheme: false,
  }))
  expect(face(menu)).toBe(face(renderAvatar('account-1')))
})

test('the account menu keeps the uploaded photo ahead of the mascot', () => {
  const menu = renderToStaticMarkup(createElement(UserMenu, {
    avatarSeed: 'account-1',
    user: { name: 'Sample account', email: 'sample@example.com', avatarSrc: 'blob:synthetic-uploaded-photo' },
    showTheme: false,
  }))
  expect(menu).toContain('src="blob:synthetic-uploaded-photo"')
  expect(menu).not.toContain('data-mascot=')
})


test('the approved glass body and separate live SVG eyes are rendered as one mascot', () => {
  const html = renderToStaticMarkup(createElement(GlassMascot, { state: 'thinking', ambient: true, spinOnHover: true }))
  expect(html).toContain('data-mascot="glass"')
  expect(html).toContain('glass-mascot-body.webp')
  expect(html.match(/class="mo-eye"/g)).toHaveLength(2)
  expect(html).not.toContain('data-glass-orbit')
  expect(html).toContain('data-spin="true"')
  expect(html).toContain('mo-always')
})

test('the mascot has a coordinated palette, with distinct state colours and stable account colours', () => {
  const render = (props: Parameters<typeof GlassMascot>[0]) => renderToStaticMarkup(createElement(GlassMascot, props))
  expect(render({})).toContain('data-mascot-palette="violet"')
  expect(render({ state: 'thinking' })).toContain('data-mascot-palette="glacier"')
  expect(render({ state: 'success' })).toContain('data-mascot-palette="jade"')
  expect(render({ state: 'error' })).toContain('data-mascot-palette="rose"')
  for (const palette of ['violet', 'glacier', 'jade', 'amber', 'rose', 'graphite'] as const) {
    const markup = render({ palette })
    expect(markup).toContain(`data-mascot-palette="${palette}"`)
    expect(markup).toContain('glass-mascot-body.webp')
  }
  const colours = Array.from({ length: 12 }, (_, index) => render({ seed: `account-${index}` }).match(/data-mascot-palette="([^"]+)"/)?.[1])
  expect(new Set(colours).size).toBeGreaterThan(2)
  expect(render({ seed: 'account-1' })).toBe(render({ seed: 'account-1' }))
})

test('automatic idle moods get bored, yawn, then sleep instead of continuously performing', () => {
  expect(mascotPerformance('idle', 0, true)).toMatchObject({ mood: 'neutral', nextInMs: 7200 })
  expect(mascotPerformance('idle', 7500, true).mood).toBe('wink')
  expect(mascotPerformance('idle', 15000, true).mood).toBe('bored')
  expect(mascotPerformance('idle', 21800, true).mood).toBe('yawn')
  expect(mascotPerformance('idle', 30000, true)).toEqual({ mood: 'sleepy', nextInMs: null })
  expect(mascotPerformance('idle', 30000, false)).toEqual({ mood: 'neutral', nextInMs: null })
})

test('thinking cycles expressive poses without pretending to finish the review', () => {
  expect(mascotPerformance('thinking', 0, true).mood).toBe('ponder')
  expect(mascotPerformance('thinking', 3600, true).mood).toBe('inspect')
  expect(mascotPerformance('thinking', 6200, true).mood).toBe('spark')
  expect(mascotPerformance('thinking', 8000, true).mood).toBe('ponder')
  expect(mascotPerformance('success', 30000, true)).toEqual({ mood: 'joy', nextInMs: null })
})

test('decorative moods preserve the real operational state and separate the elastic rig', () => {
  const bored = renderToStaticMarkup(createElement(GlassMascot, { state: 'idle', mood: 'bored', ambient: true }))
  expect(bored).toContain('data-mascot-state="idle"')
  expect(bored).toContain('data-mascot-mood="bored"')
  expect(bored).toContain('gm-elastic')
  expect(bored).toContain('gm-lean')
  expect(bored).not.toContain('tabindex=')
  const yawn = renderToStaticMarkup(createElement(GlassMascot, { state: 'idle', mood: 'yawn' }))
  expect(yawn).toContain('data-mascot-mood="yawn"')
  const thinking = renderToStaticMarkup(createElement(GlassMascot, { state: 'thinking', mood: 'spark', ambient: true }))
  expect(thinking).toContain('data-mascot-state="thinking"')
  expect(thinking).toContain('data-mascot-mood="spark"')
  expect(thinking).toContain('gm-thought-dots')
})
