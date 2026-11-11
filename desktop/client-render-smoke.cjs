const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs/promises')
const { app, BrowserWindow, ipcMain } = require('electron')

const rendererPath = process.env.OFFICE_CLIENT_TEST_RENDERER || path.resolve(__dirname, '..', 'client', 'dist', 'index.html')
const preloadPath = path.join(__dirname, 'preload.cjs')
let initialized = false
let selectedToolNames = []
let availableSkillNames = ['find-skills', 'custom-installed']
let workflowFixture = null
let queuedAutoStatus = null
let groupPauseCalls = 0
let nativeRunTargets = []
let nativeWorkActive = false
let nativeRunReads = 0
let sentOfficeMessage = null
let groupMessageReads = 0
let groupReadDelay = 0
let failGroupRead = false
let workflowReadDelay = 0
let workspaceFixture
let workspaceSelection = 'cancel'
let bootstrapModelTests = 0
let rejectLanguageSave = false
let sendDelay = 0
const deliverySmoke = process.argv.includes('--chat-delivery')
const refreshSmoke = process.argv.includes('--refresh-isolation')
const modelSmoke = process.argv.includes('--model-settings')
const modelScenario = require('./model-settings-smoke.cjs')
let refreshActive = false
let refreshQueueFails = false
let deferQueueSnapshot = null
let refreshFrontFails = false
let refreshFrontDelay = 0
let refreshBackDone = false
let refreshFrontDone = false
let refreshRunListFails = false
const refreshInFlight = new Map()
const refreshMaxInFlight = new Map()
async function refreshTracked(key, action) {
  const count = (refreshInFlight.get(key) || 0) + 1
  refreshInFlight.set(key, count)
  refreshMaxInFlight.set(key, Math.max(refreshMaxInFlight.get(key) || 0, count))
  try { return await action() } finally { refreshInFlight.set(key, count - 1) }
}

let deliveryCalls = 0
let deliveryEvents = 0
let deliveryFailure = false
let deliveryStoredBeforeFailure = false
let deliveryRunStatus = 'success'
let deliveryPrivateDelay = 0
let slowRunList = false
let captureNextGroupRead = false
let capturedGroupReads = 0
let groupAfterSequences = []
let groupRunReads = []
let runEventAfterSequences = []
let groupReplyAdded = false
let multiRunActive = false
let multiCompletionStage = 0
let multiBackFinalizingReads = 0
let multiFrontReplyAdded = false
let multiBackReplyAdded = false
let terminalRunActive = false
let terminalRunTime = ''
let cancelRunActive = false
let cancelRunCalled = false
let clarificationRunActive = false
let clarificationResumeCalled = false
let terminalStatusRuns = {
  'terminal-error-run': 'error',
  'terminal-timeout-run': 'timeout',
  'terminal-interrupted-run': 'interrupted',
}
let privateListRuns = 0
let privateStatusReads = 0
let privateCompletionAllowed = false
let privateReplyAdded = false
let privateReplyRunId = 'private-run-one'
let privateSentOfficeMessage = null
let privateMessages = []
let queueItems = [
  { queueId: 'queue-one', targetEmployeeId: 'backend-one', sourceKind: 'direct', conversationKind: 'group', text: '原始排队任务', enqueuedAt: Date.now() },
  { queueId: 'queue-two', targetEmployeeId: 'research-one', sourceKind: 'pm_delegate', conversationKind: 'group', text: 'PM 指派的排队任务', enqueuedAt: Date.now() + 1 },
]
let employeePersonalInfo = { displayName: '周礼', prompt: '员工个人 Prompt', skills: ['find-skills'], modelProfileId: 'model-0' }
let employeeState = 'idle'
let savedPersonalInfo = null
let rejectPersonalSave = false
let deletedEmployeeId = null
let revealedFile = null
const richReply = 'AI 返回完成\n\n老板，**聊天展示已优化**，工具输出不再挤占正文。\n\n### 本次调整\n- 人在右侧，AI 在左侧\n- 命令只展示，不执行\n\n```typescript\nconst send = () => "hello office"\n```\n\n[项目调整设计文档](docs/design.md)\n\n<script>window.chatInjected = true</script>'
let groupMessages = [{
  messageId: 'group-history-one',
  sequence: 1,
  senderKind: 'user',
  senderId: 'user-a',
  senderDisplayName: '你',
  senderAvatar: '',
  content: '历史群聊消息',
  mentionEmployeeIds: [],
  sourceRunId: null,
  createdAt: Date.now(),
}]

