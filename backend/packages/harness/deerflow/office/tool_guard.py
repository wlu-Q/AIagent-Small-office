"""Final model and execution boundary, including middleware-generated tools."""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from deerflow.office.tool_manifest import load_tool_manifest, manifest_revision


class EmployeeToolGuard(AgentMiddleware):
    def __init__(self, agent_dir, contract):
        self.agent_dir = agent_dir
        self.revision = contract.revision
        self.names = frozenset(contract.runtime_names)

    def _check_revision(self):
        if manifest_revision(load_tool_manifest(self.agent_dir)) != self.revision:
            raise RuntimeError('Employee tool contract changed during work; restart with current configuration')

    def _model_request(self, request):
        self._check_revision()
        return request.override(tools=[t for t in request.tools if
            (t.get('name') if isinstance(t, dict) else t.name) in self.names])

    def wrap_model_call(self, request, handler):
        return handler(self._model_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._model_request(request))

    def _denied(self, request):
        self._check_revision()
        if request.tool_call['name'] not in self.names:
            return ToolMessage(content='Tool is not authorized by this employee tool contract.',
                               tool_call_id=request.tool_call['id'], name=request.tool_call['name'], status='error')
        return None

    def wrap_tool_call(self, request, handler):
        denied = self._denied(request)
        return denied if denied is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        denied = self._denied(request)
        return denied if denied is not None else await handler(request)
