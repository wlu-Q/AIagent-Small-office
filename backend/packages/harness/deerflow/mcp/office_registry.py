"""The Gateway's per-service discovery results. Persistent authorization lives in tools.yaml."""
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from threading import RLock
from uuid import UUID, uuid4, uuid5

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.extensions_store import edit_extensions, lock, read_raw
from deerflow.mcp.session_pool import get_session_pool


@dataclass
class Service:
    revision: str
    grant: str
    status: str = 'connecting'
    tools: list = field(default_factory=list)
    error: str = ''


class OfficeMcpRegistry:
    def __init__(self):
        self.lock = RLock()
        self.services = {}
        self.active = False
        self.tasks = set()

    def register(self, name, raw):
        with self.lock:
            previous = self.services.pop(name, None)
            if not raw or not raw.get('enabled', True):
                return previous, None
            identity = uuid5(UUID(raw['office_activation']), json.dumps([name, target(raw)])).hex
            service = Service(raw['office_revision'], 'mcp-service/' + identity)
            self.services[name] = service
            return previous, service

    def entries(self):
        with self.lock:
            return dict(self.services)

    def loaded_tools(self):
        with self.lock:
            return [tool for service in self.services.values() for tool in service.tools]

    async def discover(self, name, entry):
        from deerflow.mcp.tools import get_mcp_tools
        server = None
        try:
            with lock:
                raw = read_raw().get('mcpServers', {}).get(name, {})
                if raw.get('office_revision') != entry.revision:
                    return
                for group in ('headers', 'env'):
                    for value in raw.get(group, {}).values():
                        if re.fullmatch(r'\$[A-Za-z_][A-Za-z_0-9]*', value) and not os.getenv(value[1:]):
                            with self.lock:
                                entry.status, entry.error = 'failed', f'Missing environment variable {value[1:]}'
                            return
                config = ExtensionsConfig.from_file()
            server = config.mcp_servers.get(name)
            if server is None or getattr(server, 'office_revision', '') != entry.revision:
                return
            config = config.model_copy(update={'mcp_servers': {name: server}})
            async with asyncio.timeout(25):
                tools = await get_mcp_tools(config, strict=True)
            from deerflow.office.tool_contract import build_tool_catalog
            reserved = {entry.name for entry in build_tool_catalog(include_mcp=False).values()}
            for tool in tools:
                tool.metadata = {**tool.metadata, 'office_mcp_server': name, 'office_mcp_grant': entry.grant,
                                 'office_mcp_binding': uuid4().hex}
            with self.lock:
                if self.services.get(name) is not entry:
                    return
                names = {tool.name for key, service in self.services.items() if key != name for tool in service.tools}
                if any(tool.name in names or tool.name in reserved for tool in tools):
                    raise ValueError('MCP tool name collision')
                entry.tools, entry.status = tools, 'ready'
        except Exception as exc:
            # SDK errors can include headers, subprocess arguments, or token-bearing URLs.
            cause = exc
            while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
                cause = cause.exceptions[0]
            response = getattr(cause, 'response', None)
            with self.lock:
                entry.status = 'failed'
                if response is not None:
                    entry.error = f'HTTP {response.status_code}'
                elif isinstance(cause, FileNotFoundError) and server and server.type == 'stdio':
                    entry.error = f'Executable unavailable: {server.command}'
                else:
                    entry.error = type(cause).__name__

    def binding_is_current(self, name, binding):
        with self.lock:
            return any(t.name == name and t.metadata['office_mcp_binding'] == binding
                       for service in self.services.values() for t in service.tools)

    async def apply(self, name, previous, entry):
        if previous:
            await get_session_pool().close_server(f'{name}:{previous.revision}')
        if entry:
            await self.discover(name, entry)

    def read_services(self, *, refresh=False):
        with lock:
            with edit_extensions() as data:
                servers = data.get('mcpServers', {})
                for server in servers.values():
                    initialize_service(server)
                    if refresh:
                        server['office_revision'] = uuid4().hex
            names = self.entries().keys() | servers.keys()
            return [(name, *self.register(name, servers.get(name))) for name in sorted(names)]

    async def start(self):
        self.active = True
        for update in self.read_services():
            task = asyncio.create_task(self.apply(*update))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def stop(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        with self.lock:
            self.services.clear()
            self.active = False
        await get_session_pool().close_all()


def target(server):
    return (server.get('type') or server.get('transport', 'stdio'), server.get('url') or '', server.get('command') or '', tuple(server.get('args') or ()))


def initialize_service(server):
    server.setdefault('office_revision', uuid4().hex)
    if server.get('enabled', True):
        server.setdefault('office_activation', uuid4().hex)
    else:
        server.pop('office_activation', None)


registry = OfficeMcpRegistry()
