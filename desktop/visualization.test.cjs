const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../client/node_modules/typescript')
const exportsForSpec = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../client/src/components/office-chat/visualizationSpec.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: exportsForSpec, require: () => require('./i18n-test-helper.cjs') })
const { parseChart, validateMermaid } = exportsForSpec
const chart = { type: 'bar', title: '任务进度', labels: ['前端', '测试'], series: [{ name: '完成', values: [4, 2] }], unit: '项' }
const parse = value => parseChart(JSON.stringify(value))

test('bar and line retain real values, negatives, and missing line data without inventing zero', () => {
  assert.equal(parse(chart).series[0].values[0], 4)
  const line = parse({ ...chart, type: 'line', series: [{ name: '变化', values: [-2, null] }] })
  assert.equal(line.series[0].values[0], -2)
  assert.equal(line.series[0].values[1], null)
})
test('pie requires one nonnegative, nonempty whole', () => {
  assert.equal(parse({ ...chart, type: 'pie' }).type, 'pie')
  for (const values of [[-1, 2], [0, 0], [null, 2]]) {
    assert.throws(() => parse({ ...chart, type: 'pie', series: [{ name: '完成', values }] }))
  }
  assert.throws(() => parse({ ...chart, type: 'pie', series: [...chart.series, { name: '其他', values: [1, 2] }] }))
})
test('misaligned, ambiguous and nonnumeric values fail locally', () => {
  for (const values of [[4], ['4', 2], [true, 2], [1e14, 2]]) {
    assert.throws(() => parse({ ...chart, series: [{ name: '完成', values }] }))
  }
  assert.throws(() => parse({ ...chart, labels: ['前端', '前端'] }))
  assert.throws(() => parse({ ...chart, series: [...chart.series, ...chart.series] }))
  assert.throws(() => parseChart('{"type":"bar"'))
  assert.throws(() => parseChart(JSON.stringify(chart).replace('[4,2]', '[1e999,2]')))
})
test('arbitrary ECharts options, HTML output and prototype fields cannot enter the renderer', () => {
  for (const extra of [{ tooltip: { formatter: 'alert(1)' } }, { backgroundColor: 'url(https://example.com)' }, { html: '<script>1</script>' }]) {
    assert.throws(() => parse({ ...chart, ...extra }))
  }
  assert.throws(() => parseChart(JSON.stringify(chart).replace('{', '{"__proto__":{},')))
  assert.throws(() => parse({ ...chart, series: [{ ...chart.series[0], itemStyle: { color: 'red' } }] }))
})
test('oversized graphs and data are bounded before importing rendering libraries', () => {
  assert.throws(() => parseChart(' '.repeat(12001)))
  assert.throws(() => parse({ ...chart, labels: Array.from({ length: 41 }, (_, i) => String(i)) }))
  assert.throws(() => validateMermaid('flowchart LR\n' + 'A-->B\n'.repeat(121)))
})
test('Mermaid supports plain flow/architecture and sequence relationships', () => {
  assert.equal(validateMermaid('%% 简短说明\nflowchart LR\n A["用户"] --> B["PM"]'), 'flowchart LR\n A["用户"] --> B["PM"]')
  assert.ok(validateMermaid('sequenceDiagram\n U->>P: 提出目标'))
})

test('attribute-free Mermaid break tags become text without mutating the source', () => {
  for (const tag of ['<br>', '<br/>', '<br />', '<BR />', '<br\t/>']) {
    const source = `flowchart LR\n A["入口${tag}校园门户"] --> B[交付]`
    assert.equal(validateMermaid(source), 'flowchart LR\n A["入口 · 校园门户"] --> B[交付]')
    assert.ok(source.includes(tag))
  }
  assert.equal(validateMermaid('sequenceDiagram\n U->>P: 提出目标<br/>确认范围'), 'sequenceDiagram\n U->>P: 提出目标 · 确认范围')
  const actual = fs.readFileSync(path.join(__dirname, 'fixtures/mermaid-break-labels.mmd'), 'utf8')
  assert.equal((actual.match(/<br\/>/g) || []).length, 6)
  assert.equal(validateMermaid(actual), actual.trim().replace(/<br\/>/g, ' · '))
})

test('break compatibility does not allow attributes, other tags or oversized input', () => {
  for (const tag of ['<br onclick="alert(1)">', '<br style="color:red"/>', '<br class=x>', '<br data-x=1>', '<br/ onload=x>', '<br><img src=x>', '<br/><script>alert(1)</script>', '<bracket>']) {
    assert.throws(() => validateMermaid(`flowchart LR\n A["入口${tag}校园门户"]`))
  }
  assert.throws(() => validateMermaid('flowchart LR\n A["' + '<br/>'.repeat(2500) + '"]'))
})
test('Mermaid cannot override configuration or inject markup, styling, resource loads and actions', () => {
  for (const code of [
    '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B',
    '---\nconfig: {}\n---\nflowchart LR\nA-->B',
    'flowchart LR\nA["<img src=x onerror=alert(1)>"]',
    'flowchart LR\nA-->B\nclick A "javascript:alert(1)"',
    'flowchart LR\nA-->B\nclassDef default fill:red',
    'flowchart LR\nA@{ img: "https://example.com/image" }',
  ]) assert.throws(() => validateMermaid(code))
})
test('skill examples use the same executable output contracts as chat', () => {
  const content = fs.readFileSync(path.join(__dirname, '../skills/public/office-visualization/SKILL.md'), 'utf8')
  const examples = [...content.matchAll(/```(mermaid|office-chart)\r?\n([\s\S]*?)```/g)]
  assert.equal(examples.length, 3)
  for (const [, language, source] of examples) (language === 'mermaid' ? validateMermaid : parseChart)(source)
})
