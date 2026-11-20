"""Local Office service configuration shared by settings and employee requests."""
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deerflow.config.extensions_config import reload_extensions_config
from deerflow.config.extensions_store import edit_extensions, lock, read_raw
from deerflow.mcp.office_registry import initialize_service, registry, target


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['http', 'stdio']
    url: str = ''
    headers: dict[str, str] = Field(default_factory=dict)
    command: Literal['npx', 'uvx'] | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_connection(self):
        if self.type == 'stdio':
            if not self.command or self.url or self.headers:
                raise ValueError('Expected a local command with args and env')
        else:
            url = urlsplit(self.url)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.fragment:
                raise ValueError('Expected an HTTP(S) service URL without credentials or fragment')
            if self.command or self.args or self.env:
                raise ValueError('Expected a URL with optional headers')
        return self


class ServiceChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['create', 'edit', 'enable', 'disable', 'delete', 'reconnect']
    revision: str = ''
    connection: Connection | None = None


class ServiceConflict(ValueError):
    pass


def prepare_server(old, incoming):
    # These two fields are owned by the Gateway even when using the bulk API.
    server = {k: v for k, v in incoming.items() if k not in ('office_revision', 'office_activation')}
    same = old is not None and target(old) == target(server)
    for key in ('headers', 'env'):
        values = dict(server.get(key) or {})
        for field, value in values.items():
            if value == '***':
                if not same or field not in old.get(key, {}):
                    raise ValueError('Enter a new credential for the new target')
                values[field] = old[key][field]
        if values or key in server:
            server[key] = values
    if same and old.get('enabled', True) and server.get('enabled', True) and old.get('office_activation'):
        server['office_activation'] = old['office_activation']
    initialize_service(server)
    return server


def list_services():
    with lock:
        entries = registry.entries()
        rows = []
        for name, raw in read_raw().get('mcpServers', {}).items():
            entry = entries.get(name)
            rows.append(dict(name=name, enabled=raw.get('enabled', True), revision=raw.get('office_revision', ''),
                type=raw.get('type') or raw.get('transport', 'stdio'), url=raw.get('url') or '', command=raw.get('command') or '',
                args=raw.get('args', []), env={k: '***' for k in raw.get('env', {})},
                headers={k: '***' for k in raw.get('headers', {})}, status=entry.status if entry else 'disabled',
                count=len(entry.tools) if entry else 0, error=entry.error if entry else ''))
        return rows


async def change_service(name: str, change: ServiceChange):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Use letters, digits, hyphens or underscores for the service name')
    with lock:
        with edit_extensions() as data:
            servers = data.setdefault('mcpServers', {})
            old = servers.get(name)
            creating = change.action == 'create'
            if creating != (old is None) or (old and old.get('office_revision', '') != change.revision):
                raise ServiceConflict('Service changed; reopen its settings')
            server = None
            if change.action in ('create', 'edit'):
                if change.connection is None:
                    raise ValueError('Connection required')
                connection = change.connection.model_dump(exclude_defaults=True)
                same = old and target(old) == target(connection)
                server = dict(old) if same else {}
                for key in Connection.model_fields:
                    server.pop(key, None)
                server.update(connection, enabled=old.get('enabled', True) if old else True)
                server = prepare_server(old, server)
            elif change.action == 'delete':
                del servers[name]
            else:
                if change.connection:
                    raise ValueError('Unexpected connection')
                if change.action == 'reconnect' and not old.get('enabled', True):
                    raise ValueError('Enable the service before reconnecting')
                server = dict(old)
                if change.action != 'reconnect':
                    server['enabled'] = change.action == 'enable'
                server = prepare_server(old, server)
            if server is not None:
                servers[name] = server
        previous, entry = registry.register(name, server)
        reload_extensions_config()
    await registry.apply(name, previous, entry)
    return list_services()
