import { Link } from '@tanstack/react-router'
import { GlassMascot } from '@web-app-demo/mascot'
import type { ReactNode } from 'react'

import { Typography } from '@/components/typography'

import './auth-page-shell.css'

export function AuthPageShell({ children }: { children: ReactNode }) {
  return (
    <main className="sr-auth grid min-h-svh lg:grid-cols-2">
      <section className="sr-auth-form-side flex flex-col gap-4 p-6 md:p-10">
        <div className="flex justify-center gap-2 md:justify-start">
          <Link className="flex items-center gap-2" search={{ returnTo: undefined }} to="/login">
            <span className="flex size-9 items-center justify-center"><GlassMascot size={36} /></span>
            <Typography as="span" variant="h5">
              spec review
            </Typography>
          </Link>
        </div>
        <div className="flex flex-1 items-center justify-center py-8">
          <div className="sr-auth-form w-full max-w-sm">{children}</div>
        </div>
      </section>
      <section lang="ru" className="auth-mascot-panel relative hidden flex-col overflow-hidden p-8 text-white lg:flex xl:p-12">
        <Typography variant="shortcut" className="text-white/75">BCC HUB × MNU · FINTECH HACKATHON</Typography>
        <div className="auth-mascot-content">
          <div className="auth-mascot-stage" aria-hidden="true">
            <div className="auth-mascot-halo" />
            <div className="auth-mascot-orbit" />
            <div className="auth-mascot-orbit auth-mascot-orbit--inner" />
            <div className="auth-mascot-shadow" />
            <div className="auth-mascot-character">
              <GlassMascot size={440} ambient spinOnHover />
            </div>
          </div>
          <div className="auth-mascot-copy space-y-4">
            <Typography variant="h1" className="auth-mascot-heading">Точные требования.<br />Уверенная разработка.</Typography>
            <Typography variant="lead" className="mx-auto max-w-sm text-white/75">Проверка технических заданий с привязкой к исходному документу.</Typography>
          </div>
        </div>
        <Typography variant="bodySm" className="text-white/75">Spec Review · Проект хакатона</Typography>
      </section>
    </main>
  )
}
