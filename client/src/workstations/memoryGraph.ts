import type { GraphData } from '@antv/g6'
import type { EmployeeMemoryFact } from '../services/office/officeApi'

export type MemoryGrouping = 'category' | 'source'
export const memoryColors = ['#3f8886', '#be8c45', '#7179aa', '#ae6c71', '#60845e', '#608aa1']

export function buildMemoryGraph(facts: EmployeeMemoryFact[], grouping: MemoryGrouping, employeeName: string, label: (value: string) => string): GraphData {
  const groups = new Map<string, EmployeeMemoryFact[]>()
  for (const fact of facts) {
    const value = fact[grouping]
    const key = grouping === 'source' && (!value || value === 'unknown') ? `unattributed:${fact.id}` : value
    groups.set(key, [...(groups.get(key) || []), fact])
  }
  const nodes: NonNullable<GraphData['nodes']> = [{ id: 'employee', type: 'circle', data: { kind: 'employee', title: employeeName, color: '#586955' }, style: { x: 0, y: 0 } }]
  const edges: NonNullable<GraphData['edges']> = []
  Array.from(groups.entries()).forEach(([key, members], index) => {
    const groupId = `group:${key}`
    const color = memoryColors[index % memoryColors.length]
    const angle = index / groups.size * Math.PI * 2 - Math.PI / 2
    nodes.push({ id: groupId, type: 'circle', data: { kind: 'group', title: label(key), color }, style: { x: Math.cos(angle) * 170, y: Math.sin(angle) * 170 } })
    edges.push({ id: `owns:${groupId}`, source: 'employee', target: groupId, data: { color } })
    members.forEach((fact, position) => {
      const factId = `fact:${fact.id}`
      const spread = angle + (position - (members.length - 1) / 2) * 0.3
      nodes.push({ id: factId, type: 'circle', data: { kind: 'fact', factId: fact.id, title: fact.content.length > 10 ? `${fact.content.slice(0, 10)}…` : fact.content, color }, style: { x: Math.cos(spread) * 330, y: Math.sin(spread) * 330 } })
      edges.push({ id: `classified:${fact.id}`, source: groupId, target: factId, data: { color } })
    })
  })
  return { nodes, edges }
}
