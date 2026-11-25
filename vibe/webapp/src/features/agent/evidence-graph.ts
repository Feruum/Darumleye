import type { AgentEvidenceGraph, AgentEvidenceKind, AgentEvidenceNode, AgentEvidenceRole } from '@web-app-demo/contracts'

type GraphEdge = AgentEvidenceGraph['edges'][number]
export type EvidenceGraphGroup = { id: string; kind: AgentEvidenceKind; count: number }
export type EvidenceGraphFilters = {
  query?: string
  kind?: AgentEvidenceKind
  role?: AgentEvidenceRole
  severity?: NonNullable<AgentEvidenceNode['severity']>
  focus?: string
  agreement?: FindingAgreement
  collapsed?: readonly AgentEvidenceKind[]
}

export const evidenceKindLabels: Record<AgentEvidenceKind, string> = {
  requirement: 'Требования', evidence: 'Цитаты', observation: 'Наблюдения', counterexample: 'Контрпримеры', finding: 'Замечания', question: 'Вопросы', reference: 'Регламенты',
}
export const evidenceRoleLabels: Record<AgentEvidenceRole, string> = {
  logic_inspector: 'Logic Inspector', scenario_builder: 'Scenario Builder', gap_hunter: 'Gap Hunter', contract_inspector: 'Contract Inspector', risk_inspector: 'Risk Inspector', reference_inspector: 'Reference Inspector', code_checks: 'Программная проверка',
}
export const evidenceEdgeLabels: Record<GraphEdge['kind'], string> = {
  cites: 'Цитирует', about: 'Относится к', contributes_to: 'Вклад в замечание', disagrees_with: 'Разногласие', illustrates: 'Иллюстрирует', asks: 'Требует уточнения', from_reference: 'Из регламента',
}

export function evidenceAdjacency(graph: AgentEvidenceGraph) {
  const adjacency = new Map<string, Set<string>>()
  for (const node of graph.nodes) adjacency.set(node.id, new Set())
  for (const { source, target } of graph.edges) {
    adjacency.get(source)?.add(target)
    adjacency.get(target)?.add(source)
  }
  return adjacency
}

export function projectEvidenceGraph(graph: AgentEvidenceGraph, filters: EvidenceGraphFilters) {
  const byId = new Map(graph.nodes.map(node => [node.id, node]))
  const adjacency = evidenceAdjacency(graph)
  function neighborhood(seeds: Iterable<string>, depth: number) {
    const seen = new Set(seeds)
    let frontier = [...seen]
    for (let i = 0; i < depth && frontier.length; i++) {
      const next: string[] = []
      for (const id of frontier) for (const neighbor of adjacency.get(id) ?? []) {
        if (seen.has(neighbor)) continue
        seen.add(neighbor)
        next.push(neighbor)
      }
      frontier = next
    }
    return seen
  }
  const scope = filters.focus ? neighborhood([filters.focus], 2) : new Set(byId.keys())
  const query = filters.query?.trim().toLocaleLowerCase() ?? ''
  const agreement = findingAgreement(graph)
  const filtered = Boolean(query || filters.role || filters.severity || filters.kind || filters.agreement)
  const matches = graph.nodes.filter(node => {
    if (!scope.has(node.id) || (filters.kind && node.kind !== filters.kind)) return false
    if (filters.agreement && agreement.get(node.id) !== filters.agreement) return false
    const neighbors = [...(adjacency.get(node.id) ?? [])].map(id => byId.get(id))
    if (filters.role && node.role !== filters.role && !neighbors.some(neighbor => neighbor?.role === filters.role)) return false
    if (filters.severity && node.severity !== filters.severity && !neighbors.some(neighbor => neighbor?.severity === filters.severity)) return false
    return !query || [node.id, node.requirement_id, node.finding_id, node.question_id, node.source_id, node.title].some(value => value?.toLocaleLowerCase().includes(query))
  })
  const visible = filtered ? neighborhood(matches.map(node => node.id), 1) : scope
  const grouped = new Map<AgentEvidenceKind, EvidenceGraphGroup>()
  const mapped = new Map<string, string>()
  const nodes = graph.nodes.filter(node => {
    if (!scope.has(node.id) || !visible.has(node.id)) return false
    if (!filters.collapsed?.includes(node.kind) || node.id === filters.focus) { mapped.set(node.id, node.id); return true }
    const group = grouped.get(node.kind) ?? { id: `group:${node.kind}`, kind: node.kind, count: 0 }
    group.count++
    grouped.set(node.kind, group)
    mapped.set(node.id, group.id)
    return false
  })
  const seen = new Set<string>()
  const edges: GraphEdge[] = []
  for (const edge of graph.edges) {
    const source = mapped.get(edge.source)
    const target = mapped.get(edge.target)
    if (!source || !target || (source === target && edge.source !== edge.target)) continue
    const key = JSON.stringify([source, target, edge.kind])
    if (seen.has(key)) continue
    seen.add(key)
    edges.push({ source, target, kind: edge.kind })
  }
  return { nodes, groups: [...grouped.values()], edges, matches: matches.length, total: mapped.size }
}

export type FindingAgreement = 'agreed' | 'disputed' | 'single' | 'unassessed'
export const findingAgreementLabels: Record<FindingAgreement, string> = {
  agreed: 'Согласованные выводы',
  disputed: 'Спорные выводы',
  single: 'Одна линия анализа',
  unassessed: 'Без оценки',
}

/** Infer support only from actual contributions; other relations are not votes.
 * A recorded disagreement is preserved even if a later re-review resolved it.
 */
export function findingAgreement(graph: AgentEvidenceGraph): Map<string, FindingAgreement> {
  const byId = new Map(graph.nodes.map(node => [node.id, node]))
  const contributions = new Map<string, Set<AgentEvidenceRole>>()
  const disputes = new Set<string>()
  for (const edge of graph.edges) {
    if (edge.kind === 'contributes_to' && byId.get(edge.target)?.kind === 'finding') {
      const observation = byId.get(edge.source)
      if (observation?.kind !== 'observation' || !observation.role) continue
      const roles = contributions.get(edge.target) ?? new Set()
      roles.add(observation.role)
      contributions.set(edge.target, roles)
    }
    if (edge.kind === 'disagrees_with') {
      for (const id of [edge.source, edge.target]) {
        const node = byId.get(id)
        if (node?.kind === 'finding') disputes.add(node.id)
        else if (node?.finding_id) disputes.add(node.finding_id)
      }
    }
  }
  const result = new Map<string, FindingAgreement>()
  for (const node of graph.nodes) {
    if (node.kind !== 'finding') continue
    const roles = contributions.get(node.id)
    const independentExperts = [...(roles ?? [])].filter(role => role !== 'code_checks')
    result.set(node.id, disputes.has(node.id) ? 'disputed' : independentExperts.length >= 2 ? 'agreed' : roles?.size ? 'single' : 'unassessed')
  }
  return result
}
