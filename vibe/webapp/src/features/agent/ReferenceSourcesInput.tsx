import { AGENT_REFERENCE_MAX_FILES, agentReferenceCategorySchema } from '@web-app-demo/contracts'
import { useId } from 'react'

import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { createReferenceDraft, referenceCategoryLabels, referenceFileError, referencePresets, type ReferenceDraft, type ReferenceMetadataDraft } from './reference-sources'

type Props = {
  references: ReferenceDraft[]
  onChange: (references: ReferenceDraft[]) => void
  onFileError: (message: string | undefined) => void
  disabled: boolean
}

export function ReferenceSourcesInput({ references, onChange, onFileError, disabled }: Props) {
  const inputId = useId()
  function addFiles(files: File[]) {
    const error = referenceFileError([...references.map(({ file }) => file), ...files])
    onFileError(error)
    if (!error) onChange([...references, ...files.map(createReferenceDraft)])
  }

  return <details className="sr-reference-input">
    <Typography as="summary" variant="bodySmMedium">Регламенты и стандарты {references.length > 0 ? `· ${references.length}` : `· необязательно`}</Typography>
    <div className="sr-reference-input-content">
      <details className="sr-reference-catalog">
        <Typography as="summary" variant="caption">Справочник источников</Typography>
        <ul>{referencePresets.map((preset) => <li key={preset.id}>
          {preset.metadata.source_url ? <Typography as="a" variant="bodyXs" href={preset.metadata.source_url} target="_blank" rel="noopener noreferrer">{preset.label}</Typography> : <Typography as="span" variant="bodyXs">{preset.label}</Typography>}
        </li>)}</ul>
      </details>
      <input id={inputId} aria-label="Файлы регламентов и стандартов" type="file" className="sr-only" multiple accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.jpg,.jpeg,.png" disabled={disabled || references.length >= AGENT_REFERENCE_MAX_FILES} onChange={(event) => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ''; if (files.length) addFiles(files) }} />
      <label htmlFor={inputId} className="sr-reference-add" data-disabled={disabled || references.length >= AGENT_REFERENCE_MAX_FILES}><Typography as="span" variant="caption">Добавить документы (PDF, DOCX, XLSX, CSV, TXT, MD, JPG, PNG)</Typography></label>
      <Typography as="p" variant="caption">Фрагменты сохраняются в вашем личном индексе.</Typography>
      <Typography as="p" variant="caption">До 8 файлов · 5 МиБ, 60 страниц и 24 000 знаков на файл · 10 МиБ / 48 000 знаков всего.</Typography>
      {references.map((reference, index) => <ReferenceSourceEditor key={reference.id} reference={reference} index={index} disabled={disabled} onChange={(metadata) => onChange(references.map((item) => item.id === reference.id ? { ...item, metadata } : item))} onRemove={() => onChange(references.filter((item) => item.id !== reference.id))} />)}
    </div>
  </details>
}

function ReferenceSourceEditor({ reference, index, onChange, onRemove, disabled }: { reference: ReferenceDraft; index: number; onChange: (metadata: ReferenceMetadataDraft) => void; onRemove: () => void; disabled: boolean }) {
  const { metadata, file } = reference
  function update<K extends keyof ReferenceMetadataDraft>(key: K, value: ReferenceMetadataDraft[K]) {
    onChange({ ...metadata, [key]: value })
  }

  return <div className="sr-reference-editor">
    <div className="sr-reference-editor-header"><div><Typography as="strong" variant="bodySmMedium">Источник {index + 1}</Typography><Typography as="p" variant="caption">{file.name} · {Math.max(1, Math.round(file.size / 1024))} КБ</Typography></div><Button type="button" variant="ghost" size="xs" disabled={disabled} aria-label={`Удалить источник ${index + 1}`} onClick={onRemove}>Удалить</Button></div>
    <details open>
      <Typography as="summary" variant="caption">Метаданные источника</Typography>
      <fieldset disabled={disabled} className="sr-reference-fields">
        <label><Typography as="span" variant="caption">Заполнить из справочника</Typography><select value="" onChange={(e) => { const preset = referencePresets.find(({id}) => id === e.target.value); if (preset) onChange({ ...preset.metadata }) }}><option value="">Выберите при необходимости</option>{referencePresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}</select></label>
        <label><Typography as="span" variant="caption">Категория</Typography><select value={metadata.category} onChange={(e) => { const selection = agentReferenceCategorySchema.safeParse(e.target.value); if (selection.success) update('category', selection.data); else update('category', '') }}><option value="">Выберите категорию</option>{Object.entries(referenceCategoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><Typography as="span" variant="caption">Название</Typography><input value={metadata.title} maxLength={200} onChange={(e) => update('title', e.target.value)} /></label>
        <label><Typography as="span" variant="caption">Орган / владелец</Typography><input value={metadata.issuer} maxLength={200} placeholder="Не указано — если неизвестно" onChange={(e) => update('issuer', e.target.value)} /></label>
        <label><Typography as="span" variant="caption">Версия / дата редакции</Typography><input value={metadata.version} maxLength={200} placeholder="Укажите редакцию файла" onChange={(e) => update('version', e.target.value)} /></label>
        <label><Typography as="span" variant="caption">Область действия</Typography><textarea value={metadata.scope} maxLength={500} rows={2} placeholder="Какие процессы регулирует" onChange={(e) => update('scope', e.target.value)} /></label>
        <label><Typography as="span" variant="caption">Ссылка на источник (для справки)</Typography><input type="text" inputMode="url" value={metadata.source_url ?? ''} maxLength={2000} placeholder="https://…" onChange={(e) => update('source_url', e.target.value || null)} /></label>
      </fieldset>
    </details>
  </div>
}
