import type { AgentEvidenceGraph, AgentEvidenceKind, AgentEvidenceNode, AgentEvidenceRole, AgentSpecReview } from '@web-app-demo/contracts'
import { Background, Controls, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'
import { Button } from '@/components/interface/button'
import { Typography } from '@/components/typography'
import { evidenceEdgeLabels, evidenceKindLabels, evidenceRoleLabels, findingAgreement, findingAgreementLabels, projectEvidenceGraph, type FindingAgreement } from './evidence-graph'
import { formatEvidenceLocation } from './review-report'
import type { ReviewEvidence } from './review-source'
import './evidence-graph.css'

const lanes: AgentEvidenceKind[] = ['reference', 'requirement', 'evidence', 'observation', 'counterexample', 'finding', 'question']
const severities = { critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий' } as const
const expertStatuses = { completed: 'Завершено', unavailable: 'Недоступно', skipped: 'Пропущено' }
const nodeColors: Record<AgentEvidenceKind, string> = {
  requirement: '#768ba4', evidence: '#168258', observation: '#755ad6', counterexample: '#b87b22', finding: '#c7515f', question: '#a17caa', reference: '#298d98',
}

function byId(a: { id: string }, b: { id: string }) { return a.id.localeCompare(b.id, 'en', { numeric: true }) }

export default function EvidenceGraphExplorer({ graph, report, onShowEvidence, onShowReference }: { graph: AgentEvidenceGraph; report: AgentSpecReview; onShowEvidence: (evidence: ReviewEvidence) => void; onShowReference: (sourceId: string, evidence: ReviewEvidence) => void }) {
  const [query, setQuery] = useState('')
  const [role, setRole] = useState<AgentEvidenceRole | ''>('')
  const [severity, setSeverity] = useState<NonNullable<AgentEvidenceNode['severity']> | ''>('')
  const [kind, setKind] = useState<AgentEvidenceKind | ''>('')
  const [agreement, setAgreement] = useState<FindingAgreement | ''>('')
  const agreements = useMemo(() => findingAgreement(graph), [graph])
  const [selected, setSelected] = useState<string | undefined>()
  const [focus, setFocus] = useState<string | undefined>()
  const [collapsed, setCollapsed] = useState<AgentEvidenceKind[]>(() => graph.nodes.length > 160 ? ['requirement', 'evidence', 'observation', 'counterexample', 'question', ...(graph.nodes.filter(node => node.kind === 'finding').length > 120 ? ['finding' as const] : [])] : [])
  const [listMode, setListMode] = useState(false)
  const byNodeId = useMemo(() => new Map(graph.nodes.map(node => [node.id, node])), [graph])
  const selectedNode = selected ? byNodeId.get(selected) : undefined
  const projected = useMemo(() => projectEvidenceGraph(graph, { query, role: role || undefined, severity: severity || undefined, kind: kind || undefined, focus, collapsed, agreement: agreement || undefined }), [graph, query, role, severity, kind, focus, collapsed, agreement])
  const related = useMemo(() => graph.edges.flatMap(edge => {
    if (!selected || (edge.source !== selected && edge.target !== selected)) return []
    const node = byNodeId.get(edge.source === selected ? edge.target : edge.source)
    return node ? [{ node, relation: edge.kind }] : []
  }), [graph, selected, byNodeId])
  const flowNodes = useMemo<Node[]>(() => {
    const nodes: Node[] = []
    const activeLanes = lanes.filter(lane => projected.nodes.some(node => node.kind === lane) || projected.groups.some(group => group.kind === lane))
    activeLanes.forEach((lane, column) => {
      const items = projected.nodes.filter(node => node.kind === lane).sort(byId)
      const group = projected.groups.find(item => item.kind === lane)
      const y = (index: number) => index * 106
      if (group) nodes.push({ id: group.id, position: { x: column * 262, y: 0 }, data: { label: <div className="sr-graph-node-label"><Typography as="strong" variant="bodySmMedium">{evidenceKindLabels[lane]} · {group.count}</Typography><Typography as="span" variant="caption">Раскрыть группу</Typography></div> }, style: { width: 220, height: 80, borderColor: nodeColors[lane] }, className: 'sr-graph-group-node', sourcePosition: Position.Right, targetPosition: Position.Left, ariaLabel: `Раскрыть: ${evidenceKindLabels[lane]}, ${group.count} узлов` })
      items.forEach((node, i) => nodes.push({ id: node.id, position: { x: column * 262, y: y(i + (group ? 1 : 0)) }, data: { label: <div className="sr-graph-node-label"><Typography as="span" variant="caption">{node.id} · {agreements.has(node.id) ? findingAgreementLabels[agreements.get(node.id)!] : evidenceKindLabels[node.kind]}</Typography><Typography as="strong" variant="bodySmMedium">{node.title}</Typography></div> }, style: { width: 220, height: 80, borderColor: nodeColors[lane] }, sourcePosition: Position.Right, targetPosition: Position.Left, className: `sr-graph-node sr-graph-node-${lane} sr-graph-agreement-${agreements.get(node.id) ?? 'other'}`, selected: selected === node.id, ariaLabel: `${node.id}: ${node.title}` }))
    })
    return nodes
  }, [projected, selected, agreements])
  const edges = useMemo<Edge[]>(() => projected.edges.map(edge => ({ id: JSON.stringify([edge.source, edge.target, edge.kind]), source: edge.source, target: edge.target, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed }, label: edge.kind === 'disagrees_with' || edge.kind === 'adjudication_context' ? evidenceEdgeLabels[edge.kind] : undefined, style: edge.kind === 'disagrees_with' ? { stroke: '#c7515f', strokeDasharray: '6 4', strokeWidth: 2 } : edge.kind === 'adjudication_context' ? { strokeDasharray: '3 5', strokeWidth: 1.25 } : { strokeWidth: 1.25 }, ariaLabel: `${edge.source}: ${evidenceEdgeLabels[edge.kind]} ${edge.target}`, selectable: false })), [projected.edges])

  function choose(id: string) {
    const group = projected.groups.find(item => item.id === id)
    if (group) { setCollapsed(previous => previous.filter(value => value !== group.kind)); return }
    setSelected(id)
    if (focus && !projected.nodes.some(node => node.id === id)) setFocus(id)
  }
  function toggleGroup(value: AgentEvidenceKind) { setCollapsed(previous => previous.includes(value) ? previous.filter(item => item !== value) : [...previous, value]) }
  function resetFilters() { setQuery(''); setRole(''); setSeverity(''); setKind(''); setAgreement(''); setFocus(undefined); setCollapsed([]) }
  const chosenReference = selectedNode?.source_id ? report.reference_review?.sources.find(source => source.id === selectedNode.source_id) : undefined
  const quote = selectedNode?.evidence
  const isPrimarySource = !selectedNode?.source_id || selectedNode.source_id === 'primary'
  const listNodes = projected.nodes.slice().sort(byId)

  return <section className="sr-evidence-explorer" aria-label="Граф доказательств" data-testid="evidence-graph-explorer" data-list-mode={listMode}>
    <div className="sr-graph-toolbar">
      <div className="sr-graph-summary">
        <Typography as="h3" variant="h4">Граф доказательств</Typography>
        <Typography as="p" variant="caption">{graph.nodes.length} узлов · {graph.edges.length} связей · {graph.unresolved_count} неразрешённых разногласий</Typography>
        {graph.finalization_status === 'deterministic_fallback' && <Typography as="p" variant="caption">Использована программная агрегация замечаний.</Typography>}
        {graph.finalization_status === 'limited' && <Typography as="p" variant="caption">Объединение замечаний ограничено объёмом контекста; похожие выводы могут отображаться отдельно.</Typography>}
        {graph.adjudication && <Typography as="p" variant="caption">Арбитраж: подтверждено {graph.adjudication.confirmed}, рекомендаций {graph.adjudication.recommendations ?? 0}, отклонено {graph.adjudication.unsupported}, требуют проверки {graph.adjudication.uncertain + graph.adjudication.unreviewed} из {graph.adjudication.candidates}.</Typography>}
      </div>
      <div className="sr-graph-agreement" aria-label="Группы экспертных выводов">
        {(Object.entries(findingAgreementLabels) as [FindingAgreement, string][]).map(([value, label]) => <button type="button" key={value} data-agreement={value} aria-pressed={agreement === value} onClick={() => setAgreement(previous => previous === value ? '' : value)}><Typography as="span" variant="caption">{label} · {[...agreements.values()].filter(item => item === value).length}</Typography></button>)}
        <Typography as="p" variant="caption">Согласование: вклад разных экспертов без явной связи разногласия. Спорные выводы включают и зафиксированные, позже разрешённые разногласия.</Typography>
      </div>
      <details className="sr-graph-experts"><Typography as="summary" variant="bodySmMedium">Эксперты · {graph.experts.length}</Typography><ul>{graph.experts.map(expert => <li key={expert.role}><Typography as="strong" variant="bodySmMedium">{evidenceRoleLabels[expert.role]}</Typography><Typography as="span" variant="caption">{expertStatuses[expert.status]} · {expert.observations} наблюдений</Typography></li>)}</ul></details>
      <div className="sr-graph-filters">
        <label className="sr-graph-search"><Typography as="span" variant="caption">Поиск по ID или названию</Typography><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Например, D-001" /></label>
        <label><Typography as="span" variant="caption">Эксперт</Typography><select value={role} onChange={event => setRole(event.target.value as AgentEvidenceRole | '')}><option value="">Все эксперты</option>{Object.entries(evidenceRoleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><Typography as="span" variant="caption">Приоритет</Typography><select value={severity} onChange={event => setSeverity(event.target.value as typeof severity)}><option value="">Все приоритеты</option>{Object.entries(severities).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><Typography as="span" variant="caption">Тип узла</Typography><select value={kind} onChange={event => setKind(event.target.value as AgentEvidenceKind | '')}><option value="">Все типы</option>{Object.entries(evidenceKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      </div>
      <div className="sr-graph-actions">
        <Button size="sm" variant={focus ? 'outline' : 'default'} onClick={() => setFocus(undefined)} aria-pressed={!focus}>Весь граф</Button>
        <Button size="sm" variant={focus ? 'default' : 'outline'} onClick={() => { if (selected) setFocus(selected) }} disabled={!selected} aria-pressed={focus === selected && Boolean(selected)}>Связи узла</Button>
        <Button size="sm" variant="outline" onClick={() => setListMode(previous => !previous)} aria-pressed={listMode} className="sr-graph-view-toggle">{listMode ? 'К графу' : 'Список узлов'}</Button>
        <Button size="sm" variant="ghost" onClick={resetFilters}>Сбросить фильтры</Button>
        <Typography as="span" variant="caption" aria-live="polite">Показано {projected.total} из {graph.nodes.length} узлов</Typography>
      </div>
      <div className="sr-graph-groups" aria-label="Свернуть или раскрыть группы">{lanes.filter(lane => graph.nodes.some(node => node.kind === lane)).map(lane => <button type="button" key={lane} onClick={() => toggleGroup(lane)} aria-pressed={collapsed.includes(lane)}><i style={{ backgroundColor: nodeColors[lane] }} aria-hidden /><Typography as="span" variant="caption">{evidenceKindLabels[lane]}{collapsed.includes(lane) ? ' + ' : ' − '}</Typography></button>)}</div>
    </div>
    <div className="sr-graph-content">
      <div className="sr-graph-canvas" aria-label="Интерактивная схема связей" key={`${focus ?? ''}:${listMode}`}>
        {flowNodes.length > 0 ? <ReactFlow nodes={flowNodes} edges={edges} onNodeClick={(_, node) => choose(node.id)} onSelectionChange={({ nodes }) => { if (nodes[0] && byNodeId.has(nodes[0].id) && nodes[0].id !== selected) setSelected(nodes[0].id) }} nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} onlyRenderVisibleElements minZoom={0.015} maxZoom={2.2} fitView fitViewOptions={{ padding: .18 }}>
          <Background gap={24} size={1} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable ariaLabel="Мини-карта графа" nodeColor={node => node.style?.borderColor as string ?? '#768ba4'} />
        </ReactFlow> : <Typography as="p" variant="bodySm" className="sr-graph-empty">Узлы по этим фильтрам не найдены.</Typography>}
      </div>
      <div className="sr-graph-list" aria-label="Узлы графа">
        {projected.groups.map(group => <button type="button" key={group.id} onClick={() => choose(group.id)}><Typography as="strong" variant="bodySmMedium">{evidenceKindLabels[group.kind]} · {group.count}</Typography><Typography as="span" variant="caption">Раскрыть группу</Typography></button>)}
        {listNodes.map(node => <button type="button" key={node.id} aria-pressed={selected === node.id} onClick={() => choose(node.id)}><Typography as="span" variant="caption">{node.id} · {agreements.has(node.id) ? findingAgreementLabels[agreements.get(node.id)!] : evidenceKindLabels[node.kind]}</Typography><Typography as="strong" variant="bodySmMedium">{node.title}</Typography></button>)}
        {!listNodes.length && !projected.groups.length && <Typography as="p" variant="bodySm">Узлы не найдены.</Typography>}
      </div>
      <aside className="sr-graph-details" aria-label="Детали выбранного узла">
        {selectedNode ? <>
          <Typography as="p" variant="caption">{selectedNode.id} · {evidenceKindLabels[selectedNode.kind]}</Typography>
          <Typography as="h4" variant="bodySmMedium">{selectedNode.title}</Typography>
          {agreements.has(selectedNode.id) && <Typography as="p" variant="caption" className="sr-graph-agreement-label" data-agreement={agreements.get(selectedNode.id)}>{findingAgreementLabels[agreements.get(selectedNode.id)!]}</Typography>}
          {selectedNode.role && <Typography as="p" variant="caption">Эксперт: {evidenceRoleLabels[selectedNode.role]}</Typography>}
          {selectedNode.severity && <Typography as="p" variant="caption">Приоритет: {severities[selectedNode.severity]}</Typography>}
          {selectedNode.kind === 'counterexample' && <Typography as="p" variant="caption">Смоделированный сценарий, не реальное событие.</Typography>}
          {selectedNode.description && <Typography as="p" variant="bodySm">{selectedNode.description}</Typography>}
          {quote && <div className="sr-graph-quote"><Typography as="p" variant="caption">{chosenReference ? `${chosenReference.title} · ${formatEvidenceLocation(quote, chosenReference.source)}` : formatEvidenceLocation(quote, report.source)}</Typography><Typography as="blockquote" variant="bodySm">{quote.quote}</Typography>{isPrimarySource ? <Button size="sm" variant="outline" onClick={() => onShowEvidence(quote)}>Показать в документе</Button> : chosenReference && <Button size="sm" variant="outline" onClick={() => onShowReference(chosenReference.id, quote)}>Показать в регламенте</Button>}</div>}
          {related.length > 0 && <><Typography as="h5" variant="bodySmMedium">Связанные узлы</Typography><ul className="sr-graph-relations">{related.map(({ node, relation }, i) => <li key={`${node.id}:${relation}:${i}`} data-disputed={relation === 'disagrees_with'}><button type="button" onClick={() => choose(node.id)}><Typography as="span" variant="caption">{evidenceEdgeLabels[relation]} · {node.id}</Typography><Typography as="strong" variant="bodySmMedium">{node.title}</Typography></button></li>)}</ul></>}
        </> : <Typography as="p" variant="bodySm">Выберите узел, чтобы увидеть его доказательства и связанные наблюдения.</Typography>}
      </aside>
    </div>
  </section>
}
