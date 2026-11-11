const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const JSZip = require(require.resolve('jszip', { paths: [path.resolve(__dirname, '../client')] }))

function pdfFixture() {
  const stream = 'BT /F1 24 Tf 48 740 Td (Library PDF original content) Tj ET'
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>']
  let data = '%PDF-1.4\n'; const offsets = [0]
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(data)); data += `${i + 1} 0 obj\n${object}\nendobj\n` })
  const start = Buffer.byteLength(data)
  data += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(o => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`
  return Buffer.from(data)
}
async function docxFixture() {
  const zip = new JSZip()
  zip.file('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
  zip.file('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
  zip.file('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:rPr><w:b/><w:sz w:val="40"/></w:rPr><w:t>员工手册原文验收</w:t></w:r></w:p><w:p><w:r><w:t>这是 DOCX 文件里的实际正文，不是示例章节。</w:t></w:r></w:p><w:p><w:hyperlink r:id="rId2"><w:r><w:t>不可导航外部链接</w:t></w:r></w:hyperlink></w:p><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>')
  zip.file('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://invalid.example/library-tracker" TargetMode="External"/></Relationships>')
  return zip.generateAsync({ type: 'nodebuffer' })
}

// Actual Electron renderer and preload, isolated IPC fixture; no Office data or model calls.
exports.run = async function run(window, wait, setValue) {
  const execute = source => window.webContents.executeJavaScript(source)
  const click = selector => execute(`document.querySelector(${JSON.stringify(selector)}).click()`)
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
  const output = path.resolve(__dirname, '../output/playwright')
  await fs.mkdir(output, { recursive: true })
  const shot = async name => fs.writeFile(path.join(output, name), (await window.webContents.capturePage()).toPNG())
  const search = 'input[aria-label="搜索资料名称"]'
  const ready = () => wait(window, "document.querySelector('.library-reader.is-ready') !== null", 'reader completes unfolding')
  const back = async () => { await click('.library-back'); await wait(window, "!document.querySelector('.library-reader')", 'book folds back into shelf') }
  window.webContents.setZoomFactor(1)
  window.setSize(1440, 960)
  window.webContents.debugger.attach('1.3')
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] })
  await click('[data-office-library-launcher]')
  await wait(window, "document.body.innerText.includes('把常用资料放上书架')", 'one empty shelf, no samples')
  assert.equal(await execute("document.querySelectorAll('.library-tabs, .library-toc, a[download]').length"), 0)
  assert.equal(await execute("document.querySelector('[data-office-library-launcher]').getBoundingClientRect().top > document.querySelector('[data-office-employee-editor-launcher]').getBoundingClientRect().bottom"), true)
  const pdf = pdfFixture(); const docx = await docxFixture()
  await fs.writeFile(path.join(output, 'library-original.pdf'), pdf)
  await fs.writeFile(path.join(output, 'library-original.docx'), docx)
  const names = ['前端开发规范', '品牌视觉指南', '产品介绍', '接口设计规范', '文档写作指南', '项目交付清单', '个人工作约定', '技术参考资料']
  await execute(`(() => {
    const transfer = new DataTransfer();
    ${JSON.stringify(names)}.forEach((name,i) => transfer.items.add(new File([i === 0 ? ${JSON.stringify('# 前端开发规范\n\n这是文件原文。\n\n## 命名约定\n组件命名应表达业务含义。\n\n## 提交前检查\n确认没有无关改动。\n\n<script>window.libraryInjected=true</script>\n![tracker](https://invalid.example/tracker.png)\n[link](javascript:alert(1))')} : '全文内容：' + name], name + (i === 6 ? '.txt' : '.md'))));
    transfer.items.add(new File([Uint8Array.from(atob(${JSON.stringify(pdf.toString('base64'))}), c=>c.charCodeAt(0))], '参考资料.pdf'));
    transfer.items.add(new File([Uint8Array.from(atob(${JSON.stringify(docx.toString('base64'))}), c=>c.charCodeAt(0))], '员工手册.docx'));
    const input = document.querySelector('[data-office-library] input[type=file]');
    input.files = transfer.files; input.dispatchEvent(new Event('change', {bubbles:true}));
  })()`)
  await wait(window, "document.querySelectorAll('[data-library-book]').length === 10", 'all files in one shelf')
  assert.equal(await execute("getComputedStyle(document.querySelector('.library-grid')).gridTemplateColumns.split(' ').length"), 5)
  assert.equal(await execute("getComputedStyle(document.querySelector('.library-cover')).transform"), 'none', 'resting cover is upright')
  window.showInactive()
  await pause(500)
  // Real pointer events: each hovered corner recedes, not a single preset rotation.
  for (const [x, y, rx, ry] of [[.1,.1,1,-1],[.9,.1,1,1],[.1,.9,-1,-1],[.9,.9,-1,1]]) {
    const point = await execute(`(() => {const r=document.querySelector('.library-book-space').getBoundingClientRect();return {x:Math.round(r.x+r.width*${x}),y:Math.round(r.y+r.height*${y})}})()`)
    await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', {type:'mouseMoved', ...point}); await pause(80)
    const tilt = await execute("({x:parseFloat(document.querySelector('.library-book').style.getPropertyValue('--tilt-x')),y:parseFloat(document.querySelector('.library-book').style.getPropertyValue('--tilt-y'))})")
    assert.equal(Math.sign(tilt.x), rx); assert.equal(Math.sign(tilt.y), ry)
  }
  await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', {type:'mouseMoved',x:20,y:20}); await pause(250)
  await pause(250)
  await shot('library-bookshelf-final.png')
  assert.equal(await execute("document.querySelector('[data-office-library]').textContent.includes('重命名')"),false)
  assert.equal(await execute("document.querySelector('.library-delete').parentElement.className"),'library-book-meta')
  await click('.library-delete')
  await wait(window,"document.body.innerText.includes('确认删除')",'delete confirmation')
  await pause(250); await shot('library-delete-confirmation.png')
  assert.equal(await execute("Boolean(document.querySelector('.library-reader'))"),false,'delete does not open reader')
  await execute("Array.from(document.querySelectorAll('.MuiDialogActions-root button')).find(b=>b.textContent==='取消').click()")
  await wait(window,"!document.body.innerText.includes('确认删除')",'cancel deletion')
  assert.equal(await execute("document.querySelectorAll('[data-library-book]').length"),10)
  const openSettings = async () => {
    await execute("Array.from(document.querySelectorAll('.library-header button')).find(b=>b.textContent==='模型配置').click()")
    await wait(window,"document.querySelector('[aria-label=\"向量模型名称\"]') !== null",'settings loaded')
  }
  const settingsButton = text => execute(`Array.from(document.querySelectorAll('[data-library-settings] .settings-actions button')).find(b=>b.textContent===${JSON.stringify(text)}).click()`)
  await openSettings()
  await pause(300); await shot('library-settings.png')
  for (const kind of ['embedding', 'rerank']) {
    assert.equal(await execute(`document.querySelector('#library-${kind}-key').value`),'','stored secret never returned')
  }
  await setValue('[aria-label="向量模型名称"]','')
  await settingsButton('保存')
  await wait(window,"document.querySelector('[data-library-settings]')?.getAttribute('aria-busy')==='true'",'save busy state')
  assert.equal(await execute("Array.from(document.querySelectorAll('[data-library-settings] input')).every(i=>i.disabled)"),true)
  await wait(window,"document.body.innerText.includes('请填写向量模型名称')",'save failure is actionable')
  assert.equal(await execute("Boolean(document.querySelector('[data-library-settings]'))"),true)
  await setValue('[aria-label="向量模型名称"]','qwen3.7-text-embedding-flash')
  await setValue('#library-embedding-key','fixture-embedding-key')
  await setValue('#library-rerank-key','fixture-rerank-key')
  await click('[aria-label="显示向量密钥"]')
  assert.equal(await execute("document.querySelector('#library-embedding-key').type"),'text')
  assert.equal(await execute("document.querySelector('#library-rerank-key').type"),'password')
  await click('[aria-label="显示精排密钥"]')
  assert.equal(await execute("document.querySelector('#library-rerank-key').type"),'text')
  await settingsButton('保存并测试')
  await wait(window,"document.body.innerText.includes('连接成功，向量 1024 维')",'save and test')
  for (const kind of ['embedding', 'rerank']) {
    assert.equal(await execute(`document.querySelector('#library-${kind}-key').value`),'')
    assert.equal(await execute(`document.querySelector('#library-${kind}-key').type`),'password')
  }
  await settingsButton('保存')
  await wait(window,"!document.querySelector('[data-library-settings]')",'save closes only settings')
  assert.equal(await execute("Boolean(document.querySelector('[data-office-library]'))"),true)
  await click('[aria-label="阅读前端开发规范"]')
  await wait(window, "document.querySelector('[data-book-transition=opening]') !== null", 'real cover opening layer')
  assert.equal(await execute("document.querySelector('.library-flight .library-cover').getAnimations().length"), 1)
  await pause(270); await shot('library-opening.png')
  await ready()
  await wait(window, "document.querySelector('.library-markdown')?.textContent.includes('确认没有无关改动')", 'entire original document, no chapters')
  assert.equal(await execute("document.querySelectorAll('.library-toc,a[download],.library-markdown img,.library-markdown a,.library-markdown script').length"), 0)
  assert.equal(await execute('Boolean(window.libraryInjected)'), false)
  await shot('library-reader.png')
  await back()
  assert.equal(await execute("document.activeElement?.getAttribute('aria-label')"), '阅读前端开发规范')
  await click('[aria-label="阅读参考资料"]'); await ready()
  await wait(window, "document.querySelectorAll('.library-pdf-page').length === 2 && document.querySelector('.library-pdf-page canvas')?.width > 0", 'actual PDF pages rendered')
  await pause(500)
  assert.equal(await execute("document.querySelectorAll('.library-pdf-page [role=alert]').length"), 0)
  const ink = await execute("(() => { const c=document.querySelector('.library-pdf-page canvas');const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<p.length;i+=4)if(p[i]<100&&p[i+3]>200)n++;return n;})()")
  assert.ok(ink > 100, 'PDF has rendered text, not a blank canvas')
  await shot('library-pdf.png'); await back()
  await click('[aria-label="阅读员工手册"]'); await ready()
  await wait(window, "document.querySelector('.library-docx')?.srcdoc.includes('员工手册原文验收')", 'actual DOCX original content')
  await pause(250)
  const iframe = await execute("({sandbox:document.querySelector('.library-docx').getAttribute('sandbox'),source:document.querySelector('.library-docx').srcdoc})")
  assert.equal(iframe.sandbox, ''); assert.ok(!iframe.source.includes('href="https://'))
  const frame = window.webContents.mainFrame.frames.find(frame => frame.url === 'about:srcdoc')
  assert.ok(frame, 'isolated DOCX frame loaded')

  const dom = await window.webContents.debugger.sendCommand('DOMSnapshot.captureSnapshot', {computedStyles:[]})
  assert.ok(dom.strings.some(text => text.includes('实际正文')), 'DOCX text exists in rendered iframe DOM')

  await shot('library-docx.png'); await back()
  await click('[aria-label="阅读个人工作约定"]'); await ready()
  await wait(window, "document.querySelector('.library-prose')?.textContent === '全文内容：个人工作约定'", 'TXT original text')
  window.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'}); window.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'})
  await wait(window, "!document.querySelector('.library-reader')", 'Escape folds book back')
  await setValue(search, '不存在的资料'); await wait(window, "document.body.innerText.includes('没有找到这份资料')", 'search empty state')
  await setValue(search, '前端'); assert.equal(await execute("document.querySelectorAll('[data-library-book]').length"), 1)
  await setValue(search, '')
  await execute("document.querySelector('.library-scroll').scrollTop=100")
  const scrollTop = await execute("document.querySelector('.library-scroll').scrollTop")
  await click('[aria-label="阅读员工手册"]'); await ready(); await back()
  assert.equal(await execute("document.querySelector('.library-scroll').scrollTop"), scrollTop)
  await click('.library-shelf [aria-label="关闭资料库"]')
  await wait(window, "!document.querySelector('[data-office-library]')", 'close')
  await wait(window, "document.activeElement?.hasAttribute('data-office-library-launcher')", 'restore launcher focus')
  await click('[data-office-library-launcher]')
  await wait(window, "document.querySelectorAll('[data-library-book]').length === 10", 'reopen retains files')
  for (const [name, bytes, message] of [['错误.pdf',3,'不是有效的 PDF'],['不支持.exe',3,'仅支持'],['过大.txt',20*1024*1024+1,'超过 20 MB']]) {
    await execute(`(() => {const t=new DataTransfer();t.items.add(new File([new Uint8Array(${bytes})],${JSON.stringify(name)}));const i=document.querySelector('[data-office-library] input[type=file]');i.files=t.files;i.dispatchEvent(new Event('change',{bubbles:true}));})()`)
    await wait(window, `document.body.innerText.includes(${JSON.stringify(message)})`, 'invalid file rejected')
  }
  assert.equal(await execute("document.querySelectorAll('[data-library-book]').length"),10)
  window.webContents.setZoomFactor(1)
  await window.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false}); await pause(300)
  console.log('LIBRARY_VIEWPORT', await execute("({width:innerWidth,grid:getComputedStyle(document.querySelector('.library-grid')).gridTemplateColumns,mobile:matchMedia('(max-width:480px)').matches})"))
  await shot('library-mobile.png')
  assert.equal(await execute("getComputedStyle(document.querySelector('.library-grid')).gridTemplateColumns.split(' ').length"), 2)
  await shot('library-mobile.png')
  await openSettings(); await pause(300)
  assert.equal(await execute("(() => { const r=document.querySelector('[data-library-settings]').getBoundingClientRect(); const a=document.querySelector('.settings-actions').getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && a.bottom<=innerHeight; })()"),true,'mobile modal fits and footer visible')
  await shot('library-settings-mobile.png')
  await click('[aria-label="关闭模型配置"]')
  await wait(window,"!document.querySelector('[data-library-settings]')",'close mobile settings')
  await window.webContents.debugger.sendCommand('Emulation.clearDeviceMetricsOverride')
  window.setSize(1440,960)
  // Emulate the OS reduced-motion preference through Chromium, not app state.

  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]})
  await click('[aria-label="阅读前端开发规范"]'); await ready()
  assert.equal(await execute("document.querySelectorAll('.library-transition').length"), 0)
  await back();
  await click('.library-delete')
  await wait(window,"document.body.innerText.includes('确认删除')",'confirm delete again')
  await execute("Array.from(document.querySelectorAll('.MuiDialogActions-root button')).find(b=>b.textContent==='确认删除').click()")
  await wait(window,"document.querySelectorAll('[data-library-book]').length===9",'confirmed deletion removes one file')
  await click('.library-shelf [aria-label="关闭资料库"]'); await wait(window, "!document.querySelector('[data-office-library]')", 'close before existing panel check')
  await click('[data-office-employee-editor-launcher]')
  await wait(window, "document.querySelector('[data-close-employee-editor]') !== null", 'existing employee panel unaffected')
  await click('[data-close-employee-editor]')
  window.webContents.debugger.detach()
  console.log('OFFICE_LIBRARY_FRONTEND_SMOKE_OK: unified shelf, four-corner tilt, opening/closing, original TXT/MD/PDF/DOCX, no downloads/TOC, safety, errors, search, scroll/focus, mobile, reduced motion, employee panel')
}

exports.pdfFixture = pdfFixture
exports.docxFixture = docxFixture
