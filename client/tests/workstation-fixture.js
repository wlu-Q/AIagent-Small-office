(() => {
  window.__workstationEmployees = [{
    id: 'workstation-preview-employee', displayName: 'Ethan', workstationId: 'workstation-04',
    state: 'working', templateId: 'fixture', role: 'Developer', avatar: 'ethan', modelProfileId: null,
    isPm: false, statusDetail: '', lastReport: '', currentTaskId: null, enabled: true,
    disabledReason: '', createdAt: 0, updatedAt: 0, canDelegate: false,
  }]
  window.officeDesktopBridge = {
    getLanguage: async () => 'zh-CN',
    getOfficeBootstrap: async () => ({ initialized: true, companyName: 'Workstation test', defaultModel: null, thinkingLevel: 'off' }),
    getWorkspaceSummary: async () => ({ workspace: null }),
    onServiceEvent: () => () => {},
    getServiceStatus: async () => ({ mode: 'electron', lifecycle: 'connected', transport: 'http', detail: '', lastUpdatedAt: Date.now() }),
    pingService: async () => ({}),
    listOfficeEmployees: async () => ({ employees: window.__workstationEmployees.map(employee => ({ ...employee })) }),
    listOfficeEmployeeTemplates: async () => ({ templates: [] }),
    listModels: async () => ({ models: [] }),
    listSkills: async () => ({ skills: [] }),
    listExecutionApprovals: async () => ({ items: [] }),
    getCurrentOfficeWorkflow: async () => ({ workflow: null }),
    getOfficeGroupExecution: async () => ({ activeCount: 0, paused: false, stopping: false, heldQueueIds: [], error: '', runTargets: [] }),
    listOfficeGroupMessages: async () => ({ messages: [], nextAfterSequence: 0 }),
    listOfficeConversationMessages: async () => ({ messages: [] }),
    listOfficeQueue: async () => ({ items: [] }),
    listOfficeRuns: async () => ({ runs: [] }),
    getOfficeSettings: async () => ({ thinkingLevel: 'off' }),
    zoomBy: async () => {}, resetZoom: async () => {},
  }
})()
