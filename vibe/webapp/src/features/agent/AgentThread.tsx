import { useMemo, useState } from 'react'
import {
  ActionBarPrimitive, AssistantRuntimeProvider, ComposerPrimitive, MessagePrimitive,
  ThreadPrimitive, useAuiState, useLocalRuntime, type TextMessagePartProps,
} from '@assistant-ui/react'
import {
  ArrowDown01Icon, ArrowRight01Icon, ArrowUp01Icon,
  Copy01Icon, File01Icon, StopIcon, Tick02Icon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { ArcBadge } from '@/components/arc'
import { Alert, AlertDescription, AlertTitle } from '@/components/interface/alert'
import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { GlassMascot } from '@web-app-demo/mascot'
import type { AuthenticatedTransport } from '@/platform/api'
import { createAgentModelAdapter, type AgentSource, type RagMetadata, type ToolStatus } from './runtime'
import './agent-chat.css'

const suggestions = [
  { label: 'Итоги проверки', prompt: 'Кратко объясни результаты проверки этого ТЗ.' },
  { label: 'Что исправить первым?', prompt: 'Какие замечания нужно исправить в первую очередь и почему?' },
  { label: 'Ключевые требования', prompt: 'Какие ключевые требования перечислены в исходном ТЗ?' },
]

export function AgentThread({ transport, reviewId, reviewFilename }: {
  transport: AuthenticatedTransport; reviewId?: string; reviewFilename?: string
}) {
  const [threadId] = useState(() => reviewId ? `review:${reviewId}:${crypto.randomUUID()}` : crypto.randomUUID())
  const adapter = useMemo(() => createAgentModelAdapter(transport, { threadId, ...(reviewId ? { moduleId: 'review_chat', reviewId } : {}) }), [transport, threadId, reviewId])
  const runtime = useLocalRuntime(adapter)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="agent-chat grid min-w-0 gap-4" data-testid="agent-thread">
        <ThreadPrimitive.Viewport className="agent-chat-viewport relative min-h-0 min-w-0 overflow-y-auto overscroll-contain" aria-label="Диалог" role="log" aria-live="polite">
          <div className="agent-message-column mx-auto grid w-full min-w-0 gap-5 px-2 py-2 sm:px-3">
            <ThreadPrimitive.Empty>
              <div className="agent-welcome grid justify-items-center gap-4 px-1 py-6 text-center sm:py-8">
                <span className="agent-welcome-avatar"><GlassMascot size={96} followPointer ambient spinOnHover /></span>
                <div className="grid gap-2">
                  <Typography as="h2" variant="h3">{reviewId ? 'Обсудим результаты проверки?' : 'Сначала проверьте ТЗ'}</Typography>
                  <Typography className="max-w-[440px]" tone="muted" variant="bodySm">
                    {reviewFilename ? `Выбрано: ${reviewFilename}. Ассистент использует исходный текст и отчёт.` : 'После завершения проверки документ и отчёт станут доступны в этом чате.'}
                  </Typography>
                </div>
                {reviewId && <div className="grid w-full gap-2 text-left sm:grid-cols-3" aria-label="Примеры вопросов">
                  {suggestions.map(({ label, prompt }) => <ThreadPrimitive.Suggestion key={label} prompt={prompt} send={false} asChild>
                    <button type="button" className="agent-suggestion" data-testid="agent-suggestion"><Typography as="span" variant="bodySmMedium">{label}</Typography><HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden /></button>
                  </ThreadPrimitive.Suggestion>)}
                </div>}
              </div>
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          </div>
          <div className="pointer-events-none sticky bottom-2 flex justify-center">
            <ThreadPrimitive.ScrollToBottom asChild>
              <Button type="button" variant="outline" size="icon" className="agent-scroll-to-bottom pointer-events-auto rounded-full shadow-sm" aria-label="К последнему сообщению" title="К последнему сообщению"><HugeiconsIcon icon={ArrowDown01Icon} size={18} aria-hidden /></Button>
            </ThreadPrimitive.ScrollToBottom>
          </div>
        </ThreadPrimitive.Viewport>
        <Composer disabled={!reviewId} />
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

function Composer({ disabled }: { disabled: boolean }) {
  const running = useAuiState((state) => state.thread.isRunning)
  return (
    <div className="agent-composer-column mx-auto grid w-full min-w-0 gap-2">
      <ComposerPrimitive.Root className="agent-composer grid gap-2" data-testid="agent-composer">
        <ComposerPrimitive.Input className="agent-composer-input" aria-label="Вопрос" placeholder={disabled ? 'Выберите завершённую проверку' : 'Спросите о выбранном ТЗ или отчёте…'} disabled={running || disabled} submitMode="ctrlEnter" addAttachmentOnPaste={false} minRows={2} maxRows={6} maxLength={32000} />
        <div className="flex items-center justify-end gap-2">
          {running ? (
            <ComposerPrimitive.Cancel asChild><Button type="button" variant="outline" size="sm" data-testid="agent-stop"><HugeiconsIcon icon={StopIcon} size={16} aria-hidden />Остановить</Button></ComposerPrimitive.Cancel>
          ) : (
            <ComposerPrimitive.Send disabled={disabled} asChild><Button type="submit" size="sm" className="sr-primary" data-testid="agent-send">Отправить<HugeiconsIcon icon={ArrowUp01Icon} size={17} aria-hidden /></Button></ComposerPrimitive.Send>
          )}
        </div>
      </ComposerPrimitive.Root>
      <Typography variant="caption" tone="muted" align="center" className="hidden sm:block">Ctrl / ⌘ + Enter — отправить вопрос</Typography>
    </div>
  )
}

function PlainText({ text }: TextMessagePartProps) {
  return <Typography as="p" className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]" variant="body">{text}</Typography>
}

function UserMessage() {
  return <MessagePrimitive.Root className="ml-auto grid w-fit max-w-[90%] min-w-0 gap-1.5 sm:max-w-[80%]" data-testid="agent-user-message">
    <Typography variant="caption" tone="muted" align="end">Вы</Typography>
    <div className="agent-user-bubble"><MessagePrimitive.Parts components={{ Text: PlainText }} /></div>
  </MessagePrimitive.Root>
}

export function AgentResponseIdentity({ running, hasText, failed = false, followPointer = false }: { running: boolean; hasText: boolean; failed?: boolean; followPointer?: boolean }) {
  const state = running ? hasText ? 'streaming' : 'waiting' : 'idle'
  return <div className="flex min-w-0 items-center gap-3" data-testid="agent-response-identity" data-response-state={state}>
    <span className="agent-response-avatar" data-active={running}>
      <GlassMascot size={56} state={failed ? 'error' : running ? hasText ? 'speaking' : 'thinking' : hasText ? 'success' : 'idle'} followPointer={followPointer} ambient={running && followPointer} />
    </span>
    <div className="grid gap-0.5">
      <Typography variant="bodySmMedium">Ассистент</Typography>
      {running && <Typography as="span" variant="caption" tone="muted" role="status" data-testid="agent-thinking">{hasText ? 'Отвечаю…' : 'Готовлю ответ…'}</Typography>}
    </div>
  </div>
}

function AssistantMessage() {
  const message = useAuiState((state) => state.message)
  const rag = message.metadata.custom.rag as RagMetadata | undefined
  const running = message.status?.type === 'running'
  const hasText = message.content.some((part) => part.type === 'text' && Boolean(part.text))
  const failure = message.status?.type === 'incomplete' && message.status.reason === 'error' ? message.status.error : null
  const rawFailureText = typeof failure === 'string' ? failure
    : failure && typeof failure === 'object' && 'message' in failure && typeof failure.message === 'string' ? failure.message
    : 'Не удалось получить ответ.'
  const failureText = /network error|failed to fetch|response was interrupted/i.test(rawFailureText)
    ? 'Соединение прервалось до завершения ответа. Отправьте вопрос ещё раз.'
    : rawFailureText === 'The agent could not finish this request.'
      ? 'Ассистент не смог завершить ответ. Повторите вопрос.' : rawFailureText
  return <MessagePrimitive.Root className="grid min-w-0 gap-3" data-testid="agent-assistant-message">
    <AgentResponseIdentity running={running} hasText={hasText} failed={Boolean(failure)} followPointer={message.isLast} />
    <div className="grid min-w-0 gap-3 sm:pl-[68px]">
      {hasText && <MessagePrimitive.Parts components={{ Text: PlainText }} />}
      {failure && <Alert variant="destructive" data-testid="agent-error"><AlertTitle>Ошибка</AlertTitle><AlertDescription>{failureText}</AlertDescription></Alert>}
      {rag && rag.tools.some((tool) => tool.status === 'error') && <details className="agent-tool-details" data-testid="agent-tools">
        <Typography as="summary" variant="caption">Действия ассистента · {rag.tools.length}</Typography>
        <div className="grid gap-2 pt-2">{rag.tools.map((tool) => <div key={tool.id} className="grid gap-1" data-status={tool.status}>
          <div className="flex flex-wrap items-center gap-2"><Typography variant="bodySm">{toolLabel(tool)}</Typography><ArcBadge tone={tool.status === 'error' ? 'danger' : tool.status === 'ok' ? 'success' : 'warning'} size="sm">{tool.status === 'ok' ? 'Готово' : tool.status === 'error' ? 'Ошибка' : 'В процессе'}</ArcBadge></div>
          {tool.error && <Typography variant="caption" tone="destructive">{tool.error}</Typography>}
        </div>)}</div>
      </details>}
      {!running && hasText && !failure && <AgentSources sources={rag?.sources ?? []} />}
      {hasText && !running && <ActionBarPrimitive.Root className="flex items-center gap-1" hideWhenRunning>
        <ActionBarPrimitive.Copy asChild><Button type="button" variant="ghost" size="sm" className="group/copy" aria-label="Копировать ответ" title="Копировать ответ">
          <HugeiconsIcon icon={Copy01Icon} size={16} aria-hidden className="group-data-[copied]/copy:hidden" />
          <HugeiconsIcon icon={Tick02Icon} size={16} aria-hidden className="hidden group-data-[copied]/copy:block" />
          <Typography as="span" variant="controlXs" className="group-data-[copied]/copy:hidden">Копировать</Typography><Typography as="span" variant="controlXs" className="hidden group-data-[copied]/copy:inline">Скопировано</Typography>
        </Button></ActionBarPrimitive.Copy>
      </ActionBarPrimitive.Root>}
    </div>
  </MessagePrimitive.Root>
}

export function AgentSources({ sources }: { sources: readonly AgentSource[] }) {
  if (!sources.length) return null
  const groups = new Map<string, AgentSource[]>()
  for (const source of sources) groups.set(source.filename, [...(groups.get(source.filename) ?? []), source])
  return <details className="agent-source-disclosure" data-testid="agent-sources">
    <summary>
      <HugeiconsIcon icon={File01Icon} size={15} aria-hidden />
      <Typography as="span" variant="bodySmMedium">Источники</Typography>
      <Typography as="span" variant="caption" tone="muted">{groups.size}</Typography>
      <HugeiconsIcon icon={ArrowDown01Icon} size={15} className="agent-source-chevron" aria-hidden />
    </summary>
    <div className="grid gap-2 pt-2">{[...groups].map(([filename, entries]) => <div className="agent-source" key={filename}>
      <HugeiconsIcon icon={File01Icon} size={17} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="grid min-w-0 gap-2 [overflow-wrap:anywhere]">
        <Typography variant="bodySmMedium">{filename}</Typography>
        {entries.map((source, index) => <div key={`${source.page ?? ''}:${index}`} className="grid gap-0.5">
          {source.page != null && <Typography variant="caption" tone="muted">Стр. {source.page}</Typography>}
          {source.snippet && <Typography className="whitespace-pre-wrap" tone="muted" variant="caption">{source.snippet}</Typography>}
        </div>)}
      </div>
    </div>)}</div>
  </details>
}

function toolLabel(tool: ToolStatus) {
  if (tool.name === 'search_user_review') return 'Поиск по проверке и исходному ТЗ'
  if (tool.name === 'search_user_documents' || tool.name === 'search_knowledge') return 'Поиск по документам'
  if (tool.name === 'search_user_memory') return 'Поиск по заметкам'
  return 'Обработка запроса'
}
