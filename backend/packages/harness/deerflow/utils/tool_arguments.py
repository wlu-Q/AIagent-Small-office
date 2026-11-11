"""Narrow provider argument normalization shared by execution and provenance checks."""

import json

_ARRAY_ARGUMENTS = {
    'publish_task_result': ('source_workspace_ids',),
}


def normalize_tool_arguments(name: str, arguments: dict) -> tuple[dict, dict]:
    normalized = dict(arguments)
    changes = {}
    for field in _ARRAY_ARGUMENTS.get(name, ()):
        original = arguments.get(field)
        if not isinstance(original, str):
            continue
        try:
            parsed = json.loads(original)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            normalized[field] = parsed
            changes[field] = {'original': original, 'normalized': parsed}
    return normalized, changes
