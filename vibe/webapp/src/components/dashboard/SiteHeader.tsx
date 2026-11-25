import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Typography } from '@/components/typography'
import type { ReactNode } from 'react'

export function SiteHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <header className="sr-shell-header sticky top-0 z-10 flex h-14 shrink-0 items-center">
      <div className="sr-shell-header-content flex w-full items-center gap-1 px-3 lg:gap-2 lg:px-5">
        <SidebarTrigger className="-ml-1" />
        <Separator
          className="mx-2 data-[orientation=vertical]:h-4 data-[orientation=vertical]:self-center"
          orientation="vertical"
        />
        <Typography as="span" variant="h6">
          {title}
        </Typography>
        {action && <Typography as="div" variant="bodySm" className="ml-auto shrink-0">{action}</Typography>}
      </div>
    </header>
  )
}
