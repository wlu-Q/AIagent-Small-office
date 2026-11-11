"""Content-free diagnostics for the final model input; never changes messages."""

import hashlib
import json
from collections import Counter

from langchain_core.messages import HumanMessage


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def measure_context(batches, invocation_params, previous=None):
    """Return numeric metrics and transient fingerprints, not prompt contents.

    Counts are characters, NOT provider tokens. Combined system blocks cannot
    reliably be split back into their original prompt/Work Context sources.
    Prefix equality is only a diagnostic; it does not prove provider caching.
    """
    groups = {}
    fingerprints = []
    ids = Counter()
    non_text_blocks = 0
    for batch in batches:
        last_user = next((i for i in reversed(range(len(batch)))
                          if isinstance(batch[i], HumanMessage)
                          and not batch[i].additional_kwargs.get('hide_from_ui')), -1)
        for i, msg in enumerate(batch):
            content = msg.content
            if isinstance(content, str):
                text = content
            else:
                blocks = content if isinstance(content, list) else []
                text = ''.join(b if isinstance(b, str) else (b['text'] if isinstance(b.get('text'), str) else '')
                               for b in blocks if isinstance(b, str) or isinstance(b, dict))
                non_text_blocks += sum(not isinstance(b, str) and not (isinstance(b, dict) and 'text' in b)
                                       for b in blocks)
            extra = msg.additional_kwargs
            role = msg.type
            source = ('memory' if extra.get('dynamic_context_kind') == 'memory'
                      else 'hidden_context' if role == 'human' and extra.get('hide_from_ui')
                      else 'latest_user' if role == 'human' and i == last_user
                      else 'earlier_user' if role == 'human'
                      else role)
            calls = getattr(msg, 'tool_calls', None) or []
            tool_call_chars = len(_json(calls)) if calls else 0
            chars = len(text) + tool_call_chars
            group = groups.setdefault(source, {'messages': 0, 'chars': 0, 'content_chars': 0, 'tool_call_chars': 0})
            group['messages'] += 1
            group['chars'] += chars
            group['content_chars'] += len(text)
            group['tool_call_chars'] += tool_call_chars
            if msg.id:
                ids[msg.id] += 1
            # Fingerprints only live on the callback instance, never in events.
            signature = _json([role, content, calls, getattr(msg, 'tool_call_id', None)])
            fingerprints.append((hashlib.sha256(signature.encode()).digest(), chars))
    params = invocation_params if isinstance(invocation_params, dict) else {}
    schemas = params.get('tools', params.get('functions'))
    schema_text = _json(schemas) if isinstance(schemas, list) else None
    schema_hash = hashlib.sha256(schema_text.encode()).digest() if schema_text is not None else None
    current = (fingerprints, schema_hash)
    prefix = 0
    if previous is not None:
        for old, new in zip(previous[0], fingerprints):
            if old[0] != new[0]:
                break
            prefix += 1
    return {
        'unit': 'characters_not_tokens',
        'sources': groups,
        'message_count': len(fingerprints),
        'message_chars': sum(g['chars'] for g in groups.values()),
        'non_text_blocks': non_text_blocks,
        'duplicate_message_ids': sum(v - 1 for v in ids.values()),
        'tool_schema_chars': len(schema_text) if schema_text is not None else None,
        'tool_count': len(schemas) if isinstance(schemas, list) else None,
        'same_tool_schema': previous[1] == schema_hash if previous and previous[1] is not None and schema_hash is not None else None,
        'unchanged_prefix_messages': prefix if previous is not None else None,
        'unchanged_prefix_chars': sum(v[1] for v in fingerprints[:prefix]) if previous is not None else None,
    }, current
