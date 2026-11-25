import { CheckmarkCircle02Icon, InformationCircleIcon, Loading03Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import type { AgentReviewJob, AgentSpecReview, MeResponse } from '@web-app-demo/contracts'
import { useCallback, useEffect, useRef, useState, type PropsWithChildren } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/interface/button'
import { Toaster } from '@/components/ui/sonner'
import { Typography } from '@/components/typography'
import { authQueryKeys, useAuth } from '@/features/auth'
import { ApiRequestError } from '@/platform/api'
import { cancelReviewJobWhenAvailable, startReviewJob, waitForReviewJob } from './api'
import { agentQueryKeys } from './queries'
import { recordReview } from './review-overview'
import { clearPendingReviewCancellation, clearPendingReviewJob, createReviewRun, readPendingReviewCancellation, readPendingReviewJob, rememberPendingReviewCancellation, rememberPendingReviewJob } from './review-run'
import { ReviewRunContext, useReviewRun } from './review-run-context'

export function ReviewRunProvider({ ownerId, children }: PropsWithChildren<{ ownerId: string }>) {
  const { transport } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [run] = useState(createReviewRun)
  const [initialJob] = useState(() => readPendingReviewJob(ownerId))
  const notification = useRef<string | number | undefined>(undefined)
  const cancellation = useRef<{ id: string; controller: AbortController } | undefined>(undefined)
  const dismissNotification = useCallback(() => {
    if (notification.current !== undefined) toast.dismiss(notification.current)
    notification.current = undefined
  }, [])

  useEffect(() => () => {
    run.dispose()
    cancellation.current?.controller.abort()
    cancellation.current = undefined
    dismissNotification()
  }, [run, dismissNotification])

  const openReview = useCallback(() => { void navigate({ to: '/app/assistant', search: {} }) }, [navigate])
  const isCurrentAccount = useCallback(() => queryClient.getQueryData<MeResponse>(authQueryKeys.me())?.user.id === ownerId, [queryClient, ownerId])

  const requestCancellation = useCallback((id: string) => {
    rememberPendingReviewCancellation(ownerId, id)
    if (cancellation.current?.id === id) return
    cancellation.current?.controller.abort()
    const controller = new AbortController()
    cancellation.current = { id, controller }
    void cancelReviewJobWhenAvailable(transport, id, controller.signal).then(result => {
      if (controller.signal.aborted) return
      clearPendingReviewCancellation(ownerId, id)
      clearPendingReviewJob(ownerId, id)
      if (result?.status === 'completed' && isCurrentAccount()) void queryClient.invalidateQueries({ queryKey: agentQueryKeys.completedReviews(ownerId) })
    }).catch(() => {
      // Keep the owner-scoped cancellation intent for the next authenticated
      // session/reload if the request was interrupted or authorization expired.
    }).finally(() => {
      if (cancellation.current?.controller === controller) cancellation.current = undefined
    })
  }, [transport, ownerId, queryClient, isCurrentAccount])

  const monitor = useCallback(async (id: string, signal: AbortSignal, observe: (job: AgentReviewJob) => void, initial?: AgentReviewJob) => {
    try {
      const report = await waitForReviewJob(transport, id, signal, observe, initial)
      clearPendingReviewJob(ownerId, id)
      return report
    } catch (error) {
      // Definitive job failures/absence need a fresh upload; transient loss
      // of connection keeps the marker so a reload can reattach later.
      if (error instanceof ApiRequestError && error.status < 500) clearPendingReviewJob(ownerId, id)
      throw error
    }
  }, [transport, ownerId])

  const callbacksForJob = useCallback((id: string) => ({
    completed: (report: AgentSpecReview) => {
      if (!isCurrentAccount()) return
      recordReview(queryClient, ownerId, report)
      void queryClient.invalidateQueries({ queryKey: agentQueryKeys.completedReviews(ownerId) })
      notification.current = toast.success('Отчёт готов', { description: report.filename, duration: 12_000, action: { label: 'Открыть отчёт', onClick: openReview } })
    },
    failed: () => {
      if (!isCurrentAccount()) return
      const pending = readPendingReviewJob(ownerId) === id
      notification.current = toast.error(pending ? 'Связь с проверкой прервана' : 'Проверка не завершена', { description: pending ? 'Обновите страницу, чтобы восстановить связь с запуском.' : 'Откройте проверку, чтобы повторить попытку.', duration: 12_000, action: { label: 'Открыть', onClick: openReview } })
    },
    cancelled: () => {
      // Remember the intent until a responding server acknowledges it.
      requestCancellation(id)
    },
  }), [isCurrentAccount, queryClient, ownerId, openReview, requestCancellation])

  useEffect(() => {
    const cancelledId = readPendingReviewCancellation(ownerId)
    if (cancelledId) {
      requestCancellation(cancelledId)
      return
    }
    const id = readPendingReviewJob(ownerId)
    if (id) run.resume((signal, observe) => monitor(id, signal, observe), callbacksForJob(id))
  }, [run, ownerId, monitor, callbacksForJob, requestCancellation])

  function submit() {
    const current = run.getSnapshot()
    if (current.status === 'running') return false
    if (readPendingReviewCancellation(ownerId)) {
      toast.info('Ожидаем подтверждения отмены предыдущей проверки')
      return false
    }
    dismissNotification()
    const pending = readPendingReviewJob(ownerId)
    if (pending && current.status === 'failed') return run.resume((signal, observe) => monitor(pending, signal, observe), callbacksForJob(pending))
    if (!current.file) return false
    if (pending) clearPendingReviewJob(ownerId, pending)
    const id = crypto.randomUUID()
    return run.start(async ({ file, references, reviewMode }, signal, observe) => {
      rememberPendingReviewJob(ownerId, id)
      let initial: AgentReviewJob | undefined
      try {
        initial = await startReviewJob(transport, id, file, signal, references, reviewMode)
      } catch (error) {
        signal.throwIfAborted()
        if (error instanceof ApiRequestError && (error.status < 500 || error.code === 'LLM_NOT_CONFIGURED')) {
          clearPendingReviewJob(ownerId, id)
          throw error
        }
        // The server may have accepted the upload before the connection was
        // lost. Resolve that UUID before asking the user to upload again.
      }
      return monitor(id, signal, observe, initial)
    }, callbacksForJob(id))
  }

  const openNewReview = useCallback((intent: number) => {
    // A stale new-review URL can still be present when a tab reloads during
    // an accepted job. Resume takes precedence over that initial intent.
    if (initialJob && run.getSnapshot().status === 'idle' && readPendingReviewJob(ownerId) === initialJob) return
    if (run.openNewReview(intent)) {
      const pending = readPendingReviewJob(ownerId)
      if (pending && readPendingReviewCancellation(ownerId) !== pending) clearPendingReviewJob(ownerId, pending)
      dismissNotification()
    }
  }, [run, dismissNotification, initialJob, ownerId])

  return <ReviewRunContext.Provider value={{ run, submit, openNewReview }}>
    {children}
    <Toaster position="bottom-right" closeButton />
  </ReviewRunContext.Provider>
}

export function ReviewRunIndicator() {
  const { snapshot } = useReviewRun()
  if (snapshot.status === 'idle' || snapshot.status === 'cancelled') return null
  const running = snapshot.status === 'running'
  const complete = snapshot.status === 'completed'
  const label = running ? 'Проверка выполняется' : complete ? 'Отчёт готов' : 'Проверка не завершена'
  return <Button asChild variant="outline" size="sm" className="max-w-full shrink-0 gap-1.5 rounded-md">
    <Link to="/app/assistant" search={{}} aria-label={label} title={snapshot.file?.name ?? snapshot.filename} data-testid="review-run-indicator" data-review-status={snapshot.status}>
      <HugeiconsIcon icon={running ? Loading03Icon : complete ? CheckmarkCircle02Icon : InformationCircleIcon} className={running ? 'size-4 animate-spin text-violet-600 motion-reduce:animate-none dark:text-violet-300' : complete ? 'size-4 text-emerald-600 dark:text-emerald-400' : 'size-4 text-amber-600 dark:text-amber-400'} aria-hidden />
      <Typography as="span" variant="caption" className="hidden sm:inline">{label}</Typography>
      <Typography as="span" variant="caption" className="sm:hidden">{running ? 'Проверяем' : complete ? 'Готово' : 'Ошибка'}</Typography>
    </Link>
  </Button>
}
