import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { AgentReferenceSourceSnapshot } from '@web-app-demo/contracts'
import { Typography } from '@/components/typography'
import { Button } from '@/components/interface/button'
import { useAuth } from '@/features/auth'
import { ApiRequestError } from '@/platform/api'
import { getCompletedReferenceSource, restoreCompletedReferenceSource } from './api'
import { agentQueryKeys } from './queries'
import { ReviewSourceDocument } from './ReviewSourceDocument'
import { matchOriginalReferenceFiles, type OriginalReferenceSource, type ReferenceUpload } from './reference-sources'
import { verifiedReferenceUnits } from './review-source'
import type { ReviewEvidence } from './review-source'

export function ReferenceSourceReader({ reviewId, source, evidence, jump, references = [], focusMode, onToggleFocus, mobileVisible }: {
  reviewId?: string; source: OriginalReferenceSource; evidence: ReviewEvidence; jump: number
  references?: readonly ReferenceUpload[]; focusMode: boolean; onToggleFocus: () => void; mobileVisible: boolean
}) {
  const { transport, user } = useAuth()
  const [originalFile, setOriginalFile] = useState<File>()
  const [recovered, setRecovered] = useState<AgentReferenceSourceSnapshot>()
  const [restoreError, setRestoreError] = useState<string>()
  const [restoring, setRestoring] = useState(false)
  const query = useQuery({
    queryKey: [...agentQueryKeys.completedReview(user?.id, reviewId ?? ''), 'reference', source.id, source.sha256],
    queryFn: ({ signal }) => getCompletedReferenceSource(transport, reviewId!, source.id, signal),
    enabled: Boolean(user?.id && reviewId), staleTime: Infinity, retry: false,
  })
  useEffect(() => {
    let cancelled = false
    if (references.length) void matchOriginalReferenceFiles([source], references.map(item => item.file)).then(result => {
      if (!cancelled && result.matches[0]) setOriginalFile(previous => previous ?? result.matches[0]!.file)
    })
    return () => { cancelled = true }
  }, [references, source])
  const snapshot = recovered ?? query.data
  const sourceUnits = snapshot ? verifiedReferenceUnits(snapshot, source, evidence) : undefined
  const loading = Boolean(reviewId && query.isPending && query.isFetching)
  const missing = query.error instanceof ApiRequestError && query.error.status === 404

  async function restore(file: File) {
    setRestoreError(undefined)
    setRestoring(true)
    try {
      const matched = await matchOriginalReferenceFiles([source], [file])
      if (!matched.matches[0]) {
        setRestoreError(matched.error ?? 'Файл не совпадает с SHA-256 исходного регламента.')
        return
      }
      setOriginalFile(matched.matches[0].file)
      if (!sourceUnits) {
        if (!reviewId) {
          setRestoreError('Нет архивного идентификатора для восстановления извлечённого текста.')
          return
        }
        const restored = await restoreCompletedReferenceSource(transport, reviewId, source.id, matched.matches[0].file)
        if (!verifiedReferenceUnits(restored, source, evidence)) {
          setRestoreError('Не удалось подтвердить место цитаты в восстановленном тексте.')
          return
        }
        setRecovered(restored)
      }
    } catch {
      setRestoreError('Не удалось подтвердить место цитаты в исходном файле. Попробуйте ещё раз.')
    } finally {
      setRestoring(false)
    }
  }

  // The reader displays the verified complete extracted original, never a
  // synthetic reconstruction from quotations. For matching local originals
  // PDF and photo views remain available through the existing document reader.
  return <ReviewSourceDocument file={sourceUnits ? originalFile : undefined} sourceUnits={sourceUnits} sourceLoading={loading} ocrPages={source.ocr_pages}
    unreadableUnits={source.unreadable_units} filename={source.filename} source={source.source} evidence={evidence} jump={jump}
    mobileVisible={mobileVisible} focusOnJump annotations={[]} onAnnotationSelect={() => {}} focusMode={focusMode} onToggleFocus={onToggleFocus}
    unavailableMessage={snapshot && !sourceUnits ? 'Место цитаты не подтверждено сохранённым текстом регламента.' : missing ? 'В этом архиве нет полного текста регламента. Доступна только сохранённая цитата.' : 'Не удалось открыть полный текст регламента.'}
    renderFallback={() => <div className="grid gap-2">
      {!missing && query.isError && <Button size="sm" variant="outline" onClick={() => void query.refetch()}>Повторить загрузку</Button>}
      {originalFile && !sourceUnits && <Button size="sm" variant="outline" disabled={restoring} onClick={() => void restore(originalFile)}>Восстановить из исходного файла</Button>}
      <label className="grid gap-1"><Typography as="span" variant="caption">Приложить исходный файл (с проверкой SHA-256)</Typography><input type="file" disabled={restoring} accept=".pdf,.txt,.md,.docx,.xlsx,.csv,.jpg,.jpeg,.png" onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void restore(file); event.currentTarget.value = '' }} /></label>
      {restoring && <Typography as="p" variant="caption" role="status">Проверяем оригинал…</Typography>}
      {restoreError && <Typography as="p" variant="caption" tone="destructive" role="alert">{restoreError}</Typography>}
    </div>} />
}
