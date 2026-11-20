const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const model = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/workstations/memoryGraph.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: model })
const facts = [
  { id: 'a', content: 'Alpha', category: 'context', source: 'thread-1' },
  { id: 'b', content: 'Beta', category: 'goal', source: 'thread-1' },
  { id: 'c', content: 'Gamma', category: 'context', source: 'unknown' },
  { id: 'd', content: 'Delta', category: 'goal', source: 'unknown' },
]
test('memory graph uses original categories and sources, not inferred semantic relations', () => {
  for (const mode of ['category', 'source']) {
    const graph = model.buildMemoryGraph(facts, mode, 'Employee', value => value)
    const ids = new Set(graph.nodes.map(node => node.id))
    assert.equal(graph.nodes.filter(node => node.data.kind === 'fact').length, facts.length)
    assert.ok(graph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)))
    assert.ok(graph.edges.every(edge => edge.source === 'employee' || edge.source.startsWith('group:')))
    for (const fact of facts) {
      const edge = graph.edges.find(edge => edge.target === `fact:${fact.id}`)
      const key = mode === 'source' && fact.source === 'unknown' ? `unattributed:${fact.id}` : fact[mode]
      assert.equal(edge.source, `group:${key}`)
    }
  }
})
test('filtered or empty graphs do not leak omitted facts', () => {
  const graph = model.buildMemoryGraph([facts[0]], 'source', 'Employee', value => value)
  assert.equal(graph.nodes.length, 3)
  assert.equal(graph.edges.length, 2)
  assert.equal(model.buildMemoryGraph([], 'category', 'Employee', value => value).edges.length, 0)
})
