import { Add01Icon } from '@hugeicons/core-free-icons'
import { Link } from '@tanstack/react-router'
import type { IconSvgElement } from '@hugeicons/react'
import { HugeiconsIcon } from '@hugeicons/react'

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { Typography } from '@/components/typography'
import type { WorkspaceRoutePath } from '@/features/navigation'
import { DashboardLink } from './DashboardLink'

const iconMotionByPath = {
  '/app': 'home',
  '/app/assistant': 'tiles',
  '/app/chat': 'chat',
  '/app/profile': 'person',
  '/app/settings': 'gear',
  '/admin': 'tiles',
  '/admin/users': 'person',
  '/admin/settings': 'gear',
} satisfies Record<WorkspaceRoutePath, string>

export type DashboardNavigationItem = {
  icon: IconSvgElement
  isActive: boolean
  label: string
  to: WorkspaceRoutePath
}

export function NavMain({
  items,
  label = 'Workspace',
  showNewReview = false,
}: {
  items: ReadonlyArray<DashboardNavigationItem>
  label?: string
  showNewReview?: boolean
}) {
  const { isMobile, state, setOpenMobile } = useSidebar()
  return (
    <nav aria-label="Primary navigation">
      <SidebarGroup>
        <SidebarGroupLabel>{label}</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            {showNewReview && <SidebarMenuItem className="mb-2">
              <SidebarMenuButton asChild tooltip="Новая проверка" className="sr-new-review h-11 justify-center rounded-lg">
                <Link to="/app/assistant" search={(previous) => ({ newReview: Math.max(Date.now(), (previous.newReview ?? 0) + 1) })} aria-label="Новая проверка" data-testid="workspace-new-review" onClick={() => setOpenMobile(false)}>
                  <HugeiconsIcon icon={Add01Icon} size={19} strokeWidth={2} className="sr-nav-icon" data-nav-motion="add" aria-hidden />
                  <Typography as="span" variant="control" className="group-data-[collapsible=icon]:hidden">Новая проверка</Typography>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>}
            {items.map((item) => (
              <SidebarMenuItem key={item.to}>
                <SidebarMenuButton
                  asChild
                  isActive={item.isActive}
                  tooltip={!isMobile && state === 'collapsed' ? item.label : undefined}
                >
                  <DashboardLink to={item.to} data-testid={`workspace-nav-${item.to}`}>
                    <HugeiconsIcon icon={item.icon} strokeWidth={2} className="sr-nav-icon" data-nav-motion={iconMotionByPath[item.to]} aria-hidden />
                    <Typography asChild variant="control">
                      <span>{item.label}</span>
                    </Typography>
                  </DashboardLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    </nav>
  )
}
