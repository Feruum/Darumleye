/**
 * `PUBLIC_WEBAPP_URL` is build-time configuration read by the React application. The unified
 * release always sets it to the web app's `https://` origin, and a local or webapp-less build
 * leaves it unset. A value that is set but is not an absolute http(s) URL is a configuration
 * mistake, so this throws and fails the static build instead of shipping a call to action that links
 * nowhere.
 */
export function resolvePublicWebappUrl(value: string | undefined): string | undefined {
  const webappUrl = value?.trim()
  if (!webappUrl) return undefined

  if (!isAbsoluteHttpUrl(webappUrl)) {
    throw new Error(
      `PUBLIC_WEBAPP_URL must be an absolute http(s) URL such as https://app.example.com, got "${webappUrl}". ` +
        'Leave it unset to build the website without a link to the web app.',
    )
  }

  return webappUrl
}

function isAbsoluteHttpUrl(value: string) {
  try {
    const { protocol } = new URL(value)
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

export function getWorkspaceActions(publicWebappUrl?: string) {
  const webappUrl = resolvePublicWebappUrl(publicWebappUrl)
  if (!webappUrl) return {
    primary: { href: '#process', label: 'Как это работает' },
    login: null,
  }

  function authUrl(path: '/login' | '/signup') {
    const url = new URL(path, webappUrl)
    url.searchParams.set('returnTo', '/app/assistant')
    return url.toString()
  }

  return {
    primary: { href: authUrl('/signup'), label: 'Начать проверку' },
    login: { href: authUrl('/login'), label: 'Войти' },
  }
}
