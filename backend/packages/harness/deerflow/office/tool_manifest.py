"""The employee's sole persisted tool selection. No Skill or runtime logic."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

_TOOL_ID = re.compile(r'^(?:[A-Za-z0-9_-]+|mcp-service/[a-f0-9]{32}|mcp/[^/\s]+/[A-Za-z0-9_-]+)$')


@dataclass(frozen=True)
class EmployeeToolManifest:
    version: int
    tools: tuple[str, ...]

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise ValueError('Unsupported tools.yaml version; expected 1')
        if not isinstance(self.tools, tuple) or any(not isinstance(t, str) or not _TOOL_ID.fullmatch(t) for t in self.tools):
            raise ValueError('tools must contain exact tool identifiers')
        if len(set(self.tools)) != len(self.tools):
            raise ValueError('Duplicate tool identifiers')

    def to_dict(self):
        return {'version': self.version, 'tools': sorted(self.tools)}


def load_tool_manifest(agent_dir: Path) -> EmployeeToolManifest:
    path = agent_dir / 'tools.yaml'
    if not path.is_file():
        raise FileNotFoundError('Employee tools.yaml missing; run employee tool manifest migration')
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise ValueError('Invalid tools.yaml') from exc
    if not isinstance(data, dict) or set(data) != {'version', 'tools'} or not isinstance(data['tools'], list):
        raise ValueError('tools.yaml requires version and tools list only')
    return EmployeeToolManifest(data['version'], tuple(data['tools']))


def manifest_revision(manifest: EmployeeToolManifest) -> str:
    return hashlib.sha256(json.dumps(manifest.to_dict(), sort_keys=True).encode()).hexdigest()


def save_tool_manifest(agent_dir: Path, manifest: EmployeeToolManifest, *, expected_revision: str) -> str:
    """Caller holds the employee lifecycle lock; replace atomically on the same disk."""
    path = agent_dir / 'tools.yaml'
    actual = manifest_revision(load_tool_manifest(agent_dir)) if path.exists() else ''
    if expected_revision != actual:
        raise RuntimeError('Employee tool revision conflict; reload before saving')
    fd, name = tempfile.mkstemp(prefix='.tools-', suffix='.tmp', dir=agent_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            yaml.safe_dump(manifest.to_dict(), stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return manifest_revision(manifest)
