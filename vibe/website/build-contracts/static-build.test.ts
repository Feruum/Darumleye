import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

/**
 * Build contracts read the static `dist/` that `bun run test:build-contracts` (repository root)
 * builds right before them. They never build on their own: the unit suite in `tests/` stays
 * read-only and free of the developer's build environment, and a build-contract file run by itself
 * checks whatever `dist/` currently holds.
 */
const websiteRoot = fileURLToPath(new URL('..', import.meta.url))
const dist = resolve(websiteRoot, 'dist')

test('the static build exposes landing content and native navigation without JavaScript', () => {
  assert.ok(
    existsSync(dist),
    `${dist} is missing: run \`bun run test:build-contracts\` from the repository root, which builds before it checks`,
  )

  const storySource = readFileSync(
    resolve(websiteRoot, 'src/stories/ui/demos.tsx'),
    'utf8',
  )
  assert.match(storySource, /h-\[34rem\]/)

  const assets = resolve(dist, 'assets')
  const html = readFileSync(resolve(dist, 'index.html'), 'utf8')
  const scripts = readdirSync(assets)
    .filter((name) => name.endsWith('.js'))
    .map((name) => ({ name, source: readFileSync(resolve(assets, name), 'utf8') }))
  const cssFiles = readdirSync(assets).filter((name) => name.endsWith('.css'))
  assert.ok(cssFiles.length > 0, `expected at least one CSS asset in ${assets}`)
  const css = cssFiles.map((name) => readFileSync(resolve(assets, name), 'utf8')).join('\n')
  const shellUrl = html.match(/<script[^>]*src="([^"]+\.js)"/)?.[1]

  assert.match(html, /data-review-example/)
  assert.match(html, /id="main-content"/)
  assert.match(html, /id="hero-title"/)
  assert.match(html, /<title>[^<]+<\/title>/)
  assert.match(html, /name="description"/)
  assert.doesNotMatch(html, /astro-island|astro\/|<!--app-html-->/)
  assert.ok(!css.includes('.h-\\[34rem\\]{'))
  for (const match of html.matchAll(/href="#([^" ]+)"/g)) {
    assert.ok(html.includes(`id="${match[1]}"`), `missing pre-rendered anchor target: ${match[1]}`)
  }
  assert.ok(scripts.every(({ source }) => !source.includes('data-hero-scene-canvas')), 'the retired 3D scene is not part of the product landing build')
  assert.ok(shellUrl, 'expected the React client entrypoint')

  const shell = readFileSync(resolve(dist, shellUrl.slice(1)), 'utf8')
  assert.match(shell, /requestIdleCallback/)
})


test('the prerendered mascot shell and favicon are emitted as local assets', () => {
  const html = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.match(html, /data-mascot="glass"/)
  const shell = html.match(/<image[^>]*href="([^"]*glass-mascot-body[^"]*\.webp)"/)?.[1]
  assert.ok(shell, 'expected the local shell reference')
  assert.ok(existsSync(resolve(dist, shell.replace(/^\//, ''))))
  assert.ok(existsSync(resolve(dist, 'favicon-mascot.png')))
})
