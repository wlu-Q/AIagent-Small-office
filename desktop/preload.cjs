const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('officeDesktopBridge', {
  listExecutionApprovals: () => ipcRenderer.invoke('office-desktop:listExecutionApprovals'),
  decideExecutionApproval: (id, hash, decision) => ipcRenderer.invoke('office-desktop:decideExecutionApproval', id, hash, decision),
  listMcpServices: () => ipcRenderer.invoke('office-desktop:listMcpServices'),
  changeMcpService: (name, change) => ipcRenderer.invoke('office-desktop:changeMcpService', name, change),
  getOfficeEmployeeTools: (id) => ipcRenderer.invoke('office-desktop:getOfficeEmployeeTools', id),
  saveOfficeEmployeeTools: (id, payload) => ipcRenderer.invoke('office-desktop:saveOfficeEmployeeTools', id, payload),
  officeLibrary: (payload) => ipcRenderer.invoke('office-desktop:library', payload),
  openExternalUrl: (url) => ipcRenderer.invoke('office-desktop:openExternalUrl', url),
  previewOfficeFile: (payload) => ipcRenderer.invoke('office-desktop:previewOfficeFile', payload),
  revealOfficeFile: (payload) => ipcRenderer.invoke('office-desktop:revealOfficeFile', payload),
  getServiceStatus: () => ipcRenderer.invoke('office-desktop:getServiceStatus'),
  pingService: () => ipcRenderer.invoke('office-desktop:pingService'),
  getWorkspaceSummary: () => ipcRenderer.invoke('office-desktop:getWorkspaceSummary'),
  selectAndBindWorkspace: () => ipcRenderer.invoke('office-desktop:selectAndBindWorkspace'),
  listOfficeEmployees: () => ipcRenderer.invoke('office-desktop:listOfficeEmployees'),
  getOfficeEmployee: (employeeId) => ipcRenderer.invoke('office-desktop:getOfficeEmployee', employeeId),
  listOfficeEmployeeTemplates: () => ipcRenderer.invoke('office-desktop:listOfficeEmployeeTemplates'),
  listModels: () => ipcRenderer.invoke('office-desktop:listModels'),
  listOfficeModels: () => ipcRenderer.invoke('office-desktop:listOfficeModels'),
  addOfficeModel: (payload) => ipcRenderer.invoke('office-desktop:addOfficeModel', payload),
  getOfficeModel: (modelId) => ipcRenderer.invoke('office-desktop:getOfficeModel', modelId),
  updateOfficeModel: (modelId, payload) => ipcRenderer.invoke('office-desktop:updateOfficeModel', modelId, payload),
  setOfficeDefaultModel: (modelId) => ipcRenderer.invoke('office-desktop:setOfficeDefaultModel', modelId),
  listSkills: () => ipcRenderer.invoke('office-desktop:listSkills'),
  getOfficeBootstrap: () => ipcRenderer.invoke('office-desktop:getOfficeBootstrap'),
  getOfficeSettings: () => ipcRenderer.invoke('office-desktop:getOfficeSettings'),
  getLanguage: () => ipcRenderer.invoke('office-desktop:getLanguage'),
  setLanguage: (language) => ipcRenderer.invoke('office-desktop:setLanguage', language),
  saveOfficeSettings: (payload) => (
    ipcRenderer.invoke('office-desktop:saveOfficeSettings', payload)
  ),
  getUserMemory: () => ipcRenderer.invoke('office-desktop:getUserMemory'),
  getEmployeeMemory: (employeeId) => ipcRenderer.invoke('office-desktop:getEmployeeMemory', employeeId),
  listWorkLogDays: (employeeId, before) => ipcRenderer.invoke('office-desktop:listWorkLogDays', employeeId, before),
  getWorkLogDay: (employeeId, day) => ipcRenderer.invoke('office-desktop:getWorkLogDay', employeeId, day),
  createMemoryFact: (payload) => ipcRenderer.invoke('office-desktop:createMemoryFact', payload),
  updateMemoryFact: (factId, payload) => (
    ipcRenderer.invoke('office-desktop:updateMemoryFact', factId, payload)
  ),
  deleteMemoryFact: (factId) => ipcRenderer.invoke('office-desktop:deleteMemoryFact', factId),
  testOfficeBootstrapModel: (payload) => (
    ipcRenderer.invoke('office-desktop:testOfficeBootstrapModel', payload)
  ),
  initializeOfficeBootstrap: (payload) => (
    ipcRenderer.invoke('office-desktop:initializeOfficeBootstrap', payload)
  ),
  createOfficeEmployee: (payload) => ipcRenderer.invoke('office-desktop:createOfficeEmployee', payload),
  saveOfficeEmployeePersonalInfo: (employeeId, payload) => (
    ipcRenderer.invoke('office-desktop:saveOfficeEmployeePersonalInfo', employeeId, payload)
  ),
  deleteOfficeEmployee: (employeeId) => (
    ipcRenderer.invoke('office-desktop:deleteOfficeEmployee', employeeId)
  ),
  sendOfficeMessage: (command) => ipcRenderer.invoke('office-desktop:sendOfficeMessage', command),
  listOfficeGroupMessages: (afterSequence) => (
    ipcRenderer.invoke('office-desktop:listOfficeGroupMessages', afterSequence)
  ),
  listOfficeConversationMessages: (employeeId, kind) => (
    ipcRenderer.invoke('office-desktop:listOfficeConversationMessages', employeeId, kind)
  ),
  listOfficeQueue: () => ipcRenderer.invoke('office-desktop:listOfficeQueue'),
  editOfficeQueue: (queueId, text) => ipcRenderer.invoke('office-desktop:editOfficeQueue', queueId, text),
  deleteOfficeQueue: (queueId) => ipcRenderer.invoke('office-desktop:deleteOfficeQueue', queueId),
  cancelOfficeRun: (employeeId, runId) => (
    ipcRenderer.invoke('office-desktop:cancelOfficeRun', employeeId, runId)
  ),
  getCurrentOfficeWorkflow: () => ipcRenderer.invoke('office-desktop:getCurrentOfficeWorkflow'),
  getOfficeWorkflow: (id) => ipcRenderer.invoke('office-desktop:getOfficeWorkflow', id),
  getOfficeGroupExecution: () => ipcRenderer.invoke('office-desktop:getOfficeGroupExecution'),
  pauseOfficeGroup: () => ipcRenderer.invoke('office-desktop:pauseOfficeGroup'),
  resetOfficeGroup: () => ipcRenderer.invoke('office-desktop:resetOfficeGroup'),
  resumeOfficeRun: (employeeId, runId, payload) => (
    ipcRenderer.invoke('office-desktop:resumeOfficeRun', employeeId, runId, payload)
  ),
  getOfficeRun: (employeeId, runId) => (
    ipcRenderer.invoke('office-desktop:getOfficeRun', employeeId, runId)
  ),
  listOfficeRuns: (employeeId, kind, limit) => (
    ipcRenderer.invoke('office-desktop:listOfficeRuns', employeeId, kind, limit)
  ),
  listOfficeRunEvents: (employeeId, runId, afterSeq) => (
    ipcRenderer.invoke('office-desktop:listOfficeRunEvents', employeeId, runId, afterSeq)
  ),
  getZoomFactor: () => ipcRenderer.invoke('office-desktop:getZoomFactor'),
  setZoomFactor: (factor) => ipcRenderer.invoke('office-desktop:setZoomFactor', factor),
  zoomBy: (delta) => ipcRenderer.invoke('office-desktop:zoomBy', delta),
  resetZoom: () => ipcRenderer.invoke('office-desktop:resetZoom'),
  onServiceEvent: (listener) => {
    const wrapped = (_event, payload) => listener(payload)
    ipcRenderer.on('office-desktop:event', wrapped)
    return () => ipcRenderer.removeListener('office-desktop:event', wrapped)
  },
})
