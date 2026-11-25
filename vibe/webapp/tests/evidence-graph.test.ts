import { expect, test } from 'bun:test'
import type { AgentEvidenceGraph } from '@web-app-demo/contracts'
import { findingAgreement, projectEvidenceGraph } from '../src/features/agent/evidence-graph'

const graph: AgentEvidenceGraph = {
  schema_version: 1,
  nodes: [
    { id: 'R-001', kind: 'requirement', title: 'Requirement', requirement_id: 'R-001' },
    { id: 'R-002', kind: 'requirement', title: 'Unrelated', requirement_id: 'R-002' },
    { id: 'O-001', kind: 'observation', title: 'Logic', role: 'logic_inspector', severity: 'high' },
    { id: 'O-002', kind: 'observation', title: 'Scenario', role: 'scenario_builder', severity: 'high' },
    { id: 'E-001', kind: 'evidence', title: 'Exact quote', evidence: { start: 3, end: 3, quote: 'Requirement' } },
    { id: 'D-001', kind: 'finding', title: 'Conflict', finding_id: 'D-001', severity: 'high' },
  ],
  edges: [{ source: 'O-001', target: 'E-001', kind: 'cites' }, { source: 'O-001', target: 'D-001', kind: 'contributes_to' }, { source: 'O-002', target: 'D-001', kind: 'contributes_to' }, { source: 'D-001', target: 'R-001', kind: 'about' }, { source: 'O-001', target: 'D-001', kind: 'disagrees_with' }],
  experts: [], finalization_status: 'completed', unresolved_count: 1,
}

test('neighborhood retains both independent expert observations and excludes unrelated requirements', () => {
  const view = projectEvidenceGraph(graph, { focus: 'D-001' })
  expect(view.nodes.map(item => item.id)).toEqual(['R-001', 'O-001', 'O-002', 'E-001', 'D-001'])
  expect(view.edges.some(item => item.kind === 'disagrees_with')).toBe(true)
})

test('role, kind and ID filters retain directly related observation context', () => {
  const view = projectEvidenceGraph(graph, { role: 'logic_inspector', kind: 'finding', query: 'd-001' })
  expect(view.nodes.some(item => item.id === 'D-001')).toBe(true)
  expect(view.nodes.some(item => item.id === 'O-001')).toBe(true)
  expect(view.nodes.some(item => item.id === 'R-002')).toBe(false)
  const ids = new Set(view.nodes.map(item => item.id))
  expect(view.edges.every(item => ids.has(item.source) && ids.has(item.target))).toBe(true)
})

test('collapse groups preserves graph links and does not mutate the source', () => {
  const view = projectEvidenceGraph(graph, { collapsed: ['observation'] })
  expect(view.groups).toEqual([{ id: 'group:observation', kind: 'observation', count: 2 }])
  expect(view.edges.some(edge => edge.source === 'group:observation' && edge.target === 'D-001')).toBe(true)
  expect(graph.nodes.filter(item => item.kind === 'observation')).toHaveLength(2)
})


test('finding agreement distinguishes disagreement, independent support, single line and no observations', () => {
  expect(findingAgreement(graph).get('D-001')).toBe('disputed')
  const noDispute = { ...graph, edges: graph.edges.filter(edge => edge.kind !== 'disagrees_with') }
  expect(findingAgreement(noDispute).get('D-001')).toBe('agreed')
  const sameRole = { ...noDispute, nodes: noDispute.nodes.map(node => node.id === 'O-002' ? { ...node, role: 'logic_inspector' as const } : node) }
  expect(findingAgreement(sameRole).get('D-001')).toBe('single')
  expect(findingAgreement({ ...noDispute, edges: noDispute.edges.filter(edge => edge.kind !== 'contributes_to') }).get('D-001')).toBe('unassessed')
})

test('a relation between two findings marks both disputed; code checks are not a second expert', () => {
  const second = { id: 'D-002', kind: 'finding' as const, title: 'Other', finding_id: 'D-002' }
  const noDispute = { ...graph, nodes: [...graph.nodes.map(node => node.id === 'O-002' ? { ...node, role: 'code_checks' as const } : node), second], edges: graph.edges.filter(edge => edge.kind !== 'disagrees_with') }
  expect(findingAgreement(noDispute).get('D-001')).toBe('single')
  const dispute = { ...noDispute, edges: [...noDispute.edges, { source: 'D-001', target: 'D-002', kind: 'disagrees_with' as const }] }
  expect(findingAgreement(dispute).get('D-001')).toBe('disputed')
  expect(findingAgreement(dispute).get('D-002')).toBe('disputed')
})


test('agreement filtering retains the selected finding and expert context', () => {
  const disputed = projectEvidenceGraph(graph, { agreement: 'disputed' })
  expect(disputed.nodes.some(node => node.id === 'D-001')).toBe(true)
  expect(disputed.nodes.some(node => node.id === 'O-002')).toBe(true)
  expect(projectEvidenceGraph(graph, { agreement: 'agreed' }).nodes).toHaveLength(0)
})
