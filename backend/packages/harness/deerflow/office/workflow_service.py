"""Read and retire legacy Workflow records; never advance or schedule work."""


class WorkflowService:
    def __init__(self, *, store, project_service):
        self.store = store
        self.projects = project_service

    def get(self, workflow_id=None):
        binding = self.projects.get_binding()
        if binding is None:
            if workflow_id:
                raise FileNotFoundError('Workflow requires the current project binding')
            return None
        wf = self.store.get(workflow_id, project_id=binding.project_id) if workflow_id else self.store.current(project_id=binding.project_id)
        if workflow_id and wf is None:
            raise FileNotFoundError('Workflow not found in current project')
        return wf

    async def cancel(self, workflow_id):
        wf = self.get(workflow_id)
        return self.store.retire(wf.workflow_id, project_id=wf.project_id)
