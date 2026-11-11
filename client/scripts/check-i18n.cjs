const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const root = path.resolve(__dirname, '../..')
const en = require('../../contracts/locales/en.json')
const zh = require('../../contracts/locales/zh-CN.json')
assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort(), 'Language resource keys differ')
function tokens(value) { return [...String(value).matchAll(/\{\{(\w+)\}\}/g)].map(match => match[1]).sort() }
for (const key of Object.keys(en)) {
  for (const value of [en[key], zh[key]]) {
    assert.ok(typeof value === 'string' && value.trim() || Array.isArray(value) && value.every(item => typeof item === 'string' && item.trim()), `Empty or invalid translation: ${key}`)
  }
  assert.deepEqual(tokens(en[key]), tokens(zh[key]), `Interpolation parameters differ: ${key}`)
}
function exists(key) { return key in en || `${key}_one` in en && `${key}_other` in en }
function check(directory) {
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, item.name)
    if (item.isDirectory()) { check(filename); continue }
    if (!/\.tsx?$/.test(filename)) continue
    const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true)
    function walk(node) {
      if (ts.isStringLiteral(node) && /^(ui|progress|app|gateway|thinking|providers|templates|errors)\.[a-z]/i.test(node.text)) {
        assert.ok(exists(node.text), `Missing key in ${filename}: ${node.text}`)
      }
      if (ts.isCallExpression(node) && node.expression.getText(source) === 't' && node.arguments[0] && ts.isStringLiteral(node.arguments[0])) {
        assert.ok(exists(node.arguments[0].text), `Missing key in ${filename}: ${node.arguments[0].text}`)
      }
      if (!['LanguageSelect.tsx', 'randomEmployeeName.ts'].includes(item.name)) {
        const literal = ts.isJsxText(node) ? node.text : ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ? node.text : ''
        assert.ok(!/[\u4e00-\u9fff]/.test(literal), `Untranslated literal in ${filename}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`)
      }
      ts.forEachChild(node, walk)
    }
    walk(source)
  }
}
check(path.join(root, 'client/src'))
console.log(`i18n check passed: ${Object.keys(en).length} complete keys, matching parameters and no hardcoded UI Chinese.`)
