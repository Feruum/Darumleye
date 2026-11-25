import {
  Add01Icon,
  DashboardSquare01Icon,
  Home01Icon,
  Message01Icon,
  Settings01Icon,
  UserGroupIcon,
  UserIcon,
} from '@hugeicons/core-free-icons'
import { Link, useLocation } from '@tanstack/react-router'
import { HugeiconsIcon } from '@hugeicons/react'
import type { UserDto } from '@web-app-demo/contracts'
import type { CSSProperties, PropsWithChildren, ReactNode } from 'react'

import {
  AppSidebar,
  type DashboardNavigationItem,
  SiteHeader,
} from '@/components/dashboard'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Typography } from '@/components/typography'
import {
  homePathForRole,
  navigationItemsForRole,
} from '@/features/navigation'

const iconsByPath = {
  '/app': Home01Icon,
  '/app/assistant': DashboardSquare01Icon,
  '/app/chat': Message01Icon,
  '/app/profile': UserIcon,
  '/app/settings': Settings01Icon,
  '/admin': DashboardSquare01Icon,
  '/admin/users': UserGroupIcon,
  '/admin/settings': Settings01Icon,
} as const

const mobileDestinations = [
  { to: '/app', label: 'Обзор' },
  { to: '/app/assistant', label: 'Проверка' },
  { to: '/app/chat', label: 'Чат' },
] as const

function MobileWorkspaceNavigation({ pathname }: { pathname: string }) {
  return (
    <nav className="sr-mobile-dock md:hidden" aria-label="Быстрая навигация">
      {mobileDestinations.map(({ to, label }) => (
        <Link key={to} to={to} activeOptions={{ exact: true }} aria-current={pathname === to ? 'page' : undefined} className="sr-dock-link">
          <HugeiconsIcon icon={iconsByPath[to]} size={21} strokeWidth={1.8} aria-hidden />
          <Typography as="span" variant="caption" className="sr-dock-label">{label}</Typography>
        </Link>
      ))}
      <Link to="/app/assistant" search={(previous) => ({ newReview: Math.max(Date.now(), (previous.newReview ?? 0) + 1) })} className="sr-dock-link sr-dock-new" aria-label="Начать новую проверку">
        <HugeiconsIcon icon={Add01Icon} size={23} strokeWidth={2} aria-hidden />
        <Typography as="span" variant="caption" className="sr-dock-label">Новая</Typography>
      </Link>
    </nav>
  )
}

function getSidebarDefaultOpen() {
  const persistedState = document.cookie
    .split('; ')
    .find((entry) => entry.startsWith('sidebar_state='))
    ?.slice('sidebar_state='.length)

  return persistedState !== 'false'
}

export function WorkspaceShell({
  children,
  onLogout,
  user,
  headerAction,
}: PropsWithChildren<{
  onLogout: () => Promise<void>
  user: UserDto
  headerAction?: ReactNode
}>) {
  const pathname = useLocation({ select: (location) => location.pathname })
  const navigationItems = navigationItemsForRole(user.role)
  const activeItem = navigationItems.find((item) => item.to === pathname)
  const homePath = homePathForRole(user.role)
  const settingsPath = user.role === 'admin' ? '/admin/settings' : '/app/settings'
  const items: ReadonlyArray<DashboardNavigationItem> = navigationItems.map((item) => ({
    ...item,
    icon: iconsByPath[item.to],
    isActive: item.to === pathname,
  }))

  return (
    <SidebarProvider className="sr-shell" defaultOpen={getSidebarDefaultOpen()} style={{ '--sidebar-width': '14rem' } as CSSProperties}>
      <AppSidebar
        accountPath={user.role === 'user' ? '/app/profile' : undefined}
        homePath={homePath}
        items={items}
        onLogout={onLogout}
        settingsPath={settingsPath}
        user={user}
        workspaceLabel={user.role === 'admin' ? 'Admin workspace' : 'FINTECH / BCC HUB × MNU'}
      />
      <SidebarInset className="min-w-0">
        <SiteHeader
          title={activeItem?.label ?? (user.role === 'admin' ? 'Dashboard' : 'Home')}
          action={headerAction}
        />
        <div className="flex min-w-0 flex-1 flex-col">{children}</div>
      </SidebarInset>
      {user.role === 'user' && <MobileWorkspaceNavigation pathname={pathname} />}
    </SidebarProvider>
  )
}
