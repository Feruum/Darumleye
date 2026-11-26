import { Outlet, useRouterState } from '@tanstack/react-router'

import { AuthProvider } from '@/features/auth'

export function RootLayout() {
  const demo = useRouterState({
    select: state => state.location.pathname.replace(/\/$/, '') === '/demo/date-conflict',
  })
  return demo ? <Outlet /> : <AuthProvider><Outlet /></AuthProvider>
}