function registerSmokeIpc() {
  const libraryDocuments = new Map()
  let librarySettings = {embedding_model:'qwen3.7-text-embedding-flash',embedding_url:'https://maas.qianwenaiapi.com/compatible-mode/v1',rerank_model:'qwen3.7-text-rerank',rerank_url:'https://maas.qianwenaiapi.com/api/v1/services/rerank/text-rerank/text-rerank',embeddingApiKeyConfigured:true,rerankApiKeyConfigured:true}
  ipcMain.handle('office-desktop:library', async (_event, payload) => {
    if (payload.action === 'settings') return librarySettings
    if (payload.action === 'saveSettings') {
      await new Promise(resolve=>setTimeout(resolve,350))
      if (!payload.body.embedding_model.trim()) throw new Error('请填写向量模型名称')
      const {embedding_api_key,rerank_api_key,...settings}=payload.body
      librarySettings={...settings,embeddingApiKeyConfigured:librarySettings.embeddingApiKeyConfigured || Boolean(embedding_api_key),rerankApiKeyConfigured:librarySettings.rerankApiKeyConfigured || Boolean(rerank_api_key)}
      return librarySettings
    }
    if (payload.action === 'test') return {ok:true,dimensions:1024}
    if (payload.action === 'list') return { documents: [...libraryDocuments.values()].map(({content,...metadata})=>metadata) }
    if (payload.action === 'upload') {
      const {filename,content,replace_id,expected_version} = payload.body
      const existing = [...libraryDocuments.values()].find(item=>item.filename===filename)
      if (existing && (existing.id!==replace_id || existing.version_id!==expected_version)) return {conflict:existing}
      if (filename.endsWith('.pdf') && !Buffer.from(content,'base64').subarray(0,5).equals(Buffer.from('%PDF-'))) throw new Error('不是有效的 PDF 文件')
      const id=existing?.id || require('node:crypto').randomBytes(16).toString('hex')
      const version_id=require('node:crypto').randomBytes(16).toString('hex')
      libraryDocuments.set(id,{id,filename,content,display_name:filename.replace(/\.[^.]+$/,''),size:Buffer.from(content,'base64').length,
        extension:require('node:path').extname(filename),status:'ready',error:'',version_id,active:version_id,pending:null})
      return {id,version_id}
    }
    if (payload.action === 'file') return libraryDocuments.get(payload.id)
    if (payload.action === 'delete') {libraryDocuments.delete(payload.id);return {ok:true}}
    if (payload.action === 'retry') return {ok:true}
    throw new Error('Unsupported fixture action')
  })
  modelScenario.register(ipcMain)
  let language = 'zh-CN'
  ipcMain.handle('office-desktop:getLanguage', () => language)
  ipcMain.handle('office-desktop:setLanguage', (_event, next) => { if (rejectLanguageSave) throw new Error('Language preference could not be saved'); language = next; return next })
  ipcMain.handle('office-desktop:previewOfficeFile', async (_event, payload) => ({
    path: 'D:/smoke/docs/design.md', name: 'design.md', size: 12, kind: 'text', language: 'markdown',
    ...(payload.metadataOnly ? {} : { content: '# 设计文档\n只读预览' }),
  }))
  ipcMain.handle('office-desktop:openExternalUrl', async () => {})
  ipcMain.handle('office-desktop:revealOfficeFile', async (_event, payload) => {
    revealedFile = payload
    return { path: 'D:/smoke/docs/design.md' }
  })
  ipcMain.handle('office-desktop:getOfficeBootstrap', async () => initialized
    ? { initialized: true, companyName: '烟测办公室', defaultModel: { name: 'office-default', model: 'mimo-v2.5', displayName: 'mimo-v2.5' }, baseUrl: 'https://example.com/v1', apiKeyConfigured: true }
    : { initialized: false, companyName: null, defaultModel: null, baseUrl: null, apiKeyConfigured: false })
  ipcMain.handle('office-desktop:getOfficeSettings', async () => ({
    initialized: true,
    companyName: '烟测办公室',
    defaultModel: { name: 'office-default', model: 'mimo-v2.5', displayName: 'mimo-v2.5' },
    baseUrl: 'https://example.com/v1',
    apiKeyConfigured: true,
  }))
  ipcMain.handle('office-desktop:saveOfficeSettings', async (_event, payload) => ({
    ...modelScenario.saved(payload),
    initialized: true,
    companyName: payload.companyName,
    defaultModel: { name: 'office-default', model: payload.modelName, displayName: payload.modelName },
    baseUrl: payload.baseUrl,
    apiKeyConfigured: true,
  }))
  ipcMain.handle('office-desktop:getUserMemory', async () => ({ facts: [] }))
  ipcMain.handle('office-desktop:createMemoryFact', async (_event, payload) => ({
    facts: [{ id: 'user-preference-one', content: payload.content, category: payload.category }],
  }))
  ipcMain.handle('office-desktop:updateMemoryFact', async (_event, factId, payload) => ({
    facts: [{ id: factId, content: payload.content, category: payload.category || 'user_preference' }],
  }))
  ipcMain.handle('office-desktop:deleteMemoryFact', async () => ({ facts: [] }))
  ipcMain.handle('office-desktop:testOfficeBootstrapModel', async () => {
    bootstrapModelTests += 1
    return { name: 'office-default', model: 'mimo-v2.5', displayName: 'mimo-v2.5' }
  })
  ipcMain.handle('office-desktop:initializeOfficeBootstrap', async () => {
    assert.ok(workspaceFixture)
    initialized = true
    return { initialized: true, companyName: '烟测办公室', defaultModel: { name: 'office-default', model: 'mimo-v2.5', displayName: 'mimo-v2.5' }, baseUrl: 'https://example.com/v1', apiKeyConfigured: true }
  })
  ipcMain.handle('office-desktop:getServiceStatus', async () => ({
    mode: 'electron', lifecycle: 'connected', detail: 'Smoke 服务器已连接', transport: 'http', lastUpdatedAt: Date.now(),
  }))
  ipcMain.handle('office-desktop:pingService', async () => ({ pong: true, timestamp: Date.now() }))
  ipcMain.handle('office-desktop:getWorkspaceSummary', async () => ({ workspace: workspaceFixture }))
  ipcMain.handle('office-desktop:selectAndBindWorkspace', async () => {
    if (workspaceSelection === 'cancel') return { canceled: true }
    if (workspaceSelection === 'error') throw new Error('目录无法读取，请重新选择')
    workspaceFixture = { projectId: 'project-bootstrap', path: 'D:/测试/我的工作区', name: '我的工作区', kind: 'empty', tree: [] }
    return { canceled: false, workspace: workspaceFixture }
  })
  ipcMain.handle('office-desktop:getOfficeEmployee', async (_event, employeeId) => ({
    employee: { id: employeeId, role: employeeId === 'pm-one' ? '项目经理' : '前端工程师' },
    personalInfo: employeeId === 'frontend-one'
      ? employeePersonalInfo
      : { displayName: '黎明', prompt: '研究员 Prompt', skills: ['find-skills'], modelProfileId: 'model-0' },
    effectivePackage: {
      agentId: employeeId,
      displayName: employeeId === 'frontend-one' ? employeePersonalInfo.displayName : '黎明',
      agentKind: 'employee',
      canDelegate: false,
      capabilities: ['前端开发'],
      tools: ['file:read', 'file:write', 'web'],
      toolGroupMembers: {
        'file:read': ['ls', 'read_file', 'glob', 'grep'],
        'file:write': ['apply_patch'],
        web: ['web_search', 'web_fetch', 'image_search'],
      },
      permissions: ['workspace:read'],
      ownedDeliverables: [],
    },
    installedSkills: [{ skillId: 'find-skills', enabled: true }],
    authorizedTools: [{ toolId: 'file:read', enabled: true }, { toolId: 'file:write', enabled: true }],
    capabilityIndex: {
      effectiveCapabilities: ['前端开发'], effectiveTools: ['file:read', 'file:write'], effectivePermissions: ['workspace:read'],
    },
  }))
  ipcMain.handle('office-desktop:listExecutionApprovals', async () => ({ items: [] }))
  ipcMain.handle('office-desktop:decideExecutionApproval', async () => { throw new Error('No pending fixture approval') })
  selectedToolNames = ['read_file', 'apply_patch', 'publish_task_result', 'report_to_pm', 'ask_clarification', 'mcp/offline/custom', 'web_fetch']
  const fixtureToolContract = () => ({ tools: selectedToolNames, revision: 'fixture-revision', requiredTools: ['read_file', 'report_to_pm'], availableTools: selectedToolNames, unavailableTools: {},
    choices: [['read_file', '读取文件'], ['apply_patch', '编辑文件'], ['web_search', '网络搜索'], ['search_office_library', '办公室资料库'], ['powershell_terminal', '终端控制']].map(([id, label]) => ({ id, label, description: label,
      members: id === 'read_file' ? ['read_file', 'ls', 'glob', 'grep'] : id === 'web_search' ? ['web_search', 'web_fetch', 'image_search'] : [id],
      required: id === 'read_file', unavailableReason: '' })) })
  ipcMain.handle('office-desktop:getOfficeEmployeeTools', async () => fixtureToolContract())
  ipcMain.handle('office-desktop:saveOfficeEmployeeTools', async (_event, id, payload) => {
    if (employeeState !== 'idle') throw new Error('Employee is working')
    assert.equal(payload.expectedRevision, 'fixture-revision')
    selectedToolNames = payload.tools
    return fixtureToolContract()
  })
  ipcMain.handle('office-desktop:saveOfficeEmployeePersonalInfo', async (_event, employeeId, payload) => {
    assert.equal(employeeId, 'frontend-one')
    assert.deepEqual(Object.keys(payload).sort(), ['displayName', 'modelProfileId', 'prompt', 'skills'])
    if (rejectPersonalSave) throw new Error('保存失败测试')
    savedPersonalInfo = payload
    employeePersonalInfo = payload
    return { employee: { id: employeeId, ...payload, state: employeeState } }
  })
  ipcMain.handle('office-desktop:deleteOfficeEmployee', async (_event, employeeId) => {
    assert.equal(employeeId, 'research-one')
    deletedEmployeeId = employeeId
    return { deleted: { id: employeeId, workstationId: 'workstation-04' } }
  })
  ipcMain.handle('office-desktop:listOfficeEmployees', async () => ({ employees: [
    { id: 'pm-one', templateId: 'pm', role: '项目经理', displayName: '项目经理', avatar: '', modelProfileId: null, workstationId: 'workstation-01', isPm: true, state: 'idle', statusDetail: '等待新任务', lastReport: '暂无报告', currentTaskId: null, enabled: true, disabledReason: '', createdAt: 1, updatedAt: 1, canDelegate: true },
    { id: 'frontend-one', templateId: 'frontend-engineer', role: '前端工程师', displayName: employeePersonalInfo.displayName, avatar: 'ash', modelProfileId: null, workstationId: 'workstation-02', isPm: false, state: employeeState, statusDetail: '等待新任务', lastReport: '暂无报告', currentTaskId: null, enabled: true, disabledReason: '', createdAt: 1, updatedAt: 1, canDelegate: false },
    { id: 'backend-one', templateId: 'backend-engineer', role: '后端工程师', displayName: '孙河', avatar: '', modelProfileId: null, workstationId: 'workstation-03', isPm: false, state: 'idle', statusDetail: '等待新任务', lastReport: '暂无报告', currentTaskId: null, enabled: true, disabledReason: '', createdAt: 1, updatedAt: 1, canDelegate: false },
    { id: 'research-one', templateId: 'research-documentation', role: '研究员', displayName: '黎明', avatar: '', modelProfileId: null, workstationId: 'workstation-04', isPm: false, state: 'idle', statusDetail: '等待新任务', lastReport: '暂无报告', currentTaskId: null, enabled: true, disabledReason: '', createdAt: 1, updatedAt: 1, canDelegate: false },
  ].filter((employee) => employee.id !== deletedEmployeeId) }))
  ipcMain.handle('office-desktop:listOfficeEmployeeTemplates', async () => ({ templates: [
    { templateId: 'frontend-engineer', role: '前端工程师', category: '软件开发', tags: ['前端开发'], specialties: ['React'], prompt: '前端工程师 Prompt', skills: ['find-skills'] },
  ] }))
  ipcMain.handle('office-desktop:listModels', async () => ({ models: [] }))
  ipcMain.handle('office-desktop:listSkills', async () => ({ skills: [
    { name: 'find-skills', description: 'Find skills', category: 'public', enabled: true },
    { name: 'custom-installed', description: 'User installed', category: 'custom', enabled: true },
    { name: 'newly-installed', description: 'Installed during this session', category: 'custom', enabled: true },
  ].filter(skill => availableSkillNames.includes(skill.name)) }))
  ipcMain.handle('office-desktop:sendOfficeMessage', async (_event, command) => {
    if (command.text === '继续执行' && groupPaused) {
      groupPaused = false
      nativeWorkActive = true
      return { messageId: command.messageId, conversationKind: 'group', triggerEmployeeIds: [], preselectedEmployeeIds: [], dispatches: [] }
    }

    if (sendDelay) await new Promise(resolve => setTimeout(resolve, sendDelay))
    sentOfficeMessage = command
    if (refreshSmoke) {
      refreshActive = true
      groupMessages.push({ messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: command.mentionEmployeeIds, sourceRunId: null, createdAt: Date.now() })
      return { messageId: command.messageId, conversationKind: 'group', dispatches: [
        { employeeId: 'frontend-one', started: true, runId: 'isolation-front', queueId: null },
        { employeeId: 'backend-one', started: true, runId: 'isolation-back', queueId: null },
      ] }
    }
    if (deliverySmoke) {
      deliveryCalls += 1
      if (!deliveryFailure || deliveryStoredBeforeFailure) {
        if (command.conversationKind === 'group') groupMessages.push({
          messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a',
          senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: [], sourceRunId: null, createdAt: Date.now(),
        })
        else privateMessages.push({ messageId: `native-${deliveryCalls}__user`, sourceMessageId: command.messageId, role: 'user', content: command.text, runId: null, createdAt: new Date().toISOString() })
      }
      if (deliveryFailure) throw new Error('模拟发送响应失败')
      return { messageId: command.messageId, conversationKind: command.conversationKind, dispatches: [] }
    }
    if (process.argv.includes('--queue-visibility') && command.conversationKind === 'private') {
      queueItems.push({ queueId: 'private-pending', targetEmployeeId: command.targetEmployeeId, sourceKind: 'direct', conversationKind: 'private', text: command.text, enqueuedAt: Date.now() })
      return { messageId: command.messageId, conversationKind: 'private', dispatches: [{ employeeId: command.targetEmployeeId, queueId: 'private-pending', started: false, runId: null }] }
    }
    if (command.text === '@周礼 排队自动启动测试') {
      queueItems.push({queueId:'queued-auto',targetEmployeeId:'frontend-one',sourceKind:'direct',conversationKind:'group',text:command.text,enqueuedAt:Date.now()})
      setTimeout(() => { queueItems = queueItems.filter(q=>q.queueId!=='queued-auto'); queuedAutoStatus='running' }, 1200)
      setTimeout(() => { queuedAutoStatus='success' }, 3100)
      return {messageId:command.messageId,conversationKind:'group',dispatches:[{employeeId:'frontend-one',queueId:'queued-auto',started:false,runId:null}]}
    }
    if (command.conversationKind === 'private') {
      privateSentOfficeMessage = command
      privateStatusReads = 0
      privateReplyAdded = false
      privateReplyRunId = 'private-run-two'
      privateMessages = [{
        messageId: command.messageId,
        runId: privateReplyRunId,
        role: 'user',
        content: command.text,
        createdAt: new Date().toISOString(),
      }]
      return {
        messageId: command.messageId,
        conversationKind: command.conversationKind,
        triggerEmployeeIds: [command.targetEmployeeId],
        preselectedEmployeeIds: [],
        dispatches: [{ employeeId: command.targetEmployeeId, queueId: null, started: true, runId: privateReplyRunId }],
      }
    }
    if (command.text.endsWith('取消测试')) {
      cancelRunActive = true
      groupMessages = [...groupMessages, {
        messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: command.mentionEmployeeIds, sourceRunId: null, createdAt: Date.now(),
      }]
      return {
        messageId: command.messageId,
        conversationKind: command.conversationKind,
        triggerEmployeeIds: command.mentionEmployeeIds,
        preselectedEmployeeIds: command.mentionEmployeeIds,
        dispatches: [{ employeeId: 'frontend-one', queueId: null, started: true, runId: 'cancel-run' }],
      }
    }
    if (command.text.endsWith('澄清测试')) {
      clarificationRunActive = true
      groupMessages = [...groupMessages, {
        messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: command.mentionEmployeeIds, sourceRunId: null, createdAt: Date.now(),
      }]
      return {
        messageId: command.messageId,
        conversationKind: command.conversationKind,
        triggerEmployeeIds: command.mentionEmployeeIds,
        preselectedEmployeeIds: command.mentionEmployeeIds,
        dispatches: [{ employeeId: 'frontend-one', queueId: null, started: true, runId: 'clarify-run' }],
      }
    }
    if (command.text.endsWith('多员工终态')) {
      terminalRunActive = true
      terminalRunTime = new Date().toISOString()
      groupMessages = [...groupMessages, {
        messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: command.mentionEmployeeIds, sourceRunId: null, createdAt: Date.now(),
      }]
      return {
        messageId: command.messageId,
        conversationKind: command.conversationKind,
        triggerEmployeeIds: command.mentionEmployeeIds,
        preselectedEmployeeIds: command.mentionEmployeeIds,
        dispatches: [
          { employeeId: 'frontend-one', queueId: null, started: true, runId: 'terminal-error-run' },
          { employeeId: 'backend-one', queueId: null, started: true, runId: 'terminal-timeout-run' },
          { employeeId: 'research-one', queueId: null, started: true, runId: 'terminal-interrupted-run' },
        ],
      }
    }
    if (command.mentionEmployeeIds.includes('frontend-one') && command.mentionEmployeeIds.includes('backend-one')) {
      multiRunActive = true
      multiCompletionStage = 0
      multiBackFinalizingReads = 0
      multiFrontReplyAdded = false
      multiBackReplyAdded = false
      groupMessages = [...groupMessages, {
        messageId: command.messageId, sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: command.text, mentionEmployeeIds: command.mentionEmployeeIds, sourceRunId: null, createdAt: Date.now(),
      }]
      return {
        messageId: command.messageId,
        conversationKind: command.conversationKind,
        triggerEmployeeIds: ['frontend-one', 'backend-one'],
        preselectedEmployeeIds: ['frontend-one', 'backend-one'],
        dispatches: [
          { employeeId: 'frontend-one', queueId: null, started: true, runId: 'multi-front-run' },
          { employeeId: 'backend-one', queueId: null, started: true, runId: 'multi-back-run' },
        ],
      }
    }
    groupMessageReads = 0
    groupMessages = [...groupMessages, {
      messageId: command.messageId,
      sequence: groupMessages.length + 1,
      senderKind: 'user',
      senderId: 'user-a',
      senderDisplayName: '你',
      senderAvatar: '',
      content: command.text,
      mentionEmployeeIds: command.mentionEmployeeIds,
      sourceRunId: null,
      createdAt: Date.now(),
    }]
    return {
      messageId: command.messageId,
      conversationKind: command.conversationKind,
      triggerEmployeeIds: command.mentionEmployeeIds,
      preselectedEmployeeIds: command.mentionEmployeeIds,
      dispatches: command.mentionEmployeeIds.map((employeeId) => ({ employeeId, queueId: null, started: true, runId: `run-${employeeId}` })),
    }
  })
  ipcMain.handle('office-desktop:listOfficeGroupMessages', async (_event, afterSequence = 0) => {
    if (captureNextGroupRead) {
      captureNextGroupRead = false
      capturedGroupReads += 1
      const snapshot = groupMessages.filter(message => message.sequence > afterSequence)
      await new Promise(resolve => setTimeout(resolve, 1400))
      return { messages: snapshot, nextAfterSequence: snapshot.at(-1)?.sequence || afterSequence }
    }
    if (groupReadDelay) await new Promise(resolve => setTimeout(resolve, groupReadDelay))
    if (failGroupRead) throw new Error('会话后台刷新失败测试')
    groupMessageReads += 1
    groupAfterSequences.push(Number(afterSequence) || 0)
    if (multiRunActive) {
      if (multiCompletionStage >= 1 && !multiFrontReplyAdded) {
        multiFrontReplyAdded = true
        groupMessages = [...groupMessages, {
          messageId: 'multi-front-reply', sequence: groupMessages.length + 1, senderKind: 'employee', senderId: 'frontend-one', senderDisplayName: '周礼', senderAvatar: 'ash', content: '前端并行任务完成', mentionEmployeeIds: [], sourceRunId: 'multi-front-run', createdAt: Date.now(),
        }]
      }
      // The native Run has sealed its events, but its Office completion hook
      // has not yet published the canonical reply. Do not stop at success.
      if (multiCompletionStage >= 2 && multiBackFinalizingReads >= 3 && !multiBackReplyAdded) {
        multiBackReplyAdded = true
        groupMessages = [...groupMessages, {
          messageId: 'multi-back-reply', sequence: groupMessages.length + 1, senderKind: 'employee', senderId: 'backend-one', senderDisplayName: '孙河', senderAvatar: 'adam', content: '后端并行任务完成', mentionEmployeeIds: [], sourceRunId: 'multi-back-run', createdAt: Date.now(),
        }]
      }
    }
    if (sentOfficeMessage?.text.endsWith('检查前端') && groupMessageReads >= 3 && !groupReplyAdded) {
      groupReplyAdded = true
      groupMessages = [...groupMessages, {
        messageId: 'group-ai-reply',
        sequence: groupMessages.length + 1,
        senderKind: 'employee',
        senderId: 'frontend-one',
        senderDisplayName: '旧周礼',
        senderAvatar: 'ash',
        content: richReply,
        mentionEmployeeIds: [],
        sourceRunId: 'run-frontend-one',
        createdAt: Date.now(),
      }]
    }
    const visibleMessages = groupMessages.filter((message) => message.sequence > (Number(afterSequence) || 0))
    return { messages: visibleMessages, nextAfterSequence: visibleMessages.at(-1)?.sequence || (Number(afterSequence) || 0) }
  })
  ipcMain.handle('office-desktop:listOfficeConversationMessages', async (_event, employeeId) => {
    if (deliverySmoke) {
      if (deliveryPrivateDelay) await new Promise(resolve => setTimeout(resolve, deliveryPrivateDelay))
      return { messages: privateMessages }
    }
    return employeeId === 'backend-one'
    ? { messages: [{ messageId: 'backend-history', role: 'assistant', content: '运行记录慢时先显示消息', runId: null }] }
    : ({ messages: privateReplyAdded ? [...privateMessages, {
    messageId: `private-ai-reply-${privateReplyRunId}`,
    runId: privateReplyRunId,
    role: 'assistant',
    content: '私聊 AI 返回完成',
    createdAt: new Date().toISOString(),
  }] : privateMessages })
  })
  ipcMain.handle('office-desktop:getCurrentOfficeWorkflow', async () => {
    if (workflowReadDelay) await new Promise(resolve => setTimeout(resolve, workflowReadDelay))
    return { workflow: workflowFixture }
  })
  let groupPaused = false
  const groupState = () => ({ activeCount: (cancelRunActive && !cancelRunCalled ? 1 : 0) + (nativeWorkActive ? 1 : 0), runTargets: [...nativeRunTargets, ...(clarificationRunActive && !clarificationResumeCalled ? [{ employeeId: 'frontend-one', runId: 'clarify-run', projectId: null, workId: null }] : [])], error: '', paused: groupPaused, stopping: false, heldQueueIds: groupPaused ? queueItems.filter(q => q.conversationKind === 'group').map(q => q.queueId) : [] })
  ipcMain.handle('office-desktop:getOfficeGroupExecution', async () => groupState())
  ipcMain.handle('office-desktop:pauseOfficeGroup', async () => {
    groupPauseCalls += 1
    groupPaused = true
    if (cancelRunActive) cancelRunCalled = true
    nativeWorkActive = false
    return groupState()
  })
  ipcMain.handle('office-desktop:listOfficeQueue', async () => {
    if (refreshQueueFails) throw new Error('模拟队列暂时不可用')
    const items = queueItems.map(item => ({ ...item }))
    if (deferQueueSnapshot) {
      const defer = deferQueueSnapshot
      deferQueueSnapshot = null
      await defer()
    }
    return { items }
  })
  ipcMain.handle('office-desktop:editOfficeQueue', async (_event, queueId, text) => {
    const item = queueItems.find((current) => current.queueId === queueId)
    if (!item) throw new Error('队列项不存在')
    item.text = text
    return { item }
  })
  ipcMain.handle('office-desktop:deleteOfficeQueue', async (_event, queueId) => {
    const index = queueItems.findIndex((current) => current.queueId === queueId)
    if (index < 0) throw new Error('队列项不存在')
    const [item] = queueItems.splice(index, 1)
    return { item }
  })
  ipcMain.handle('office-desktop:cancelOfficeRun', async (_event, employeeId, runId) => {
    assert.equal(employeeId, 'frontend-one')
    assert.equal(runId, 'cancel-run')
    cancelRunCalled = true
    return { runId, employeeId, status: 'interrupted' }
  })
  ipcMain.handle('office-desktop:resumeOfficeRun', async (_event, employeeId, runId, payload) => {
    assert.equal(employeeId, 'frontend-one')
    assert.equal(runId, 'clarify-run')
    assert.deepEqual(payload, {
      source: 'ask_clarification',
      requestId: 'clarification:call-clarify-1',
      checkpointId: 'checkpoint-clarify-1',
      text: '开发环境',
    })
    clarificationResumeCalled = true
    groupMessages = [...groupMessages, {
      messageId: 'clarification-answer-one', sequence: groupMessages.length + 1, senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '', content: payload.text, mentionEmployeeIds: [], sourceRunId: 'clarify-resumed-run', createdAt: Date.now(),
    }]
    return { employeeId, sourceRunId: runId, queueId: null, started: true, runId: 'clarify-resumed-run' }
  })
  ipcMain.handle('office-desktop:listOfficeRuns', async (_event, employeeId, kind) => {
    if (refreshSmoke) {
      if (kind === 'group' && employeeId === 'frontend-one' && refreshRunListFails) throw new Error('模拟前端运行列表不可用')
      if (!refreshActive || kind !== 'group' || !['frontend-one', 'backend-one'].includes(employeeId)) return { runs: [] }
      const front = employeeId === 'frontend-one'
      return { runs: [{ runId: front ? 'isolation-front' : 'isolation-back', employeeId, status: (front ? refreshFrontDone : refreshBackDone) ? 'success' : 'running' }] }
    }

    if (deliverySmoke) return { runs: kind === 'private' ? [{ runId: 'delivery-history', employeeId, status: deliveryRunStatus, eventsFinalized: deliveryRunStatus === 'success', lastEventSeq: 1 }] : [] }

    if (slowRunList && kind === 'private' && employeeId === 'backend-one') {
      await new Promise(resolve => setTimeout(resolve, 1400))
      return { runs: [] }
    }
    if (kind === 'group') {
      if (employeeId === 'frontend-one' && queuedAutoStatus) return {runs:[{runId:'queued-auto-run',employeeId,threadId:'auto-thread',status:queuedAutoStatus}]}
      groupRunReads.push(employeeId)
      if (terminalRunActive) return { runs: [] }
      if (!multiRunActive || !['frontend-one', 'backend-one'].includes(employeeId)) return { runs: [] }
      return { runs: [{
        runId: employeeId === 'frontend-one' ? 'multi-front-run' : 'multi-back-run', employeeId, taskId: 'multi-task', taskSummary: '多员工并行任务', threadId: 'office__user-a__' + employeeId + '__group', assistantId: employeeId, status: 'running',
      }] }
    }
    privateListRuns += 1
    return { runs: privateReplyAdded ? [] : [{
      runId: 'private-run-one',
      employeeId: 'frontend-one',
      taskId: 'private-task-one',
      taskSummary: '恢复中的私聊任务',
      threadId: 'office__user-a__frontend-one__private',
      assistantId: 'frontend-one',
      status: 'running',
    }] }
  })
  ipcMain.handle('office-desktop:listOfficeRunEvents', async (_event, _employeeId, runId, afterSeq) => {
    if (refreshSmoke) return refreshTracked(`events:${runId}`, async () => {
      const front = runId === 'isolation-front'
      if (front && refreshFrontDelay) await new Promise(resolve => setTimeout(resolve, refreshFrontDelay))
      if (front && refreshFrontFails) throw new Error('模拟前端事件暂时不可用')
      const name = front ? '前端' : '后端'
      const done = front ? refreshFrontDone : refreshBackDone
      const events = [
        { seq: 1, event_type: 'llm.ai.response', content: { id: `${runId}:progress`, content: `${name}仍在检查文件` } },
        { seq: 2, event_type: 'llm.tool.result', content: { name: 'read_file', tool_call_id: `${runId}:read`, status: 'success', content: 'read complete' } },
        ...(done ? [{ seq: 3, event_type: 'llm.ai.response', content: { id: `${runId}:final`, content: `${name}已完成检查` } }] : []),
      ].filter(event => event.seq > (afterSeq || 0)).map(event => ({ ...event, run_id: runId, metadata: {}, created_at: new Date().toISOString() }))
      return { events, nextAfterSeq: events.at(-1)?.seq || afterSeq || 0 }
    })

    if (deliverySmoke) { deliveryEvents += 1; await new Promise(resolve => setTimeout(resolve, 500)); return { events: [], nextAfterSeq: 1 } }

    if (runId.startsWith('native-work-')) return { events: [], nextAfterSeq: 0 }
    if (process.argv.includes('--i18n') && runId === 'cancel-run') return {
      events: Number(afterSeq) > 0 ? [] : [{
        run_id: runId, seq: 1, event_type: 'llm.ai.response',
        content: { tool_calls: [{ id: 'language-task', name: 'task', args: { subagent_type: 'backend-one' } }] },
        metadata: {}, created_at: new Date().toISOString(),
      }], nextAfterSeq: 1,
    }
    if(runId==='queued-auto-run') return {events:[{run_id:runId,seq:1,event_type:'llm.ai.response',content:{id:'auto-reply',content:'排队员工已自行开始回复'},metadata:{},created_at:new Date().toISOString()}],nextAfterSeq:1}
    if (runId === 'clarify-run') {
      const events = clarificationRunActive ? [{
        run_id: runId, seq: 1, event_type: 'llm.ai.response',
        content: { id: 'ask-ai', content: '', tool_calls: [{ id: 'call-clarify-1', name: 'ask_clarification', args: { question: '选择执行环境' } }] },
        metadata: {}, created_at: new Date().toISOString(),
      }, {
        thread_id: 'office__user-a__frontend-one__group',
        run_id: runId,
        user_id: 'user-a',
        event_type: 'runtime.human_input.requested',
        category: 'message',
        content: {
          kind: 'human_input_request',
          source: 'ask_clarification',
          request_id: 'clarification:call-clarify-1',
          checkpoint_id: 'checkpoint-clarify-1',
          question: '选择执行环境',
          input_mode: 'choice_with_other',
          options: [
            { id: 'option-1', label: '开发环境', value: '开发环境' },
            { id: 'option-2', label: '生产环境', value: '生产环境' },
          ],
        },
        metadata: { checkpoint_id: 'checkpoint-clarify-1' },
        seq: 2,
        created_at: new Date().toISOString(),
      }].filter((event) => event.seq > (Number(afterSeq) || 0)) : []
      return { events, nextAfterSeq: 2 }
    }
    const response = {
      events: (runId === 'run-frontend-one' || runId === 'multi-front-run' || runId === 'multi-back-run') ? [
      {
        thread_id: 'office__user-a__frontend-one__group',
        run_id: runId,
        user_id: 'user-a',
        event_type: 'runtime.tools.bound',
        category: 'runtime',
        content: { tool_names: ['task', 'read_file'] },
        metadata: {},
        seq: 1,
        created_at: new Date().toISOString(),
      },
      {
        thread_id: 'office__user-a__frontend-one__group',
        run_id: runId,
        user_id: 'user-a',
        event_type: 'run.start',
        category: 'trace',
        content: 'run.start',
        metadata: {},
        seq: 2,
        created_at: new Date().toISOString(),
      },
      {
        thread_id: 'office__user-a__frontend-one__group',
        run_id: runId,
        user_id: 'user-a',
        event_type: 'run.end',
        category: 'outputs',
        content: 'run.end',
        metadata: {},
        seq: 3,
        created_at: new Date().toISOString(),
      },
      ].filter((event) => event.seq > (Number(afterSeq) || 0)) : [],
      nextAfterSeq: 3,
    }
    if (runId === 'run-frontend-one') {
      const publicEvents = [
        { seq: 4, event_type: 'llm.ai.response', content: { id: 'summary-one', content: '老板，我先检查文件，再整理结论。', tool_calls: [{ id: 'read-one', name: 'read_file', args: { path: 'src/calendar.html' } }] } },
        { seq: 5, event_type: 'llm.tool.result', content: { name: 'read_file', tool_call_id: 'read-one', status: 'success', content: '<html>RAW_SOURCE_MUST_STAY_HIDDEN\\n'.repeat(3000) } },
        { seq: 6, event_type: 'llm.ai.response', content: { id: 'group-ai-reply', content: richReply } },
      ].filter((event) => event.seq > (Number(afterSeq) || 0) && (event.seq < 6 || groupReplyAdded)).map((event) => ({
        ...event, run_id: runId, thread_id: 'office__user-a__frontend-one__group', user_id: 'user-a', category: 'message', metadata: {}, created_at: new Date().toISOString(),
      }))
      response.events.push(...publicEvents)
      response.nextAfterSeq = groupReplyAdded ? 6 : 5
    }
    if (Number(afterSeq) > 0) runEventAfterSequences.push(`${runId}:${afterSeq}`)
    return response
  })
  ipcMain.handle('office-desktop:getOfficeRun', async (_event, _employeeId, runId) => {
    if (refreshSmoke) return refreshTracked(`status:${runId}`, async () => {
      const front = runId === 'isolation-front'
      if (front && refreshFrontDelay) await new Promise(resolve => setTimeout(resolve, refreshFrontDelay))
      if (front && refreshFrontFails) throw new Error('模拟前端状态暂时不可用')
      const done = front ? refreshFrontDone : refreshBackDone
      return { runId, employeeId: front ? 'frontend-one' : 'backend-one', status: done ? 'success' : 'running', eventsFinalized: done, finalizing: false, lastEventSeq: done ? 3 : 2 }
    })

    if (deliverySmoke) return { runId, employeeId: 'frontend-one', status: deliveryRunStatus, eventsFinalized: deliveryRunStatus === 'success', lastEventSeq: 1 }
    if (runId.startsWith('native-work-')) {
      nativeRunReads += 1
      if (!groupMessages.some(message => message.sourceRunId === runId)) groupMessages.push({
        messageId: `run:${runId}:final`, sequence: groupMessages.length + 1,
        senderKind: 'employee', senderId: 'pm', senderDisplayName: '项目经理', senderAvatar: '',
        content: `PM 本次工作结果 ${runId}`, mentionEmployeeIds: [], sourceRunId: runId, createdAt: Date.now(),
      })
      return { runId, employeeId: 'pm', status: 'success', eventsFinalized: true, finalizing: false, lastEventSeq: 0 }
    }
    if(runId==='queued-auto-run') return {runId,employeeId:'frontend-one',threadId:'auto-thread',status:queuedAutoStatus}
    if (runId === 'clarify-run') {
      return { runId, employeeId: 'frontend-one', taskId: 'clarify-task', taskSummary: '澄清测试', threadId: 'office__user-a__frontend-one__group', assistantId: 'frontend-one', status: 'interrupted', error: null, createdAt: '2026-10-31T01:00:00Z', updatedAt: '2026-10-31T01:01:00Z' }
    }
    if (runId === 'clarify-resumed-run') {
      return { runId, resumeSourceRunId: 'clarify-run', employeeId: 'frontend-one', taskId: 'clarify-task', taskSummary: '澄清测试', threadId: 'office__user-a__frontend-one__group', assistantId: 'frontend-one', status: 'success', error: null, createdAt: '2026-10-31T01:02:00Z', updatedAt: '2026-10-31T01:03:00Z' }
    }
    if (terminalRunActive && terminalStatusRuns[runId]) {
      return { runId, employeeId: runId === 'terminal-error-run' ? 'frontend-one' : runId === 'terminal-timeout-run' ? 'backend-one' : 'research-one', taskId: 'terminal-task', taskSummary: '终态测试', threadId: 'office__user-a__employee__group', assistantId: 'employee', status: terminalStatusRuns[runId], createdAt: terminalRunTime, updatedAt: terminalRunTime, error: runId === 'terminal-error-run' ? '执行失败' : null }
    }
    if (cancelRunActive && runId === 'cancel-run') {
      return { runId, employeeId: 'frontend-one', taskId: 'cancel-task', taskSummary: '取消测试', threadId: 'office__user-a__frontend-one__group', assistantId: 'frontend-one', status: cancelRunCalled ? 'interrupted' : 'running', error: null }
    }
    if (multiRunActive && (runId === 'multi-front-run' || runId === 'multi-back-run')) {
      const isFront = runId === 'multi-front-run'
      if (!isFront && multiCompletionStage >= 2) multiBackFinalizingReads += 1
      const finished = multiCompletionStage >= (isFront ? 1 : 2)
      const finalizing = !isFront && finished && multiBackFinalizingReads < 3
      return { runId, employeeId: isFront ? 'frontend-one' : 'backend-one', taskId: 'multi-task', taskSummary: '多员工并行任务', threadId: 'office__user-a__employee__group', assistantId: 'employee', status: finished ? 'success' : 'running', eventsFinalized: finished && !finalizing, finalizing }
    }
    if (runId === 'run-frontend-one') {
      return {
        runId,
        employeeId: 'frontend-one',
        taskId: 'group-task-one',
        taskSummary: '群聊检查前端',
        threadId: 'office__user-a__frontend-one__group',
        assistantId: 'frontend-one',
        status: groupReplyAdded ? 'success' : 'running',
      }
    }
    if (runId === 'private-run-one' || runId === 'private-run-two') {
      privateStatusReads += 1
      if (privateCompletionAllowed && privateStatusReads >= 3) privateReplyAdded = true
      return {
        runId,
        employeeId: 'frontend-one',
        taskId: 'private-task-two',
        taskSummary: '当前私聊任务',
        threadId: 'office__user-a__frontend-one__private',
        assistantId: 'frontend-one',
        status: privateReplyAdded ? 'success' : 'running',
      }
    }
    return {
      runId,
      employeeId: 'frontend-one',
      taskId: 'private-task-two',
      taskSummary: '当前私聊任务',
      threadId: 'office__user-a__frontend-one__private',
      assistantId: 'frontend-one',
      status: 'success',
    }
  })
  ipcMain.handle('office-desktop:getZoomFactor', async (event) => event.sender.getZoomFactor())
  ipcMain.handle('office-desktop:setZoomFactor', async (event, factor) => { event.sender.setZoomFactor(factor); return factor })
  ipcMain.handle('office-desktop:zoomBy', async (event, delta) => { const next = event.sender.getZoomFactor() + delta; event.sender.setZoomFactor(next); return next })
  ipcMain.handle('office-desktop:resetZoom', async (event) => { event.sender.setZoomFactor(1); return 1 })
}

