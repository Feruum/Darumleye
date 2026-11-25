import { forwardRef } from 'react'
import { ButtonLink as ArcButtonLink, type ButtonLinkProps } from '@/components/arc/button/button'

export { Button } from '@/components/arc/button/button'
export type { ButtonProps, ButtonLinkProps } from '@/components/arc/button/button'

// A real link stays in the keyboard order in both SSR and reduced-motion hydration.
// Motion otherwise adds its own tabIndex only when the tap animation is enabled.
export const ButtonLink = forwardRef<HTMLAnchorElement, ButtonLinkProps>(function ButtonLink({ tabIndex = 0, ...props }, ref) {
  return <ArcButtonLink {...props} ref={ref} tabIndex={tabIndex} />
})
