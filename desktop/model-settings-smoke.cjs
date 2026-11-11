// Rendered Electron UI assertions. Backend protocol verification lives in pytest.
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
let failAdd = true
let failDefault = true
let failEdit = true
let editingBusy = false
let holdDetail = true
let releaseDetail
let releaseSave
let releaseDefault
let edited
let added
let settings
const labels = { none: '关闭思考', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '极高', max: '最高', enabled: '开启思考', disabled: '关闭思考' }
const levels = {
  openai: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], deepseek: ['low', 'high', 'max'],
  mimo: ['enabled', 'disabled'], kimi: ['low', 'high', 'max'], glm: ['low', 'high', 'max'],
  doubao: ['minimal', 'low', 'medium', 'high'], minimax: ['low', 'medium', 'high', 'xhigh', 'max'],
}
const catalogue = {
  providers: [
    ['openai', 'OpenAI', 'https://api.openai.com/v1'],
    ['deepseek', 'DeepSeek', 'https://api.deepseek.com'],
    ['mimo', 'MiMo', 'https://api.xiaomimimo.com/v1'],
    ['kimi', 'Kimi', 'https://api.moonshot.cn/v1'],
    ['glm', 'GLM', 'https://open.bigmodel.cn/api/paas/v4'],
    ['doubao', '豆包', 'https://ark.cn-beijing.volces.com/api/v3'],
    ['minimax', 'MiniMax', 'https://api.minimax.cn/v1'],
  ].map(([id, name, baseUrl]) => ({ id, name, baseUrl,
    thinkingOptions: levels[id].map(value => ({ value, label: labels[value] })), defaultThinkingLevel: id === 'mimo' ? 'enabled' : 'high' })),
  models: Array.from({ length: 8 }, (_, i) => ({ id: `model-${i}`, provider: 'openai', providerName: 'OpenAI',
    modelName: i === 0 ? 'gpt-existing' : `team-model-${i}`, createdAt: i ? '2026-10-16T03:20:00Z' : null, supportsVision: false, isDefault: i === 0,
    thinkingLevel: 'high', thinkingLabel: '高' })),
}
exports.saved = payload => { settings = payload; return {} }
exports.register = ipc => {
  ipc.handle('office-desktop:listOfficeModels', async () => catalogue)
  ipc.handle('office-desktop:getOfficeModel', async (_e, modelId) => {
    if (holdDetail) await new Promise(resolve => { releaseDetail = resolve })
    return {
    ...catalogue.models.find(model => model.id === modelId), baseUrl: 'https://private.example/v1',
    apiKeyConfigured: true, editable: !editingBusy,
    editReasonCode: editingBusy ? 'errors.an_employee_is_using_this_model_or_waiting_for_a_reply_finish' : '',
    }
  })
  ipc.handle('office-desktop:updateOfficeModel', async (_e, modelId, payload) => {
    edited = payload
    if (failEdit) await new Promise(resolve => { releaseSave = resolve })
    if (failEdit) { failEdit = false; throw new Error('连接超时，请检查网络和 API 地址后重试') }
    Object.assign(catalogue.models.find(model => model.id === modelId), { modelName: payload.modelName, supportsVision: payload.supportsVision,
      thinkingLevel: payload.thinkingLevel, thinkingLabel: labels[payload.thinkingLevel] })
    return catalogue
  })
  ipc.handle('office-desktop:addOfficeModel', async (_e, payload) => {
    added = payload
    await new Promise(resolve => setTimeout(resolve, 150))
    if (failAdd) { failAdd = false; throw new Error('认证或权限不足，请检查 API Key 与模型访问权限') }
    catalogue.models.push({ id: 'new-model', provider: payload.provider, providerName: 'DeepSeek', modelName: payload.modelName,
      createdAt: new Date().toISOString(), supportsVision: payload.supportsVision, isDefault: false,
      thinkingLevel: payload.thinkingLevel, thinkingLabel: labels[payload.thinkingLevel] })
    return catalogue
  })
  ipc.handle('office-desktop:setOfficeDefaultModel', async (_e, modelId) => {
    if (failDefault) await new Promise(resolve => { releaseDefault = resolve })
    if (failDefault) { failDefault = false; throw new Error('默认模型切换失败，已保留原设置') }
    catalogue.models.forEach(model => { model.isDefault = model.id === modelId })
    return catalogue
  })
}
exports.run = async (window, wait, setValue) => {
  const evaluate = text => window.webContents.executeJavaScript(text)
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`)
  const chooseThinking = async value => {
    await evaluate(`document.querySelector('[data-office-thinking-select] [role="button"]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}))`)
    await wait(window, `document.querySelector('[data-office-thinking-option="${value}"]')`, 'thinking menu')
    await click(`[data-office-thinking-option="${value}"]`)
    await wait(window, `!document.querySelector('[role="listbox"]')`, 'thinking chosen')
  }
  const mouseClick = async selector => {
    const point = await evaluate(`(() => { const rect = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) } })()`)
    window.webContents.sendInputEvent({ type: 'mouseMove', ...point })
    window.webContents.sendInputEvent({ type: 'mouseDown', ...point, button: 'left', clickCount: 1 })
    window.webContents.sendInputEvent({ type: 'mouseUp', ...point, button: 'left', clickCount: 1 })
  }
  const capture = async name => {
    window.webContents.invalidate()
    await new Promise(resolve => setTimeout(resolve, 400))
    const folder = path.resolve(__dirname, '..', 'output', 'model-settings')
    await fs.mkdir(folder, { recursive: true })
    await fs.writeFile(path.join(folder, name + '.png'), (await window.webContents.capturePage()).toPNG())
  }
  await wait(window, `document.querySelectorAll('[data-office-model-list] tbody tr').length === 8`, 'model list')
  assert.equal(await evaluate(`(()=>{const el=document.querySelector('[data-office-model-list]');return el.scrollHeight>el.clientHeight && getComputedStyle(el).height==='242px'})()`), true)
  await setValue('[data-office-settings-dialog] input', '尚未保存的办公室名称')
  await setValue('[data-office-settings-dialog] textarea', '回答简短一些')
  await capture('catalogue')
  const editSelector = '[data-office-model-edit="model-0"]'
  window.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 })
  await wait(window, `getComputedStyle(document.querySelector('${editSelector}')).opacity === '0'`, 'edit hidden at rest')
  const point = await evaluate(`(()=>{const r=document.querySelector('[data-office-model-row="model-0"]').getBoundingClientRect();return {x:Math.round(r.x+10),y:Math.round(r.y+10)}})()`)
  window.webContents.sendInputEvent({ type: 'mouseMove', ...point })
  await wait(window, `getComputedStyle(document.querySelector('${editSelector}')).opacity === '1'`, 'edit visible on row hover')
  await capture('edit-hover')
  window.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 })
  await evaluate(`document.querySelector('[data-office-model-list]').focus()`)
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Tab' })
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Tab' })
  await wait(window, `document.activeElement === document.querySelector('${editSelector}') && getComputedStyle(document.activeElement).opacity === '1'`, 'keyboard Tab reveals edit')
  const rowAppearance = () => evaluate(`JSON.stringify([...document.querySelectorAll('[data-office-model-row]')].slice(1, 8).map(row => {
    const edit = getComputedStyle(row.querySelector('[data-office-model-edit]'));
    const button = getComputedStyle(row.querySelector('[data-office-model-default]'));
    return { editOpacity: edit.opacity, color: button.color, background: button.backgroundColor, opacity: button.opacity };
  }))`)
  const idleAppearance = await rowAppearance()
  await mouseClick(editSelector)
  await wait(window, `document.querySelector('#office-add-model-title')?.textContent === '编辑模型'`, 'edit dialog')
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('${editSelector}')).opacity`), '0', 'opened dialog hides the clicked edit action')
  assert.equal(await rowAppearance(), idleAppearance, 'opening edit must not reveal other edit buttons or recolor global-default actions')
  assert.equal(await evaluate(`document.querySelector('[data-office-model-submit]').disabled`), true)
  assert.equal(await evaluate(`document.querySelector('${editSelector}').textContent`), '编辑')
  assert.equal(await evaluate(`document.querySelector('[data-office-add-model-dialog]').textContent.includes('连接中')`), false)
  assert.equal(typeof releaseDetail, 'function', 'dialog opened before detail response')
  await mouseClick('[aria-label="关闭编辑模型"]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]')`, 'can close while reading')
  window.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 })
  await wait(window, `getComputedStyle(document.querySelector('${editSelector}')).opacity === '0'`, 'mouse focus must not keep edit visible after close')
  const nextRowPoint = await evaluate(`(() => { const r = document.querySelector('[data-office-model-row="model-1"]').getBoundingClientRect(); return {x:Math.round(r.x+10),y:Math.round(r.y+10)} })()`)
  window.webContents.sendInputEvent({ type: 'mouseMove', ...nextRowPoint })
  await wait(window, `[...document.querySelectorAll('[data-office-model-edit]')].filter(el => getComputedStyle(el).opacity === '1').map(el => el.getAttribute('data-office-model-edit')).join() === 'model-1'`, 'only newly hovered row shows edit')
  window.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 })
  releaseDetail()
  holdDetail = false
  await new Promise(resolve => setTimeout(resolve, 150))
  assert.equal(await evaluate(`document.querySelector('[data-office-add-model-dialog]') === null`), true, 'late response does not reopen dialog')
  await click(editSelector)
  await wait(window, `document.querySelector('[data-office-add-model-dialog] form')?.getAttribute('aria-busy') === 'false'`, 'details loaded')
  assert.equal(await evaluate(`document.querySelector('input[name="modelName"]').value`), 'gpt-existing')
  assert.equal(await evaluate(`document.querySelector('input[name="baseUrl"]').value`), 'https://private.example/v1')
  assert.equal(await evaluate(`document.querySelector('input[name="apiKey"]').value`), '')
  assert.equal(await evaluate(`document.querySelector('[aria-label="返回选择模型商"]') === null`), true)
  await capture('edit-form')
  await setValue('input[name="modelName"]', 'cancelled-draft')
  await click('[aria-label="关闭编辑模型"]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]')`, 'edit cancel')
  assert.equal(catalogue.models[0].modelName, 'gpt-existing')
  await click(editSelector)
  await wait(window, `document.querySelector('input[name="modelName"]')?.value === 'gpt-existing' && !document.querySelector('input[name="modelName"]').disabled`, 'cancelled draft discarded')
  await setValue('input[name="modelName"]', 'gpt-updated')
  await chooseThinking('xhigh')
  await click('[data-office-model-submit]')
  await wait(window, `document.querySelector('[data-office-model-submit]')?.textContent === '保存中…'`, 'save pending')
  await capture('save-pending')
  const pendingSelect = await evaluate(`(() => {
    const field = document.querySelector('[data-office-thinking-select]');
    const option = field.querySelector('.MuiSelect-select');
    const selected = getComputedStyle(option);
    const border = getComputedStyle(field.querySelector('fieldset'));
    const luminance = color => {
      const [r,g,b] = color.match(/[0-9.]+/g).slice(0,3).map(value => {
        const c = Number(value) / 255;
        return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
      });
      return .2126*r + .7152*g + .0722*b;
    };
    const text = luminance(selected.webkitTextFillColor);
    const background = luminance(getComputedStyle(field).backgroundColor);
    return { label: option.textContent, disabled: option.getAttribute('aria-disabled'),
      height: field.getBoundingClientRect().height,
      contrast: (Math.max(text,background)+.05)/(Math.min(text,background)+.05),
      borderVisible: border.borderTopWidth !== '0px' && border.borderTopStyle !== 'none' };
  })()`)
  assert.equal(pendingSelect.label, '极高', 'saving preserves the selected value')
  assert.equal(pendingSelect.disabled, 'true')
  assert.ok(pendingSelect.height > 30 && pendingSelect.borderVisible, 'disabled dropdown retains its frame')
  assert.ok(pendingSelect.contrast >= 3, 'disabled selection stays readable on the light dialog')
  assert.equal(await rowAppearance(), idleAppearance, 'saving edit must not change other rows')
  assert.equal(typeof releaseSave, 'function')
  releaseSave()
  await wait(window, `document.body.innerText.includes('保存失败：连接超时')`, 'edit failure toast with reason')
  assert.equal(await evaluate(`document.querySelector('input[name="modelName"]').value`), 'gpt-updated')
  await click('[data-office-model-submit]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]') && document.body.innerText.includes('保存成功')`, 'edit saved')
  assert.equal(edited.apiKey, '')
  assert.equal(edited.thinkingLevel, 'xhigh')
  assert.equal(await evaluate(`document.querySelector('[data-office-model-thinking="model-0"]').textContent`), '极高')
  assert.equal(catalogue.models.length, 8)
  assert.equal(catalogue.models[0].isDefault, true)
  assert.equal(catalogue.models[0].createdAt, null)
  assert.equal(await evaluate(`document.querySelector('[data-office-settings-dialog] input').value`), '尚未保存的办公室名称')
  editingBusy = true
  await click(editSelector)
  await wait(window, `document.querySelector('input[name="modelName"]')?.value === 'gpt-updated' && document.querySelector('[data-office-add-model-dialog] form')?.getAttribute('aria-busy') === 'false'`, 'edit reload persisted value')
  assert.equal(await evaluate(`document.querySelector('input[name="modelName"]').disabled && document.querySelector('[data-office-model-submit]').disabled`), true)
  assert.equal(await evaluate(`document.querySelector('[data-office-thinking-select] [role="button"]').getAttribute('aria-disabled')`), 'true')
  assert.equal(await evaluate(`document.querySelector('[data-office-add-model-dialog]').textContent.includes('等待回复')`), true)
  await click('[aria-label="关闭编辑模型"]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]')`, 'busy model can close')
  editingBusy = false
  await click('[data-office-model-add]')
  await wait(window, `document.querySelectorAll('[data-office-provider]').length === 7`, 'seven providers')
  await capture('providers')
  await click('[data-office-provider="deepseek"]')
  await wait(window, `document.querySelector('input[name="baseUrl"]')?.value === 'https://api.deepseek.com'`, 'official address')
  await setValue('input[name="modelName"]', 'deepseek-chat')
  await setValue('input[name="baseUrl"]', 'https://private.example/v1')
  await setValue('input[name="apiKey"]', 'ui-test-secret')
  await chooseThinking('max')
  await click('[aria-label="显示 API Key"]')
  assert.equal(await evaluate(`document.querySelector('input[name="apiKey"]').type`), 'text')
  await click('[aria-label="隐藏 API Key"]')
  await click('input[name="vision"]')
  await capture('add-form')
  await click('[data-office-model-submit]')
  await wait(window, `document.body.innerText.includes('添加失败：认证或权限不足')`, 'failure toast includes reason')
  assert.equal(await evaluate(`document.querySelector('input[name="apiKey"]').value`), 'ui-test-secret')
  assert.equal(await evaluate(`document.querySelector('[data-office-add-model-dialog] [role="alert"]') === null`), true)
  assert.equal(await evaluate(`document.querySelector('[data-office-settings-dialog]') !== null`), true)
  await click('[data-office-model-submit]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]') && document.querySelector('[data-office-model-default="new-model"]')`, 'only child closes on success')
  assert.equal(added.supportsVision, true)
  assert.equal(added.thinkingLevel, 'max')
  assert.equal(await evaluate(`document.querySelector('[data-office-model-thinking="new-model"]').textContent`), '最高')
  assert.equal(added.baseUrl, 'https://private.example/v1')
  assert.equal(await evaluate(`document.querySelector('[data-office-settings-dialog] input').value`), '尚未保存的办公室名称')
  assert.equal(await evaluate(`document.querySelector('[data-office-settings-dialog] textarea').value`), '回答简短一些')
  assert.equal(await evaluate(`document.querySelector('[data-office-model-default="model-0"]').disabled`), true)
  await click('[data-office-model-default="new-model"]')
  await wait(window, `document.querySelector('[data-office-model-default="new-model"]')?.textContent === '切换中…'`, 'default switch pending')
  assert.equal(await rowAppearance(), idleAppearance, 'temporarily disabled rows retain hidden edits and default colors')
  assert.equal(typeof releaseDefault, 'function')
  releaseDefault()
  await wait(window, `document.body.innerText.includes('切换失败：默认模型切换失败')`, 'switch failure toast')
  assert.equal(await evaluate(`document.querySelector('[data-office-model-default="model-0"]').textContent`), '当前默认')
  await click('[data-office-model-default="new-model"]')
  await wait(window, `document.querySelector('[data-office-model-default="new-model"]').textContent === '当前默认'`, 'default changes')
  await click('[data-office-model-add]')
  await wait(window, `document.querySelectorAll('[data-office-provider]').length === 7`, 'reopen clean chooser')
  await click('[data-office-provider="kimi"]')
  await wait(window, `document.querySelector('input[name="apiKey"]')?.value === ''`, 'secret cleared')
  await click('[aria-label="返回选择模型商"]')
  await click('[data-office-provider="mimo"]')
  await wait(window, `document.querySelector('[data-office-thinking-switch]')?.checked === true`, 'MiMo defaults on')
  assert.equal(await evaluate(`document.querySelector('[data-office-thinking-select]') === null`), true)
  await click('[data-office-thinking-switch]')
  assert.equal(await evaluate(`document.querySelector('[data-office-thinking-switch]').checked`), false)
  await click('[aria-label="返回选择模型商"]')
  await click('[data-office-provider="deepseek"]')
  await wait(window, `document.querySelector('[data-office-thinking-select]')?.textContent.includes('高')`, 'provider switch resets thinking')
  await capture('thinking-form')
  await click('[aria-label="关闭添加模型"]')
  await wait(window, `!document.querySelector('[data-office-add-model-dialog]')`, 'child close')
  await evaluate(`[...document.querySelectorAll('[data-office-settings-dialog] button')].find(b => b.textContent === '保存设置').click()`)
  await wait(window, `!document.querySelector('[data-office-settings-dialog]')`, 'outer saved')
  assert.deepEqual(settings, { companyName: '尚未保存的办公室名称', terminalAccessMode: 'approval', confirmFullAccess: false })
  console.log('MODEL_SETTINGS_UI_OK: hover/focus edit, prefill, blank secret, cancel, save/error, busy guard, scrolling, seven providers, add, default, draft preservation')
}
