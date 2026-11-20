"""MCP client using langchain-mcp-adapters."""

import logging
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)


def _local_http_client(headers=None, timeout=None, auth=None):
    import httpx
    from mcp.shared._httpx_utils import MCP_DEFAULT_TIMEOUT, MCP_DEFAULT_SSE_READ_TIMEOUT

    return httpx.AsyncClient(trust_env=False, headers=headers, auth=auth,
                             timeout=timeout or httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT))


def build_server_params(server_name: str, config: McpServerConfig) -> dict[str, Any]:
    """Build server parameters for MultiServerMCPClient.

    Args:
        server_name: Name of the MCP server.
        config: Configuration for the MCP server.

    Returns:
        Dictionary of server parameters for langchain-mcp-adapters.
    """
    transport_type = config.type or "stdio"
    params: dict[str, Any] = {"transport": transport_type}

    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{server_name}' with stdio transport requires 'command' field")
        params["command"] = config.command
        params["args"] = config.args
        # Launch the npm CLI with Node on Windows; never interpolate arguments into cmd.exe.
        if os.name == 'nt' and config.command == 'npx' and getattr(config, 'office_activation', None):
            node = shutil.which('node.exe')
            if not node:
                raise FileNotFoundError('node.exe')
            cli = Path(node).parent / 'node_modules/npm/bin/npx-cli.js'
            if not cli.is_file():
                raise FileNotFoundError('npx-cli.js')
            params.update(command=node, args=[str(cli), *config.args])
        # Add environment variables if present
        if config.env:
            params["env"] = config.env
    elif transport_type in ("sse", "http"):
        if not config.url:
            raise ValueError(f"MCP server '{server_name}' with {transport_type} transport requires 'url' field")
        params["url"] = config.url
        if getattr(config, 'office_activation', None) and urlsplit(config.url).hostname in ('localhost', '127.0.0.1', '::1'):
            params['httpx_client_factory'] = _local_http_client
        # Add headers if present
        if config.headers:
            params["headers"] = config.headers
    else:
        raise ValueError(f"MCP server '{server_name}' has unsupported transport type: {transport_type}")

    return params


def build_servers_config(extensions_config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """Build servers configuration for MultiServerMCPClient.

    Args:
        extensions_config: Extensions configuration containing all MCP servers.

    Returns:
        Dictionary mapping server names to their parameters.
    """
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    if not enabled_servers:
        logger.info("No enabled MCP servers found")
        return {}

    servers_config = {}
    for server_name, server_config in enabled_servers.items():
        try:
            servers_config[server_name] = build_server_params(server_name, server_config)
            logger.info(f"Configured MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to configure MCP server '{server_name}': {e}")

    return servers_config
