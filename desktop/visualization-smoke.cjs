// Production renderer/preload/HTTP Gateway; controlled model, isolated files.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

exports.run = async ({ window, api, send, waitFor }) => {
  const { workspace } = await api('/api/office/project')
  assert.match(workspace.path, /office-stage4-gateway-/)
  const block = (lang, code) => '```' + lang + '\n' + code + '\n```'
  const chart = { type: 'bar', title: '模块任务对比', labels: ['前端', '后端', '测试'], series: [{ name: '已完成', values: [8, 6, 5] }], unit: '项', source: '验收示例数据' }
  const doc = [
    '# 办公室任务图表', '以下使用示例数据验证图表展示。',
    block('mermaid', 'flowchart LR\n U["用户提出目标"] --> P["PM 理解需求"]\n P --> E["员工执行"]\n E --> R["验收交付"]'),
    block('mermaid', 'sequenceDiagram\n participant U as 用户\n participant P as PM\n participant E as 员工\n U->>P: 提出目标\n P->>E: 指派任务\n E-->>P: 返回证据\n P-->>U: 汇总交付'),
    block('office-chart', JSON.stringify(chart)),
    block('office-chart', JSON.stringify({ ...chart, type: 'line', title: '任务趋势', series: [{ name: '已完成', values: [3, null, 8] }] })),
    block('office-chart', JSON.stringify({ ...chart, type: 'pie', title: '任务占比' })),
    block('office-chart', '{"type":"bar","title":"坏数据"}'),
    block('mermaid', 'flowchart LR\n A["<img src=x onerror=window.visualInjected=true>"]'),
    block('mermaid', 'flowchart LR\n A[broken'),
    // Exact diagram from the reported successful Run whose UI rejected <br/>.
    block('mermaid', fs.readFileSync(path.join(__dirname, 'fixtures/mermaid-break-labels.mmd'), 'utf8')),
    block('json', JSON.stringify(chart)),
    block('typescript', 'const answer = 42'),
    '[参考网站](https://example.com)', '正文结尾仍然显示正常。',
  ].join('\n\n')
  fs.writeFileSync(path.join(workspace.path, 'diagrams.md'), doc)
  const evaluate = code => window.webContents.executeJavaScript(code)
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`)
  const output = path.resolve(__dirname, '../output/playwright')
  fs.mkdirSync(output, { recursive: true })
  const capture = async name => {
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))')
    fs.writeFileSync(path.join(output, name), (await window.webContents.capturePage()).toPNG())
  }
  const imagesReady = async scope => {
    window.show(); window.focus()
    for (let index = 0; index < 9; index++) {
      await evaluate(`document.querySelectorAll(${JSON.stringify(scope + ' [data-office-visualization]')})[${index}].scrollIntoView({block:'center',behavior:'instant'})`)
      // Programmatic scrolling must notify the chat's follow-latest handler,
      // just as native user scrolling does, before late history reads render.
      if (scope === '[data-office-chat-messages]') {
        await evaluate(`document.querySelector('${scope}').dispatchEvent(new Event('scroll'))`)
      }
      try {
        await waitFor(`document.querySelectorAll(${JSON.stringify(scope + ' [data-office-visualization]')})[${index}].dataset.visualStatus === '${index < 5 || index === 8 ? 'ready' : 'error'}'`, `visual block ${index}`, 45000)
        if (index < 5 || index === 8) {
          await waitFor(`(() => { const image = document.querySelectorAll(${JSON.stringify(scope + ' [data-office-visualization]')})[${index}].querySelector('.visual-image'); return image?.complete && image.naturalWidth > 0 })()`, `visual image ${index}`)
          await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))')
        }
      } catch (error) {
        await capture('visualization-timeout.png')
        console.log('VISUAL_BOUNDS', await evaluate(`[...document.querySelectorAll('[data-office-visualization]')].map(node => ({status:node.dataset.visualStatus,top:node.getBoundingClientRect().top,bottom:node.getBoundingClientRect().bottom}))`))
        throw error
      }
    }
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(scope + ' .visual-image')}).length`), 6)
    await waitFor(`[...document.querySelectorAll(${JSON.stringify(scope + ' .visual-image')})].every(img => img.complete && img.naturalWidth > 0)`, 'SVG images decoded')
    const actualLabels = await evaluate(`(() => { const image = document.querySelectorAll(${JSON.stringify(scope + ' [data-office-visualization]')})[8].querySelector('img'); const svg = new DOMParser().parseFromString(decodeURIComponent(image.src.split(',').slice(1).join(',')), 'image/svg+xml'); return { labels: [...svg.querySelectorAll('text')].map(node => node.textContent.replace(/\\s/g, '')), html: svg.querySelectorAll('br, foreignObject, script').length } })()`)
    assert.ok(actualLabels.labels.includes('index.html·澄江大学校园门户'), JSON.stringify(actualLabels))
    assert.ok(actualLabels.labels.includes('预约成功提示·按钮状态更新并禁用'), JSON.stringify(actualLabels))
    assert.equal(actualLabels.html, 0, 'break labels render as SVG text without HTML')
  }
  await send('E2E_VISUALIZATION 给我画一下办公室的任务流程，再把模块任务数据画成图看看。')
  const scope = '[data-office-chat-messages]'
  await waitFor(`document.querySelectorAll('${scope} [data-office-visualization]').length === 9`, 'reply includes diagrams')
  await imagesReady(scope)
  assert.ok(await evaluate(`document.querySelectorAll('${scope} [role="alert"]')[2].textContent.includes('语法有误')`), 'readable syntax error')
  assert.equal(await evaluate('!!window.visualInjected'), false)
  assert.ok(await evaluate(`document.querySelector('${scope} code.language-typescript').textContent.includes('answer')`))
  assert.ok(await evaluate(`document.querySelector('${scope} code.language-json').textContent.includes('模块任务对比')`))
  assert.ok(await evaluate(`document.querySelector('${scope}').textContent.includes('正文结尾仍然显示正常')`))
  assert.ok(await evaluate(`!!document.querySelector('${scope} a[href="https://example.com"]')`))
  // Reading the Skill is a real tool call; its visualization examples must not
  // turn tool logs into duplicate charts or remove the existing tool displays.
  const messages = (await api('/api/office/group/messages')).messages
  const reply = messages.findLast(message => message.sourceRunId && message.content.includes('办公室任务图表'))
  assert.ok(reply)
  assert.ok(await evaluate(`document.querySelector('${scope}').textContent.includes('read_file')`), 'tool activity remains visible')
  const first = `${scope} [data-office-visualization]`
  await evaluate(`document.querySelector('${first}').scrollIntoView({block:'center',behavior:'instant'})`)
  assert.equal(await evaluate(`document.querySelectorAll('${scope} [data-office-visualization] pre').length`), 0, 'no diagram source dump')
  assert.equal(await evaluate(`[...document.querySelectorAll('${scope} .visual-toolbar button')].some(button => button.textContent.includes('源码'))`), false)
  await click(`${first} button[aria-label^="放大图表"]`)
  await waitFor(`document.querySelector('[data-office-visualization-preview] img')?.naturalWidth > 0`, 'enlarged diagram')
  await click('[aria-label="关闭图表预览"]')
  await waitFor(`!document.querySelector('[data-office-visualization-preview]')`, 'enlargement closes')
  await capture('chat-visualizations.png')
  await evaluate(`document.querySelectorAll('${scope} [data-office-visualization]')[2].scrollIntoView({block:'center',behavior:'instant'})`)
  await capture('chat-chart.png')
  await evaluate(`document.querySelectorAll('${scope} [data-office-visualization]')[4].scrollIntoView({block:'center',behavior:'instant'})`)
  await capture('chat-pie.png')
  await evaluate(`document.querySelectorAll('${scope} [data-office-visualization]')[8].scrollIntoView({block:'center',behavior:'instant'})`)
  await capture('chat-break-labels-fixed.png')

  await evaluate(`document.querySelector('[data-office-file="diagrams.md"]').scrollIntoView({block:'center',behavior:'instant'})`)
  await click('[data-office-file="diagrams.md"]')
  await waitFor(`document.querySelectorAll('[data-office-file-preview] [data-office-visualization]').length === 9`, 'Markdown uses same renderer')
  await imagesReady('[data-office-file-preview]')
  await evaluate(`document.querySelectorAll('[data-office-file-preview] [data-office-visualization]')[8].scrollIntoView({block:'center',behavior:'instant'}); document.querySelectorAll('[data-office-file-preview] [data-office-visualization]')[8].querySelector('button[aria-label^="放大图表"]').click()`)
  await waitFor(`(() => { const preview = document.querySelector('[data-office-visualization-preview]'); return preview && getComputedStyle(preview.parentElement).opacity === '1' && preview.querySelector('img')?.naturalWidth > 0 })()`, 'enlarge from nested preview')
  assert.ok(await evaluate(`(() => { const img = document.querySelector('[data-office-visualization-preview] img'); const image = img.getBoundingClientRect(); const frame = img.parentElement.getBoundingClientRect(); return image.left >= frame.left && image.right <= frame.right && image.top >= frame.top && image.bottom <= frame.bottom })()`), 'enlarged diagram fits the preview instead of cropping to one node')
  await capture('diagram-preview.png')
  await click('[aria-label="关闭图表预览"]')
  assert.ok(await evaluate(`!!document.querySelector('[data-office-file-preview]')`), 'file preview stays open')
  await evaluate(`[...document.querySelectorAll('[data-office-file-preview] .mode button')].find(button => button.textContent === '源码').click()`)
  await waitFor(`document.querySelector('[data-office-code-preview] .view-lines')?.textContent.includes('办公室任务图表')`, 'Markdown source remains available')
  await click('[aria-label="关闭文件预览"]')
  await waitFor(`!document.querySelector('[data-office-file-preview]')`, 'file preview closes')
  // History reconstruction does not depend on a fresh Run or a Skill grant.
  window.reload()
  await waitFor(`document.querySelector('[data-office-chat-launcher]')`, 'reload office')
  await click('[data-office-chat-launcher]')
  await waitFor(`document.querySelectorAll('${scope} [data-office-visualization]').length === 9`, 'persisted reply reconstructed')
  await imagesReady(scope)
  assert.equal(fs.readFileSync(path.join(workspace.path, 'diagrams.md'), 'utf8'), doc, 'preview never edits files')
  console.log('VISUALIZATION_UI_GATEWAY_OK: skill discovery/read, flow, sequence, bar/line/pie, invalid JSON/syntax/HTML fallback, no diagram source control, zoom, Markdown, history, code and tool display')
}
