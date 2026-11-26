import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'

import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { ReviewSetup } from './ReviewSetup'
import { SpecReviewReport } from './SpecReviewReport'
import { createExampleFile, loadExampleReport } from './review-example'
import type { ReferenceDraft } from './reference-sources'
import { AGENT_DEFAULT_REVIEW_MODE } from '@web-app-demo/contracts'
import './spec-review.css'

function ReviewDemo() {
  const [exampleOpen, setExampleOpen] = useState(false)
  const [exampleFile] = useState(createExampleFile)
  const [exampleReport] = useState(loadExampleReport)
  const [file, setFile] = useState<File>()
  const [references, setReferences] = useState<ReferenceDraft[]>([])
  const [referenceError, setReferenceError] = useState<string>()
  const [reviewMode, setReviewMode] = useState(AGENT_DEFAULT_REVIEW_MODE)

  return <main className="sr-page" lang="ru">
    <section className="sr-workspace">
      <header className="sr-heading">
        <Typography as="h1" variant="h1">{exampleOpen ? 'Учебный отчёт' : 'Проверка ТЗ'}</Typography>
        {exampleOpen && <Button variant="outline" type="button" onClick={() => setExampleOpen(false)}>К загрузке документа</Button>}
      </header>
      {exampleOpen ? <SpecReviewReport report={exampleReport} sourceFile={exampleFile} /> : <ReviewSetup
        file={file} references={references} reviewMode={reviewMode} onReviewModeChange={setReviewMode}
        cancelled={false} loadingExample={false} referenceError={referenceError} onFileSelect={setFile}
        onReferencesChange={setReferences} onReferenceError={setReferenceError}
        onViewExample={() => setExampleOpen(true)} onSubmit={event => { event.preventDefault(); setExampleOpen(true) }}
      />}
      <Typography as="p" variant="caption" tone="muted">Демо интерфейса. Проверка открывает учебный отчёт и не отправляет файлы.</Typography>
    </section>
  </main>
}

const meta = { title: 'Workflows/Spec Review', component: ReviewDemo } satisfies Meta<typeof ReviewDemo>
export default meta

export const InteractiveDemo: StoryObj<typeof meta> = {}