function removeSmokeIpc() {
  for (const channel of [
    'listOfficeModels', 'getOfficeModel', 'updateOfficeModel', 'addOfficeModel', 'setOfficeDefaultModel',
    'listExecutionApprovals', 'decideExecutionApproval', 'getOfficeEmployeeTools', 'saveOfficeEmployeeTools',
    'getOfficeBootstrap', 'getOfficeSettings', 'getLanguage', 'setLanguage', 'saveOfficeSettings', 'getUserMemory', 'createMemoryFact', 'updateMemoryFact', 'deleteMemoryFact',
    'testOfficeBootstrapModel', 'initializeOfficeBootstrap', 'getServiceStatus', 'pingService',
    'getWorkspaceSummary', 'selectAndBindWorkspace', 'listOfficeEmployees', 'getOfficeEmployee', 'deleteOfficeEmployee', 'listOfficeEmployeeTemplates', 'listModels', 'listSkills',
    'listOfficeGroupMessages', 'listOfficeConversationMessages', 'listOfficeQueue', 'editOfficeQueue', 'deleteOfficeQueue', 'listOfficeRuns',
    'listOfficeRunEvents', 'getZoomFactor', 'setZoomFactor', 'zoomBy', 'resetZoom',
  ]) ipcMain.removeHandler('office-desktop:' + channel)
}

