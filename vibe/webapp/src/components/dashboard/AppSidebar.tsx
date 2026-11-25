import type { UserDto } from '@web-app-demo/contracts'
import { GlassMascot } from '@web-app-demo/mascot'

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from '@/components/ui/sidebar'
import { Typography } from '@/components/typography'
import type { WorkspaceRoutePath } from '@/features/navigation'
import { DashboardLink } from './DashboardLink'
import { NavMain, type DashboardNavigationItem } from './NavMain'
import { NavUser } from './NavUser'

export function AppSidebar({
  accountPath,
  homePath,
  items,
  onLogout,
  settingsPath,
  user,
  workspaceLabel,
}: {
  accountPath?: WorkspaceRoutePath
  homePath: WorkspaceRoutePath
  items: ReadonlyArray<DashboardNavigationItem>
  onLogout: () => Promise<void>
  settingsPath: WorkspaceRoutePath
  user: UserDto
  workspaceLabel: string
}) {
  return (
    <Sidebar collapsible="icon" variant="floating">
      <SidebarHeader className="px-3 pt-5 pb-6 group-data-[collapsible=icon]:px-2">
        <SidebarMenu className="gap-3">
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip="Spec Review" className="sr-brand-link px-1 py-0">
              <DashboardLink to={homePath} aria-label={`Spec Review — ${workspaceLabel}`}>
                <span aria-hidden className="sr-brand-mascot relative flex size-10 shrink-0 items-center justify-center overflow-hidden group-data-[collapsible=icon]:size-8">
                  <GlassMascot size={40} />
                </span>
                <span className="grid min-w-0 gap-1 group-data-[collapsible=icon]:hidden">
                  <Typography as="span" variant="h4" className="sr-brand-wordmark" wrap="nowrap">
                    <Typography as="strong" variant="emphasis">spec</Typography>{' '}<Typography as="span" variant="bodyXs">review</Typography>
                  </Typography>
                  {user.role === 'admin' ? (
                    <Typography as="span" variant="caption" tone="muted" truncate>{workspaceLabel}</Typography>
                  ) : (
                    <Typography as="span" variant="caption" className="sr-brand-partners">
                      <Typography as="strong" variant="emphasis">bcc</Typography>
                      <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
                        <path d="M8.4 3.6a1.8 1.8 0 0 1 3.2 0l6 10.6a1.8 1.8 0 0 1-1.6 2.7H4a1.8 1.8 0 0 1-1.6-2.7L8.4 3.6Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                        <path d="m7.5 12.1 2.5-4.4 2.5 4.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <Typography as="span" variant="caption">hub</Typography>
                      <Typography as="span" variant="caption" className="sr-brand-partner-divider">× MNU</Typography>
                    </Typography>
                  )}
                </span>
              </DashboardLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={items} showNewReview={user.role === 'user'} label={user.role === 'admin' ? 'Workspace' : 'Рабочая область'} />
      </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border py-3">
        <NavUser
          accountPath={accountPath}
          onLogout={onLogout}
          settingsPath={settingsPath}
          user={user}
        />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
