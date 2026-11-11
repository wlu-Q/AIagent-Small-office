from __future__ import annotations

import re

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY

def verification_evidence(steps: list[dict]) -> tuple[dict, ...]:
    """Pair actual native tool calls with outputs; never accept an unmatched report."""
    calls = {}
    seen = set()
    results = []
    allowed = {'read_file', 'ls', 'glob', 'grep', 'bash'}
    failed = re.compile(r'Exit Code:\s*-?[1-9]\d*|timed out|timeout exceeded|\b[1-9]\d*\s+(?:failed|failing)\b|# fail [1-9]\d*', re.I)
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Malformed native trace')
        if step.get('type') == 'ai':
            for call in step.get('tool_calls') or []:
                if isinstance(call, dict) and call.get('id') and call.get('name') in allowed:
                    if call['id'] in seen:
                        raise ValueError('Repeated tool call identity')
                    seen.add(call['id'])
                    calls[call['id']] = call
        elif step.get('type') == 'tool' and step.get('tool_call_id') in calls:
            call = calls.pop(step['tool_call_id'])
            content = str(step.get('content') or '')
            metadata = (step.get('additional_kwargs') or {}).get(TOOL_META_KEY) or {}
            if step.get('status') == 'error' or metadata.get('status') == 'error':
                raise ValueError('Tool trace contains failed verification')
            # File/search results are data. Only command observations use the
            # existing command/test failure checks, beyond tool-call status.
            if call['name'] == 'bash' and (content.startswith('Error:') or failed.search(content)):
                raise ValueError('Tool trace contains failed verification')
            results.append({'toolCallId': call['id'], 'tool': call['name'], 'summary': content[:2000]})
    if calls:
        raise ValueError('Tool trace is incomplete')
    return tuple(results)