async function waitForRenderer(window, expression, label, timeoutMs = 12000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    if (await window.webContents.executeJavaScript('Boolean(' + expression + ')')) return
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  const bodyText = await window.webContents.executeJavaScript('(document.body?.innerText || "") + "\\nEDITOR=" + document.querySelector("[data-office-message-input]")?.value')
  throw new Error(label + ': timed out. Current body text: ' + bodyText)
}

async function run() {
  await app.whenReady()
  if (process.argv.includes('--queue-visibility')) {
    groupMessages = Array.from({ length: 30 }, (_, i) => ({ ...groupMessages[0], messageId: `history-${i}`, sequence: i + 1, content: `历史群聊消息 ${i}` }))
    privateMessages = Array.from({ length: 30 }, (_, i) => ({ messageId: `private-history-${i}`, role: 'user', content: `历史私聊消息 ${i}`, createdAt: new Date(1700000000000 + i * 1000).toISOString() }))
    queueItems = Array.from({ length: 8 }, (_, i) => ({ ...queueItems[0], queueId: `queue-${i}`, text: `排队任务 ${i}` }))
    employeeState = 'working'
  }
  registerSmokeIpc()
  const window = new BrowserWindow({
    show: modelSmoke || process.argv.includes('--employee-model'),
    width: 1280,
    height: 800,
    webPreferences: { preload: preloadPath, contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false },
  })
  window.webContents.on('console-message', ({ level, message }) => {
    if (level === 'error') console.error(`RENDERER_ERROR: ${message}`)
  })
  try {
    await window.loadFile(rendererPath)
    await waitForRenderer(window, "document.querySelector('[data-office-bootstrap]') !== null && document.querySelectorAll('canvas').length === 0", 'login route renders before Office')
    const setValue = (selector, value) => window.webContents.executeJavaScript(
      '(function(){ const input = document.querySelector(' + JSON.stringify(selector) + '); const setter = Object.getOwnPropertyDescriptor(input.constructor.prototype, "value").set; setter.call(input, ' + JSON.stringify(value) + '); input.dispatchEvent(new Event("input", { bubbles: true })); })()',
    )
    const selectLanguage = next => window.webContents.executeJavaScript(`(() => {
      const select = document.querySelector('[data-office-language]'); select.value = ${JSON.stringify(next)};
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`)
    if (process.argv.includes('--i18n')) {
      await setValue('[data-bootstrap-company]', '开源办公室')
      await selectLanguage('en')
      await waitForRenderer(window, "document.documentElement.lang === 'en' && document.querySelector('h1')?.textContent === 'Create your office'", 'English setup renders')
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-company]').value"), '开源办公室')
      await window.webContents.reload()
      await waitForRenderer(window, "document.querySelector('h1')?.textContent === 'Create your office'", 'explicit language persists across reload')
      await selectLanguage('zh-CN')
      await waitForRenderer(window, "document.querySelector('h1')?.textContent === '创建你的办公室'", 'Chinese setup returns')
    }
    await setValue('[data-bootstrap-company]', '烟测办公室')
    await setValue('[data-bootstrap-model]', 'mimo-v2.5')
    await setValue('[data-bootstrap-base-url]', 'https://example.com/v1')
    await setValue('[data-bootstrap-api-key]', 'smoke-key')
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-test]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('连接成功')", 'model connection verified')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-enter]').disabled"), true)
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-bind]').click()")
    await waitForRenderer(window, "document.querySelector('[data-bootstrap-bind]').disabled === false", 'directory picker cancellation')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-workspace]').value"), '')
    workspaceSelection = 'error'
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-bind]').click()")
    await waitForRenderer(window, "document.body.innerText.includes('目录无法读取')", 'binding error shown')
    workspaceSelection = 'select'
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-bind]').click()")
    await waitForRenderer(window, "document.querySelector('[data-bootstrap-enter]')?.disabled === false", 'binding and tested model enable Office entry')
    assert.equal(bootstrapModelTests, 1)
    await window.webContents.reload()
    await waitForRenderer(window, "document.querySelector('[data-bootstrap-workspace]')?.value === 'D:/测试/我的工作区'", 'bound directory survives reopening setup')
    await setValue('[data-bootstrap-company]', '烟测办公室')
    await setValue('[data-bootstrap-model]', 'mimo-v2.5')
    await setValue('[data-bootstrap-base-url]', 'https://example.com/v1')
    await setValue('[data-bootstrap-api-key]', 'smoke-key')
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-test]').click()")
    await waitForRenderer(window, "document.querySelector('[data-bootstrap-enter]').disabled === false", 'reopened setup verifies model')
    const bootstrapImage = await window.webContents.capturePage()
    await fs.mkdir(path.join(__dirname, '../output/playwright'), { recursive: true })
    await fs.writeFile(path.join(__dirname, '../output/playwright/bootstrap-workspace.png'), bootstrapImage.toPNG())
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-enter]')?.click()")
    await waitForRenderer(window, "location.hash === '#/office' && document.querySelectorAll('canvas').length === 1 && document.body.innerText.includes('服务器已连接')", 'Office route loads only after initialization')
    if (process.argv.includes('--background')) {
      await require('./background-smoke.cjs').run(window, waitForRenderer)
      return
    }
    if (process.argv.includes('--workspace-path')) {
      const bound = { projectId: 'project-path-test', path: 'D:/测试/真实项目', name: '真实项目', kind: 'empty', tree: [] }
      for (const [summary, expected] of [
        [{ ...bound, displayPath: null }, bound.path],
        [bound, bound.path],
        [{ ...bound, displayPath: 'Z:/用户选择的项目' }, 'Z:/用户选择的项目'],
        [undefined, '点击“绑定项目”后选择一个本地文件夹。'],
      ]) {
        workspaceFixture = summary
        await window.webContents.executeJavaScript("document.querySelector('button:has([data-testid=\"FolderOpenRoundedIcon\"])').click()")
        const displayed = "[...document.querySelectorAll('h3')].find(node => node.textContent === '项目路径')?.nextElementSibling?.textContent"
        await waitForRenderer(window, `${displayed} === ${JSON.stringify(expected)}`, 'workspace path displays authoritative value')
        assert.equal(await window.webContents.executeJavaScript("document.body.innerText.includes('未记录所选路径')"), false)
        await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"关闭本地项目面板\"]').click()")
        await waitForRenderer(window, "!document.querySelector('[aria-label=\"关闭本地项目面板\"]')", 'workspace panel closed')
      }
      console.log('DESKTOP_WORKSPACE_PATH_SMOKE_OK')
      return
    }
    await window.webContents.executeJavaScript("document.querySelector('[aria-label=\\\"打开办公室设置\\\"]')?.click()")
    await waitForRenderer(window, "document.querySelector('[aria-labelledby=\\\"office-settings-title\\\"]') !== null", 'office settings dialog opens')
    await waitForRenderer(window, "document.querySelector('[aria-label=\\\"关闭办公室设置\\\"]') !== null", 'office settings actions render')
    if (modelSmoke) {
      await modelScenario.run(window, waitForRenderer, setValue)
      return
    }
    await window.webContents.executeJavaScript("[...document.querySelectorAll('[data-office-settings-dialog] button')].find(b => b.textContent === '保存设置')?.click()")
    await waitForRenderer(window, "document.querySelector('[aria-labelledby=\\\"office-settings-title\\\"]') === null", 'office settings dialog saves and closes')
    if (process.argv.includes('--employee-model')) {
      await require('./employee-model-smoke.cjs').run(window, waitForRenderer, {
        getSaved: () => employeePersonalInfo,
        setWorking: value => { employeeState = value ? 'working' : 'idle' },
        failSave: value => { rejectPersonalSave = value },
      })
      return
    }
    await waitForRenderer(window, "document.querySelector('[data-office-chat-launcher]') !== null", 'Office chat launcher renders')
    if (process.argv.includes('--library')) {
      await require('./office-library-smoke.cjs').run(window, waitForRenderer, setValue)
      return
    }
    await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-launcher]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('历史群聊消息')", 'group history renders')
    const setEditorText = (value) => window.webContents.executeJavaScript(
      '(function(){ const editor = document.querySelector(\'[data-office-message-input]\'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(editor, ' + JSON.stringify(value) + '); editor.dispatchEvent(new Event("input", { bubbles: true })); })()',
    )
    if (process.argv.includes('--i18n')) {
      cancelRunActive = true
      nativeRunTargets = [{ employeeId: 'frontend-one', runId: 'cancel-run', projectId: null, workId: null }]
      await setEditorText('Keep this draft / 保留草稿')
      await waitForRenderer(window, "document.querySelector('[data-office-work-progress]') !== null", 'running task progress is present')
      await waitForRenderer(window, "document.querySelector('[data-office-activity=assignment]')?.textContent.includes('指派员工')", 'cached Chinese system activity renders')
      await window.webContents.executeJavaScript("void (window.languageActivity = document.querySelector('[data-office-activity=assignment]'))")
      await waitForRenderer(window, "window.game?.scene.keys.game?.myPlayer?.playerName?.text === '我'", 'Chinese canvas is ready')
      await window.webContents.executeJavaScript("window.languageItem = window.game.scene.keys.game.children.list.find(item => typeof item.setDialogBox === 'function'); window.languageItem.setDialogBox('ui.press_e_to_sit')")
      await window.webContents.executeJavaScript("window.languageCanvas = document.querySelector('canvas'); document.querySelector('[aria-label=\"打开办公室设置\"]').click()")
      await waitForRenderer(window, "document.querySelector('[data-office-settings-dialog] [data-office-language]') !== null", 'language selector in settings')
      await selectLanguage('en')
      await waitForRenderer(window, "document.querySelector('#office-settings-title')?.textContent === 'Office settings'", 'English settings render')
      assert.equal(await window.webContents.executeJavaScript("window.languageActivity === document.querySelector('[data-office-activity=assignment]') && window.languageActivity.textContent.includes('Assign employee') && window.languageActivity.textContent.includes('Execute task')"), true)
      assert.equal(await window.webContents.executeJavaScript("window.game.scene.keys.game.myPlayer.playerName.text"), 'Me')
      assert.equal(await window.webContents.executeJavaScript("window.languageItem.dialogBox.list.find(item => item.type === 'Text').text"), 'Press E to sit')
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('canvas') === window.languageCanvas"), true)
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').value"), 'Keep this draft / 保留草稿')
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-messages]').textContent.includes('历史群聊消息')"), true)
      assert.equal(await window.webContents.executeJavaScript("document.body.textContent.includes('Firecrawl API Key')"), false)
      assert.equal(cancelRunCalled, false)
      window.showInactive()
      await window.webContents.executeJavaScript('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
      const image = await window.webContents.capturePage()
      await fs.writeFile(path.resolve(__dirname, '../output/playwright/english-settings.png'), image.toPNG())
      window.hide()
      await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"Close office settings\"]').click(); document.querySelector('[data-office-employee-editor-launcher]').click()")
      await waitForRenderer(window, "document.querySelector('[data-edit-employee-item=\"frontend-one\"]') !== null", 'employee editor opens in English')
      await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-item=\"frontend-one\"]').click()")
      await waitForRenderer(window, "document.querySelector('[data-edit-employee-model]')?.textContent.includes('OpenAI - gpt-existing - High')", 'employee model reasoning label is English')
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-prompt]').value"), '员工个人 Prompt')
      await window.webContents.executeJavaScript("document.querySelector('[data-close-employee-editor]').click(); document.querySelector('[aria-label=\"Open office settings\"]').click()")
      await waitForRenderer(window, "document.querySelector('[data-office-settings-dialog] [data-office-language]') !== null", 'settings reopen in English')
      await selectLanguage('zh-CN')
      await waitForRenderer(window, "document.querySelector('#office-settings-title')?.textContent === '办公室设置'", 'Chinese settings return')
      rejectLanguageSave = true
      await selectLanguage('en')
      await waitForRenderer(window, "document.querySelector('[data-office-language]')?.closest('div')?.querySelector('[role=alert]') !== null", 'language save error is visible')
      assert.equal(await window.webContents.executeJavaScript('document.documentElement.lang'), 'zh-CN')
      assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-language]').value"), 'zh-CN')
      rejectLanguageSave = false
      console.log('I18N_SMOKE_OK: both languages, reload persistence, draft/history/canvas preserved, failed save keeps current language.')
      return
    }
    if (refreshSmoke) {
      const click = selector => window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(selector)}).click()`)
      await waitForRenderer(window, `document.querySelectorAll('[data-office-queue-item]').length === 2`, 'initial queue snapshot')
      await setEditorText('@')
      await waitForRenderer(window, `document.querySelector('[data-office-mention="frontend-one"]')`, 'frontend mention')
      await click('[data-office-mention="frontend-one"]')
      await setEditorText('@周礼 @')
      await waitForRenderer(window, `document.querySelector('[data-office-mention="backend-one"]')`, 'backend mention')
      await click('[data-office-mention="backend-one"]')
      await setEditorText('@周礼 @孙河 帮我检查一下这两个页面')
      groupReadDelay = 2200
      refreshFrontDelay = 2200
      refreshFrontFails = true
      refreshQueueFails = true
      await click('[data-office-send]')
      await waitForRenderer(window, `document.querySelector('[data-office-chat-messages]').textContent.includes('后端仍在检查文件')`, 'back employee progresses before slow history/front returns', 1400)
      assert.equal(await window.webContents.executeJavaScript(`document.querySelectorAll('[data-office-queue-item]').length`), 2, 'failed queue keeps last successful snapshot')
      failGroupRead = true
      refreshBackDone = true
      await waitForRenderer(window, `document.querySelector('[data-office-run-status="isolation-back"]')?.textContent.includes('已完成') && document.querySelector('[data-office-chat-messages]').textContent.includes('后端已完成检查')`, 'back employee can finish while history, queue and front fail', 3500)
      assert.equal(await window.webContents.executeJavaScript(`document.querySelector('[data-office-run-status="isolation-front"]')?.textContent.includes('已完成') || false`), false, 'unknown front status is not success')
      const evidenceDir = path.resolve(__dirname, '../output/playwright')
      await fs.mkdir(evidenceDir, { recursive: true })
      window.showInactive()
      await new Promise(resolve => setTimeout(resolve, 250))
      await fs.writeFile(path.join(evidenceDir, 'chat-independent-refresh.png'), (await window.webContents.capturePage()).toPNG())
      // Switch away while a front read is delayed; its result must stay scoped.
      await click('[data-office-employee="research-one"]')
      await new Promise(resolve => setTimeout(resolve, 2600))
      assert.equal(await window.webContents.executeJavaScript(`document.querySelector('[data-office-chat-messages]').textContent.includes('后端已完成检查')`), false)
      refreshRunListFails = true
      refreshFrontDelay = 0
      await click('[data-office-group]')
      await waitForRenderer(window, `document.querySelector('[data-office-run-status="isolation-back"]')?.textContent.includes('已完成')`, 'one failed Run list does not block another employee')
      // Recover all endpoints. Every stream must reconcile without duplicates.
      failGroupRead = false; groupReadDelay = 0; refreshQueueFails = false
      refreshFrontFails = false; refreshFrontDone = true; refreshRunListFails = false; queueItems = []
      await waitForRenderer(window, `document.querySelector('[data-office-chat-messages]').textContent.includes('前端已完成检查') && !document.querySelector('[data-office-ai-loading]') && !document.querySelector('[data-office-refresh-error]') && !document.querySelector('[data-office-queue]')`, 'independent lanes recover and finish', 16000)
      assert.equal(await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('[data-office-progressive-reply]')).filter(node => node.textContent.includes('后端已完成检查')).length`), 1)
      assert.ok([...refreshMaxInFlight.values()].every(count => count <= 1), `overlapping reads: ${JSON.stringify([...refreshMaxInFlight])}`)
      await fs.writeFile(path.join(evidenceDir, 'chat-independent-refresh.json'), JSON.stringify({ slowHistoryIsolated: true, queueFailurePreservesSnapshot: true, employeeFailureIsolated: true, otherRunCompletes: true, switchScopesResponses: true, recoversWithoutDuplicates: true, maxInFlight: Object.fromEntries(refreshMaxInFlight) }, null, 2))
      console.log('DESKTOP_INDEPENDENT_REFRESH_SMOKE_OK')
      return
    }
    if (deliverySmoke) {
      const click = selector => window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(selector)}).click()`)
      const check = expression => window.webContents.executeJavaScript(expression).catch(error => { throw new Error(`${expression}: ${error.message}`) })
      const userCount = text => check(`Array.from(document.querySelectorAll('[data-message-role="user"]')).filter(node => node.textContent.includes(${JSON.stringify(text)})).length`)
      await waitForRenderer(window, "document.body.innerText.includes('历史群聊消息')", 'initial group history')
      // Observe transient flashes, not just the final DOM after requests finish.
      await check(`window.unexpectedReplyDots = false; window.replyObserver = new MutationObserver(() => {
        if (document.querySelector('[data-office-ai-loading]')) window.unexpectedReplyDots = true
      }); window.replyObserver.observe(document.querySelector('[data-office-chat-main]'), { childList: true, subtree: true })`)
      sendDelay = 1600
      await setEditorText('帮我改一下首页标题')
      await click('[data-office-send]')
      await waitForRenderer(window, `document.querySelector('[data-office-send-status="sending"]') && document.querySelector('[data-office-message-input]').value === ''`, 'optimistic user bubble before IPC returns', 700)
      assert.equal(deliveryCalls, 0, 'the server has not accepted the request yet')
      assert.equal(await userCount('帮我改一下首页标题'), 1)
      await setEditorText('我再补充一句')
      const evidenceDir = path.resolve(__dirname, '../output/playwright')
      await fs.mkdir(evidenceDir, { recursive: true })
      window.showInactive()
      await new Promise(resolve => setTimeout(resolve, 250))
      await fs.writeFile(path.join(evidenceDir, 'chat-send-immediate.png'), (await window.webContents.capturePage()).toPNG())
      await waitForRenderer(window, `!document.querySelector('[data-office-send-status="sending"]')`, 'accepted send removes pending marker')
      assert.equal(await userCount('帮我改一下首页标题'), 1, 'receipt replaces optimistic message without duplication')
      assert.equal(await check(`document.querySelector('[data-office-message-input]').value`), '我再补充一句', 'late receipt preserves new draft')
      // First visit loads a completed Run with unread events; this is not a reply.
      await click('[data-office-employee="frontend-one"]')
      await new Promise(resolve => setTimeout(resolve, 1700))
      assert.ok(deliveryEvents > 0, 'terminal history actually replayed')
      assert.equal(await check('window.unexpectedReplyDots'), false, 'sending and replaying terminal history never creates AI dots')
      await setEditorText('私聊发送途中切换')
      await click('[data-office-send]')
      await waitForRenderer(window, `document.querySelector('[data-office-send-status="sending"]')`, 'private optimistic bubble', 700)
      await click('[data-office-group]')
      await waitForRenderer(window, `document.querySelector('[data-office-message-input]').value === '我再补充一句'`, 'group draft survives private send')
      await new Promise(resolve => setTimeout(resolve, 1800))
      assert.equal(await userCount('私聊发送途中切换'), 0, 'private pending text does not leak to group')
      await click('[data-office-employee="frontend-one"]')
      await waitForRenderer(window, `document.querySelector('[data-office-message-input]').value === '' && document.querySelector('[data-office-chat-messages]').textContent.includes('私聊发送途中切换')`, 'background receipt belongs to original conversation')
      assert.equal(await userCount('私聊发送途中切换'), 1)
      sendDelay = 100
      for (let count = 1; count <= 2; count++) {
        await setEditorText('请回复收到，相同内容再发一次')
        await click('[data-office-send]')
        await waitForRenderer(window, `!document.querySelector('[data-office-send-status="sending"]')`, 'identical private message accepted')
        await new Promise(resolve => setTimeout(resolve, 500))
        assert.equal(await userCount('请回复收到，相同内容再发一次'), count, 'distinct sends survive source-ID reconciliation')
      }
      deliveryPrivateDelay = 1200
      await click('[data-office-group]')
      await click('[data-office-employee="frontend-one"]')
      await waitForRenderer(window, `document.querySelector('[data-office-chat-messages]').textContent.includes('私聊发送途中切换') && !document.querySelector('[data-office-message-loading]')`, 'cached conversation remains visible during refresh', 600)
      await new Promise(resolve => setTimeout(resolve, 1400))
      deliveryPrivateDelay = 0
      assert.equal(await check('window.unexpectedReplyDots'), false)
      await check('window.replyObserver.disconnect()')
      deliveryRunStatus = 'running'
      await click('[data-office-group]')
      await click('[data-office-employee="frontend-one"]')
      await waitForRenderer(window, `document.querySelector('[data-office-ai-loading]')`, 'a truly active Run still shows employee dots')
      deliveryRunStatus = 'success'
      await waitForRenderer(window, `!document.querySelector('[data-office-ai-loading]')`, 'active Run completion clears dots')
      await click('[data-office-group]')
      sendDelay = 500
      deliveryFailure = true
      await setEditorText('这条发送失败要保留')
      await click('[data-office-send]')
      await waitForRenderer(window, `document.querySelector('[data-office-send-status="unconfirmed"]')`, 'failed send preserves original text')
      assert.equal(await userCount('这条发送失败要保留'), 1)
      const callsBeforeRecheck = deliveryCalls
      await click('[data-office-send-status="unconfirmed"] button')
      await new Promise(resolve => setTimeout(resolve, 300))
      assert.equal(deliveryCalls, callsBeforeRecheck, 'checking uncertain delivery never submits twice')
      deliveryStoredBeforeFailure = true
      await setEditorText('已保存但响应失败')
      await click('[data-office-send]')
      await waitForRenderer(window, `!document.querySelector('[data-office-send-status="sending"]') && Array.from(document.querySelectorAll('[data-message-role="user"]')).some(node => node.textContent.includes('已保存但响应失败') && !node.querySelector('[data-office-send-status]'))`, 'failed response reconciles saved user message')
      assert.equal(await userCount('已保存但响应失败'), 1)
      assert.equal(await check(`Array.from(document.querySelectorAll('[data-message-role="user"]')).find(node => node.textContent.includes('已保存但响应失败')).querySelector('[data-office-send-status]') === null`), true)
      await fs.writeFile(path.join(evidenceDir, 'chat-delivery-smoke.json'), JSON.stringify({ optimisticSend: true, deduplication: true, preservesNewDraft: true, switchDuringSend: true, cachedRefresh: true, terminalReplay: true, activeRun: true, failedSend: true, savedBeforeFailure: true, noAutomaticResend: true }, null, 2))
      console.log('DESKTOP_CHAT_DELIVERY_SMOKE_OK')
      return
    }
    if (process.argv.includes('--queue-visibility')) {
      const inspectQueue = () => window.webContents.executeJavaScript(`(() => {
        const panel = document.querySelector('[data-office-queue]');
        const list = document.querySelector('[data-office-chat-messages]');
        const items = document.querySelector('[data-office-queue-items]');
        const editor = document.querySelector('[data-office-message-input]');
        const p = panel.getBoundingClientRect(), l = list.getBoundingClientRect(), e = editor.getBoundingClientRect();
        return { visible: p.top >= 0 && p.bottom <= innerHeight && p.height > 0,
          outsideHistory: !list.contains(panel), aboveEditor: p.bottom <= e.top + 1,
          historyVisible: l.height > 40, count: panel.querySelectorAll('[data-office-queue-item]').length,
          internallyScrollable: items.scrollHeight > items.clientHeight, top: p.top };
      })()`)
      await waitForRenderer(window, "document.querySelectorAll('[data-office-queue-item]').length === 8", 'long group queue loads')
      const groupBefore = await inspectQueue()
      assert.ok(groupBefore.visible && groupBefore.outsideHistory && groupBefore.aboveEditor && groupBefore.historyVisible)
      assert.ok(groupBefore.internallyScrollable, 'many pending items scroll inside their own panel')
      await window.webContents.executeJavaScript("const list = document.querySelector('[data-office-chat-messages]'); list.scrollTop = 0; list.dispatchEvent(new Event('scroll'))")
      assert.equal((await inspectQueue()).top, groupBefore.top, 'history scrolling cannot move the queue')
      let releaseOldQueue
      const oldQueueCaptured = new Promise(resolve => {
        deferQueueSnapshot = () => new Promise(release => { releaseOldQueue = release; resolve() })
      })
      await window.webContents.executeJavaScript(`document.querySelector('[data-office-employee="frontend-one"]').click()`)
      await waitForRenderer(window, "document.body.innerText.includes('历史私聊消息 29') && !document.querySelector('[data-office-queue]')", 'group queue does not leak into private chat')
      await Promise.race([oldQueueCaptured, new Promise((_, reject) => setTimeout(() => reject(new Error('Queue snapshot was not captured')), 5000))])
      await setEditorText('回复我你好')
      await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click()")
      await waitForRenderer(window, `document.querySelector('[data-office-queue-item="private-pending"]') && document.querySelector('[data-office-message-input]').value === ''`, 'busy employee private message queues after clearing composer')
      releaseOldQueue()
      await new Promise(resolve => setTimeout(resolve, 1000))
      assert.equal(await window.webContents.executeJavaScript(`Boolean(document.querySelector('[data-office-queue-item="private-pending"]'))`), true, 'late pre-send snapshot cannot hide an accepted queued message')
      const privateState = await inspectQueue()
      assert.ok(privateState.visible && privateState.outsideHistory && privateState.aboveEditor && privateState.historyVisible)
      assert.equal(privateState.count, 1)
      await window.webContents.executeJavaScript(`document.querySelector('[data-office-queue-edit="private-pending"]').click()`)
      await setValue('[data-office-queue-input="private-pending"]', '回复你好就行')
      await window.webContents.executeJavaScript(`document.querySelector('[data-office-queue-save="private-pending"]').click()`)
      await waitForRenderer(window, `document.querySelector('[data-office-queue-item="private-pending"]').textContent.includes('回复你好就行')`, 'private queue edit persists')
      const evidenceDir = path.resolve(__dirname, '../output/playwright')
      await fs.mkdir(evidenceDir, { recursive: true })
      window.showInactive()
      await new Promise(resolve => setTimeout(resolve, 350))
      await fs.writeFile(path.join(evidenceDir, 'fifo-queue-fixed.png'), (await window.webContents.capturePage()).toPNG())
      await fs.writeFile(path.join(evidenceDir, 'fifo-queue-fixed.json'), JSON.stringify({ group: groupBefore, private: privateState }, null, 2))
      await window.webContents.executeJavaScript(`document.querySelector('[data-office-queue-delete="private-pending"]').click()`)
      await waitForRenderer(window, "!document.querySelector('[data-office-queue]')", 'last pending item removal hides panel')
      console.log('DESKTOP_QUEUE_VIEWPORT_SMOKE_OK')
      return
    }
    const sidebarBefore = await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-window] aside').getBoundingClientRect().width")
    await setEditorText('第一行')
    await window.webContents.executeJavaScript("const editor = document.querySelector('[data-office-message-input]'); editor.focus(); editor.setSelectionRange(editor.value.length, editor.value.length)")
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return', modifiers: ['shift'] })
    window.webContents.sendInputEvent({ type: 'char', keyCode: '\r', modifiers: ['shift'] })
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return', modifiers: ['shift'] })
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]').value === '第一行\\n'", 'Shift Enter default editing inserts newline before more text')
    await window.webContents.insertText('第二行')
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]').value === '第一行\\n第二行'", 'Shift Enter inserts a real newline')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }))")
    assert.equal(sentOfficeMessage, null, 'IME confirmation and Shift Enter must not send messages')
    await setEditorText('')
    await waitForRenderer(window, "document.querySelector('[data-office-queue-item=\"queue-one\"]') !== null && document.body.innerText.includes('直接任务') && document.body.innerText.includes('PM 指派')", 'group queue items render with source kind')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-queue-edit=\"queue-one\"]')?.click()")
    await setValue('[data-office-queue-input="queue-one"]', '编辑后的排队任务')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-queue-save=\"queue-one\"]')?.click()")
    await waitForRenderer(window, "document.querySelector('[data-office-queue-item=\"queue-one\"]')?.textContent.includes('编辑后的排队任务')", 'queued message text edits in place')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-queue-delete=\"queue-one\"]')?.click()")
    await waitForRenderer(window, "document.querySelector('[data-office-queue-item=\"queue-one\"]') === null", 'queued message deletes before starting')
    console.log('DESKTOP_QUEUE_CONTROLS_SMOKE_OK')
    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"frontend-one\"]') !== null", 'queued employee mention')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"frontend-one\"]').click()")
    await setEditorText('@周礼 排队自动启动测试')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click()")
    await waitForRenderer(window, "document.body.innerText.includes('排队员工已自行开始回复')", 'queued Run is discovered without switching conversations')
    queuedAutoStatus = null
    console.log('DESKTOP_QUEUED_RUN_DISCOVERY_OK')

    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"frontend-one\"]') !== null", 'cancel employee mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"frontend-one\"]')?.click()")
    await setEditorText('@周礼 取消测试')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('取消测试') && document.querySelector('[data-office-pause]') !== null", 'cancel control renders for active Run')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-pause]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('已中断') && document.querySelector('[data-office-ai-loading]') === null", 'cancelled Run renders interrupted terminal')
    console.log('DESKTOP_CANCEL_RUN_SMOKE_OK')
    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"frontend-one\"]') !== null", 'clarification employee mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\\\"frontend-one\\\"]')?.click()")
    await setEditorText('@周礼 澄清测试')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('选择执行环境') && document.querySelector('[data-office-human-input=\"clarification:call-clarify-1\"]') !== null", 'human input request renders with choices')
    await waitForRenderer(window, `document.querySelector('[data-office-run-status="clarify-run"]')?.textContent.includes('等待你的回复') && [...document.querySelectorAll('[data-office-tool-group]')].some(el => el.textContent.includes('ask_clarification') && el.querySelector('summary').textContent.includes('等待回复'))`, 'ask waits in both status rows')
    const normalMessageHasHumanInput = await window.webContents.executeJavaScript(
      "document.querySelector('[data-office-human-input=\\\"ordinary-message\\\"]') !== null",
    )
    assert.equal(normalMessageHasHumanInput, false)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"选择要回复的追问\"]') === null"), true)
    assert.equal(await window.webContents.executeJavaScript("document.querySelectorAll('[data-office-human-inputs] input[type=radio]').length"), 3)
    assert.equal(await window.webContents.executeJavaScript("document.querySelectorAll('[data-office-human-inputs] input[type=text]').length"), 1)
    await window.webContents.executeJavaScript("document.querySelector('[data-office-human-option]').click()")
    assert.equal(clarificationResumeCalled, false, 'selecting a radio must not execute immediately')
    await setEditorText('开发环境')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))")
    await waitForRenderer(window, "document.body.innerText.includes('开发环境') && document.querySelector('[data-office-human-input=\"clarification:call-clarify-1\"]') === null", 'human input answer resumes the interrupted Run')
    assert.equal(clarificationResumeCalled, true)
    await waitForRenderer(window, "document.querySelector('[data-office-run-status=\"clarify-run\"]')?.textContent.includes('周礼')", 'resuming preserves the original Run owner for history and tools')
    await waitForRenderer(window, `document.querySelector('[data-office-run-status="clarify-run"]')?.textContent.includes('已继续') && [...document.querySelectorAll('[data-office-tool-group]')].some(el => el.textContent.includes('ask_clarification') && el.querySelector('summary').textContent.includes('已回复'))`, 'resumed ask history is not stopped or interrupted')
    console.log('DESKTOP_HUMAN_INPUT_RESUME_SMOKE_OK')
    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\\\"frontend-one\\\"]') !== null", 'mention candidates render')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\\\"frontend-one\\\"]')?.click()")
    await setEditorText('@周礼 检查前端')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('@周礼 检查前端')", 'sent group message renders')
    await waitForRenderer(window, "document.querySelector('[data-office-ai-loading]') !== null", 'AI loading renders while reply is pending')
    await waitForRenderer(window, "Array.from(document.querySelectorAll('[data-office-progressive-reply]')).some(node => node.textContent.includes('先检查文件')) && document.querySelector('[data-office-ai-loading]') !== null", 'public summary appears before the Run completes')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-progressive-reply] [data-sender-avatar]')?.getAttribute('data-sender-avatar')"), 'ash')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-ai-loading] [data-sender-avatar]')?.getAttribute('data-sender-avatar')"), 'ash')
    await waitForRenderer(window, "document.body.innerText.includes('AI 返回完成') && document.querySelector('[data-office-ai-loading]') === null", 'AI reply refreshes and replaces loading')
    const hiddenInternalEvents = await window.webContents.executeJavaScript(
      "document.body.innerText.includes('runtime.tools.bound') || document.body.innerText.includes('run.start') || document.body.innerText.includes('run.end')",
    )
    assert.equal(hiddenInternalEvents, false, 'internal DeerFlow lifecycle events stay out of chat')
    assert.ok(await window.webContents.executeJavaScript("document.querySelectorAll('[data-office-tool-group]').length > 0"))
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-tool-group]').open"), false)
    await window.webContents.executeJavaScript("document.querySelector('[data-office-tool-group] summary').click()")
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-tool-group]').open"), true)
    const presentation = await window.webContents.executeJavaScript(`(() => {
      const main = document.querySelector('[data-office-chat-main]')
      const reply = document.querySelector('[data-group-message="group-ai-reply"]')
      const ai = reply.querySelector('[data-office-message-content]').getBoundingClientRect()
      const human = document.querySelector('[data-message-role="user"] [data-office-message-content]').getBoundingClientRect()
      return {
        bold: reply.querySelector('strong')?.textContent,
        code: reply.querySelector('pre code')?.textContent,
        items: reply.querySelectorAll('li').length,
        copies: main.innerText.split('AI 返回完成').length - 1,
        raw: main.innerText.includes('RAW_SOURCE_MUST_STAY_HIDDEN'),
        commandsClickable: !!main.querySelector('[data-office-activity] button, [data-office-activity] a'),
        scriptRan: !!window.chatInjected,
        aligned: human.right > ai.left + 100,
        overflow: main.scrollWidth > main.clientWidth,
        chronological: !!([...main.querySelectorAll('[data-office-activity]')].find(el => el.textContent.includes('src/calendar.html')).compareDocumentPosition(reply) & Node.DOCUMENT_POSITION_FOLLOWING),
      }
    })()`)
    assert.equal(presentation.bold, '聊天展示已优化')
    assert.ok(presentation.code.includes('const send'))
    assert.equal(presentation.items, 2)
    assert.equal(presentation.copies, 1)
    assert.equal(presentation.raw, false)
    assert.equal(presentation.commandsClickable, false)
    assert.ok(await window.webContents.executeJavaScript("[...document.querySelectorAll('[data-office-activity]')].some(el => el.textContent.includes('src/calendar.html'))"))
    assert.equal(presentation.scriptRan, false)
    assert.equal(presentation.aligned, true)
    assert.equal(presentation.overflow, false)
    assert.equal(presentation.chronological, true)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-window] aside').getBoundingClientRect().width"), sidebarBefore)
    await window.webContents.executeJavaScript("document.querySelector('[data-office-file=\"docs/design.md\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-file-preview]')?.textContent.includes('设计文档')", 'file preview renders')
    await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"在文件夹中显示\"]').click()")
    await new Promise(resolve => setTimeout(resolve, 100))
    assert.equal(revealedFile.employeeId, 'frontend-one')
    assert.equal(revealedFile.runId, 'run-frontend-one')
    await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"关闭文件预览\"]').click()")
    await waitForRenderer(window, "!document.querySelector('[data-office-file-preview]')", 'preview closes')
    await fs.mkdir(path.resolve(__dirname, '../output'), { recursive: true })
    await window.webContents.executeJavaScript("document.querySelector('[data-group-message=\"group-ai-reply\"]').scrollIntoView({ block: 'center' })")
    await new Promise((resolve) => setTimeout(resolve, 350))
    await fs.writeFile(path.resolve(__dirname, '../output/chat-redesign.png'), (await window.webContents.capturePage()).toPNG())
    console.log('DESKTOP_CHAT_PRESENTATION_SMOKE_OK')
    if (!sentOfficeMessage || sentOfficeMessage.conversationKind !== 'group' || sentOfficeMessage.mentionEmployeeIds.join(',') !== 'frontend-one') {
      throw new Error('group message did not submit the selected employee id')
    }
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\\\"frontend-one\\\"]')?.click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('小小办公室交流群')", 'group conversation reopens for parallel run')
    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"frontend-one\"]') !== null", 'parallel frontend mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"frontend-one\"]')?.click()")
    await setEditorText('@周礼 @')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"backend-one\"]') !== null", 'parallel backend mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"backend-one\"]')?.click()")
    await setEditorText('@周礼 @孙河 多员工并行')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('@周礼 @孙河 多员工并行')", 'parallel group message renders')
    await waitForRenderer(window, "document.querySelectorAll('[data-office-ai-loading]').length === 2", 'parallel Runs each render their own loading indicator')
    assert.deepEqual(await window.webContents.executeJavaScript("[...document.querySelectorAll('[data-office-ai-loading]')].map(node => node.getAttribute('data-office-loading-run-id'))"), ['multi-front-run', 'multi-back-run'])
    assert.deepEqual(await window.webContents.executeJavaScript("[...document.querySelectorAll('[data-office-ai-loading]')].map(node => node.textContent.includes('周礼') ? '周礼' : node.textContent.includes('孙河') ? '孙河' : '')"), ['周礼', '孙河'])
    multiCompletionStage = 1
    await waitForRenderer(window, "document.body.innerText.includes('前端并行任务完成') && document.querySelectorAll('[data-office-ai-loading]').length === 1", 'parallel wait remains only for the second active Run')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-ai-loading]')?.getAttribute('data-office-loading-run-id')"), 'multi-back-run')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-ai-loading]')?.textContent.includes('孙河')"), true)
    multiCompletionStage = 2
    await waitForRenderer(window, "document.body.innerText.includes('后端并行任务完成') && document.querySelector('[data-office-ai-loading]') === null", 'parallel wait ends after every run completes')
    assert.ok(groupRunReads.includes('backend-one'), 'group recovery queries regular employee Runs')
    assert.ok(runEventAfterSequences.some((value) => value.startsWith('multi-front-run:')), 'frontend Run events use an afterSeq cursor')
    assert.ok(runEventAfterSequences.some((value) => value.startsWith('multi-back-run:')), 'backend Run events use an afterSeq cursor')
    console.log('DESKTOP_PARALLEL_RUNS_SMOKE_OK')
    await setEditorText('@')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"frontend-one\"]') !== null", 'terminal frontend mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"frontend-one\"]')?.click()")
    await setEditorText('@周礼 @')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"backend-one\"]') !== null", 'terminal backend mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"backend-one\"]')?.click()")
    await setEditorText('@周礼 @孙河 @')
    await waitForRenderer(window, "document.querySelector('[data-office-mention=\"research-one\"]') !== null", 'terminal research mention candidate renders')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"research-one\"]')?.click()")
    await setEditorText('@周礼 @孙河 @黎明 多员工终态')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]')?.click()")
    await waitForRenderer(window, "document.body.innerText.includes('多员工终态')", 'terminal status message renders')
    await waitForRenderer(window, "document.body.innerText.includes('失败') && document.body.innerText.includes('超时') && document.body.innerText.includes('已中断') && document.querySelector('[data-office-ai-loading]') === null", 'terminal statuses remain distinct')
    console.log('DESKTOP_RUN_TERMINAL_STATUS_SMOKE_OK')
    groupMessages.push({ messageId: 'after-cancel-chat', sequence: groupMessages.length + 1,
      senderKind: 'user', senderId: 'user-a', senderDisplayName: '你', senderAvatar: '',
      content: '结束流程后继续普通聊天', mentionEmployeeIds: [], sourceRunId: null, createdAt: Date.now() + 1000 })
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]')?.click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]')?.click()")
    await waitForRenderer(window, "document.querySelector('[data-group-message=\"after-cancel-chat\"]') !== null", 'new chat after cancelled workflow')
    assert.ok(await window.webContents.executeJavaScript(`['terminal-error-run', 'terminal-timeout-run', 'terminal-interrupted-run'].every(id => {
      const status = document.querySelector('[data-office-work-progress$=":' + id + '"]');
      const newer = document.querySelector('[data-group-message="after-cancel-chat"]');
      return status && (status.compareDocumentPosition(newer) & Node.DOCUMENT_POSITION_FOLLOWING);
    })`), 'old terminal statuses must stay before newer conversation messages')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]')?.click()")
    await waitForRenderer(window, "document.body.innerText.replace(/\\s+/g, '').includes('周礼（前端工程师·私聊）')", 'private conversation opens from sidebar')
    await waitForRenderer(window, "document.querySelector('[data-office-ai-loading]') !== null", 'private active run restores loading after selecting conversation')
    privateCompletionAllowed = true
    await waitForRenderer(window, "document.body.innerText.includes('私聊 AI 返回完成') && document.querySelector('[data-office-ai-loading]') === null", 'private active run refreshes its final reply')
    await setEditorText('请继续检查这个私聊链路\n保留第二行')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))")
    await waitForRenderer(window, "document.body.innerText.includes('请继续检查这个私聊链路')", 'private message renders after sending')
    await waitForRenderer(window, "document.body.innerText.includes('私聊 AI 返回完成') && document.querySelector('[data-office-ai-loading]') === null", 'private message receives its reply')
    if (!privateSentOfficeMessage || privateSentOfficeMessage.conversationKind !== 'private' || privateSentOfficeMessage.targetEmployeeId !== 'frontend-one' || privateSentOfficeMessage.mentionEmployeeIds.length !== 0) {
      throw new Error('private message did not submit the selected employee id without mentions')
    }
    assert.equal(privateSentOfficeMessage.text, '请继续检查这个私聊链路\n保留第二行')
    console.log('DESKTOP_LOGIN_TO_OFFICE_SMOKE_OK')
    console.log('DESKTOP_GROUP_CHAT_SMOKE_OK')
    console.log('DESKTOP_PRIVATE_CHAT_RECOVERY_SMOKE_OK')
    console.log('DESKTOP_PRIVATE_CHAT_SEND_SMOKE_OK')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-message-role=\"assistant\"] [data-sender-avatar]')?.getAttribute('data-sender-avatar')"), 'ash')
    await setEditorText('私聊未发送草稿\n第二行')
    groupReadDelay = 1600
    const groupReadsBeforeSwitch = groupAfterSequences.length
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-group-message=\"group-ai-reply\"]') && !document.querySelector('[data-office-message-loading]')", 'cached group appears before delayed network response', 700)
    await setEditorText('群聊未发送草稿')
    await window.webContents.executeJavaScript("const list = document.querySelector('[data-office-chat-messages]'); list.scrollTop = 0; list.dispatchEvent(new Event('scroll')); document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value === '私聊未发送草稿\\n第二行' && !document.querySelector('[data-office-message-loading]')", 'private draft and messages restore immediately', 700)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-main]').textContent.includes('前端并行任务完成')"), false)
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value === '群聊未发送草稿' && document.querySelector('[data-group-message=\"group-ai-reply\"]')", 'group draft survives rapid switch', 700)
    await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"关闭小小办公室通讯\"]').click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-launcher]').click()")
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').value"), '群聊未发送草稿')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-messages]').scrollTop"), 0)
    await new Promise(resolve => setTimeout(resolve, 1800))
    assert.equal(groupAfterSequences.length - groupReadsBeforeSwitch, 1, 'rapid return shares the pending group request')
    assert.ok(groupAfterSequences.at(-1) > 0, 'return to group retains the sequence cursor')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-messages]').scrollTop"), 0)
    groupReadDelay = 0
    failGroupRead = true
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value.includes('私聊未发送草稿')", 'private view selected before failing refresh')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-refresh-error]')?.title.includes('会话后台刷新失败测试') && document.querySelector('[data-group-message=\"group-ai-reply\"]')", 'refresh failure preserves cached messages')
    failGroupRead = false
    slowRunList = true
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"backend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-chat-main]').textContent.includes('运行记录慢时先显示消息') && !document.querySelector('[data-office-message-loading]')", 'first messages do not wait for slow Run listing', 700)
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-group-message=\"group-ai-reply\"]')", 'group restored while private Run list is pending')
    await new Promise(resolve => setTimeout(resolve, 1500))
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-main]').textContent.includes('运行记录慢时先显示消息')"), false)
    slowRunList = false
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value.includes('私聊未发送草稿')", 'private draft available for delayed send')
    await setEditorText('发送完成前切走')
    sendDelay = 1000
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click(); document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value === '群聊未发送草稿'", 'send does not clear another conversation draft')
    await new Promise(resolve => setTimeout(resolve, 1200))
    sendDelay = 0
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').value"), '群聊未发送草稿')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-message-input]')?.value === '' && document.querySelector('[data-office-chat-main]').textContent.includes('发送完成前切走')", 'successful background send clears only its original draft')
    console.log('DESKTOP_CHAT_SESSION_CACHE_SMOKE_OK')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee-manager-launcher]')?.click()")
    await waitForRenderer(window, "document.querySelector('[data-office-employee-manager]') !== null", 'recruitment dialog opens')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-employee-manager] [data-office-edit-employees]') === null"), true, 'employee management is not nested in recruitment dialog')
    await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"关闭招聘中心\"]').click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee-editor-launcher]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-item=\"frontend-one\"]') !== null", 'existing employees load')
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-item=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-prompt]')?.value === '员工个人 Prompt'", 'personal Prompt loads (not template)')
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-role]')?.textContent.includes('前端工程师') && document.querySelector('[data-edit-employee-tools]')?.textContent.includes('编辑文件') && document.querySelector('[data-delete-employee]') !== null", 'employee tools use actual named choices')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-tool=read_file]').checked && document.querySelector('[data-edit-tool=read_file]').disabled"), true)
    assert.equal(await window.webContents.executeJavaScript("document.querySelectorAll('[data-edit-tool]').length === 5 && document.querySelector('[data-edit-tool=web_search]').indeterminate"), true, 'five groups preserve partially selected legacy web tools')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-tool=publish_task_result]') === null && !document.querySelector('[data-edit-employee-tools]').textContent.includes('file:write')"), true)
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-tool=web_search]').click(); document.querySelector('[data-edit-tool=search_office_library]').click(); document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'tools save through shared save button')
    for (const name of ['read_file', 'ls', 'glob', 'grep', 'web_search', 'web_fetch', 'image_search', 'report_to_pm', 'ask_clarification', 'mcp/offline/custom']) assert.ok(selectedToolNames.includes(name), `group save preserves ${name}`)
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-tool=web_search]').click(); document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'group disable saves')
    assert.ok(['web_search', 'web_fetch', 'image_search'].every(name => !selectedToolNames.includes(name)))
    assert.ok(['report_to_pm', 'ask_clarification', 'mcp/offline/custom'].every(name => selectedToolNames.includes(name)))
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-tool=web_search]').click(); document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'group reenable saves')
    await window.webContents.executeJavaScript("document.querySelector('[data-close-employee-editor]').click(); document.querySelector('[data-office-employee-editor-launcher]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-tool=web_search]')?.checked && document.querySelector('[data-edit-tool=search_office_library]')?.checked", 'tool choices survive reopen')
    assert.equal(await window.webContents.executeJavaScript("(() => { const text = document.querySelector('[data-edit-employee-details]')?.textContent || ''; return !text.includes('员工信息') && !text.includes('Capabilities') && !text.includes('Permissions') && !text.includes('委派能力') && !text.includes('前端开发') && !text.includes('接口实现') && !text.includes('workstation-02') && !text.includes('最近报告') })()"), true, 'employee dialog omits runtime and capability metadata')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-name]')?.disabled === false && document.querySelector('[data-edit-employee-prompt]')?.disabled === false && document.querySelector('[data-edit-employee-skills]')?.disabled === false"), true, 'Prompt and Skills remain editable for idle employee')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[role=dialog][aria-label=\"在职员工管理\"] #employee-editor-title') === null"), true, 'employee dialog has no redundant title header')
    const chooseEmployeeModel = async id => {
      await window.webContents.executeJavaScript(`document.querySelector('[data-edit-employee-model] [role="button"]').dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }))`)
      await waitForRenderer(window, `document.querySelector('[data-employee-model-option="${id}"]') !== null`, 'employee model options')
      await window.webContents.executeJavaScript(`document.querySelector('[data-employee-model-option="${id}"]').click()`)
      await waitForRenderer(window, `!document.querySelector('[role="listbox"]')`, 'employee model menu closes')
    }
    await chooseEmployeeModel('model-1')
    assert.equal(employeePersonalInfo.modelProfileId, 'model-0', 'selection alone never saves')
    await window.webContents.executeJavaScript("document.querySelector('[data-close-employee-editor]').click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee-editor-launcher]').click()")
    await waitForRenderer(window, `document.querySelector('[data-edit-employee-model]')?.textContent.includes('gpt-existing')`, 'closing discards unsaved model')
    await chooseEmployeeModel('model-1')
    await setValue('[data-edit-employee-name]', '新周礼')
    await setValue('[data-edit-employee-prompt]', '更新后的个人 Prompt')
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-skill=\"find-skills\"]').click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'employee settings saved')
    assert.deepEqual(savedPersonalInfo, { displayName: '新周礼', prompt: '更新后的个人 Prompt', skills: [], modelProfileId: 'model-1' })
    await window.webContents.executeJavaScript("document.querySelector('[data-close-employee-editor]').click(); document.querySelector('[data-office-employee-editor-launcher]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-prompt]')?.value === '更新后的个人 Prompt'", 'reopening retains personal settings')
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-skill=\"custom-installed\"]').click(); document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'user-installed skill can be added')
    assert.deepEqual(savedPersonalInfo.skills, ['custom-installed'])
    availableSkillNames.push('newly-installed')
    await waitForRenderer(window, "document.querySelector('[data-edit-skill=\"newly-installed\"]') !== null", 'new install appears while editor stays open')
    await setValue('[data-edit-employee-name]', '未保存的名称')
    availableSkillNames = availableSkillNames.filter(name => name !== 'custom-installed')
    await waitForRenderer(window, "document.querySelector('[data-edit-skill=\"custom-installed\"]') === null", 'uninstalled selected skill disappears')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-name]')?.value"), '未保存的名称', 'skills refresh preserves unsaved form')
    await setValue('[data-edit-employee-name]', '新周礼')
    await window.webContents.executeJavaScript("document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-status]')?.textContent.includes('已保存')", 'saving after uninstall succeeds')
    assert.deepEqual(savedPersonalInfo.skills, [])
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-item=\"research-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-name]')?.value === '黎明'", 'second employee can be selected before deletion')
    await window.webContents.executeJavaScript("document.querySelector('[data-delete-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-delete-employee-confirm]') !== null", 'delete confirmation is explicit')
    await window.webContents.executeJavaScript("document.querySelector('[data-delete-employee-confirm]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-item=\"research-one\"]') === null && document.body.innerText.includes('员工已删除')", 'deleted employee disappears from management list')
    assert.equal(deletedEmployeeId, 'research-one')
    const screenshotDir = path.resolve(__dirname, '..', 'output')
    await fs.mkdir(screenshotDir, { recursive: true })
    await fs.writeFile(path.join(screenshotDir, 'stage1-employee-editor.png'), (await window.webContents.capturePage()).toPNG())
    await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-item=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-name]')?.value === '新周礼'", 'remaining employee stays available after deletion')
    employeeState = 'working'
    await waitForRenderer(window, "document.querySelector('[data-edit-employee-name]')?.disabled && document.querySelector('[data-save-employee]')?.disabled", 'working employee locks editor')
    assert.equal(await window.webContents.executeJavaScript(`document.querySelector('[data-edit-employee-model] [role="button"]').getAttribute('aria-disabled')`), 'true')
    employeeState = 'idle'
    await waitForRenderer(window, "document.querySelector('[data-save-employee]')?.disabled === false", 'idle employee unlocks editor')
    rejectPersonalSave = true
    await setValue('[data-edit-employee-prompt]', '未保存草稿')
    await window.webContents.executeJavaScript("document.querySelector('[data-save-employee]').click()")
    await waitForRenderer(window, "document.querySelector('[data-employee-edit-error]')?.textContent.includes('保存失败测试')", 'save failure visible')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-edit-employee-prompt]').value"), '未保存草稿')
    assert.equal(employeePersonalInfo.prompt, '更新后的个人 Prompt')
    console.log('DESKTOP_EMPLOYEE_EDIT_SMOKE_OK')
    await window.webContents.executeJavaScript("document.querySelector('[data-close-employee-editor]').click(); document.querySelector('[aria-label=\"关闭招聘中心\"]').click(); document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-group-message=\"group-ai-reply\"]')?.textContent.includes('旧周礼')", 'historical name does not change with employee rename')
    assert.ok(groupAfterSequences.some((sequence) => sequence > 0), `group chat did not use afterSequence: ${groupAfterSequences.join(',')}`)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-group-message=\"group-ai-reply\"] [data-sender-avatar]').getAttribute('data-sender-avatar')"), 'ash')
    await window.webContents.executeJavaScript("window.dispatchEvent(new CustomEvent('office-employees-updated', {detail: []}))")
    await waitForRenderer(window, "document.querySelector('[data-group-message=\"group-ai-reply\"]')?.textContent.includes('旧周礼')", 'history remains after employee removal')
    console.log('DESKTOP_GROUP_DISPLAY_SNAPSHOT_SMOKE_OK')
    workflowFixture = { workflow_id: 'workflow-smoke', pm_employee_id: 'pm', current_stage: 'quality_checking', status: 'active',
      execution_error: '质量门禁未通过，禁止交付', phase_runs: [],
      task_plan: [{ task_id: 'test', employee_id: 'tester', task: '自动测试', kind: 'test', status: 'succeeded', run_id: 'pm-parent' }],
      quality: { test: { status: 'failed', summary: '发现回归缺陷', blockingIssues: ['接口测试失败'], sourceRunId: 'pm-parent', evidence: [{ toolCallId: 'check' }] } } }
    await window.webContents.executeJavaScript("window.dispatchEvent(new Event('office-employees-updated'))")
    await waitForRenderer(window, "document.querySelector('[data-office-employee=\"frontend-one\"]')", 'roster available for native work checks')
    nativeWorkActive = true
    nativeRunTargets = [{ employeeId: 'pm', runId: 'native-work-test', projectId: null, workId: 'work-one' }]
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-history]')?.textContent.includes('历史任务与验证记录')", 'historical records remain readable')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-workflow]')"), null)
    await setEditorText('')
    await waitForRenderer(window, "document.querySelector('[data-office-pause]') !== null", 'one pause button for group work')
    await waitForRenderer(window, "document.body.innerText.includes('PM 本次工作结果 native-work-test')", 'server-started workflow Run publishes without switching chat')
    const readsAfterFinal = nativeRunReads
    await new Promise(resolve => setTimeout(resolve, 2200))
    assert.equal(nativeRunReads, readsAfterFinal, 'drained native Run must stop polling')
    nativeRunTargets = [{ employeeId: 'pm', runId: 'native-work-doc', projectId: null, workId: 'work-one' }]
    await waitForRenderer(window, "document.body.innerText.includes('PM 本次工作结果 native-work-doc')", 'subsequent workflow Run publishes after previous wait ends')
    const beforeGroupPause = groupPauseCalls
    await window.webContents.executeJavaScript("document.querySelector('[data-office-history] details').open = true; document.querySelector('[data-office-pause]').click()")
    await waitForRenderer(window, "document.body.innerText.includes('之前的群聊任务已暂停') && document.querySelector('[data-office-send]') !== null", 'pause returns composer to send')
    assert.equal(groupPauseCalls, beforeGroupPause + 1)
    assert.equal(await window.webContents.executeJavaScript("document.querySelectorAll('[data-office-workflow-toggle], [data-office-cancel]').length"), 0)
    await setEditorText('继续执行')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-pause]') !== null", 'natural language continues group work')
    assert.equal(await window.webContents.executeJavaScript("document.querySelectorAll('[data-office-message-input]').length"), 1)
    console.log('DESKTOP_NATIVE_WORK_LIFECYCLE_SMOKE_OK')
    // Employee update event above deliberately removed the roster. Restore it
    // for switching, without changing the historical message snapshot.
    await window.webContents.executeJavaScript("window.dispatchEvent(new Event('office-employees-updated'))")
    await waitForRenderer(window, "document.querySelector('[data-office-employee=\"frontend-one\"]')", 'employee roster restores for cache checks')
    workflowReadDelay = 1600
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-history]') === null", 'workflow hidden in private conversation')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-history]')?.textContent.includes('历史任务与验证记录')", 'workflow cached while refreshing', 700)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-history] details').open"), true)
    workflowReadDelay = 0
    await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"frontend-one\"]').click()")
    await waitForRenderer(window, "document.querySelector('[data-office-history]') === null", 'private selected before project race')
    groupMessages = [...groupMessages, { messageId: 'late-old-project', sequence: groupMessages.length + 1,
      senderKind: 'employee', senderId: 'frontend-one', senderDisplayName: '周礼', senderAvatar: 'ash',
      content: '旧项目迟到消息', mentionEmployeeIds: [], sourceRunId: null, createdAt: Date.now() }]
    captureNextGroupRead = true
    await window.webContents.executeJavaScript("document.querySelector('[data-office-group]').click()")
    await new Promise(resolve => setTimeout(resolve, 150))
    assert.equal(capturedGroupReads, 1)
    groupMessages = []
    workflowFixture = null
    nativeRunTargets = []
    nativeWorkActive = false
    workspaceFixture = { projectId: 'project-new', path: 'D:/smoke/new', name: '新测试项目', kind: 'empty', tree: [] }
    window.webContents.send('office-desktop:event', { type: 'workspace.updated' })
    await waitForRenderer(window, "!document.querySelector('[data-group-message=\"group-ai-reply\"]') && document.querySelector('[data-office-message-input]')?.value === ''", 'project change clears chat cache and draft before fetching', 700)
    await new Promise(resolve => setTimeout(resolve, 1600))
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-main]').textContent.includes('旧项目迟到消息')"), false)
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-office-message-input]').value"), '')
    console.log('DESKTOP_CHAT_CACHE_SCOPE_SMOKE_OK')
  } finally {
    if (!window.isDestroyed()) window.destroy()
    removeSmokeIpc()
  }
}

run().then(() => app.exit(0)).catch((error) => {
  console.error(error)
  removeSmokeIpc()
  app.exit(1)
})
