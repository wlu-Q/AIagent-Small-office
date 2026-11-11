"""Read Office work and human-input projections from native execution facts."""
from collections.abc import Mapping

from deerflow.office.execution_facts import PendingAsk, WorkIdentity
from deerflow.runtime.context_keys import OFFICE_WORK_IDENTITY_CONTEXT_KEY
from deerflow.runtime.runs.execution import checkpoint_run_id


def work_identity(record):
    contract = (getattr(record, 'kwargs', None) or {}).get('launch_contract') or {}
    raw = (contract.get('trusted_context') or {}).get(OFFICE_WORK_IDENTITY_CONTEXT_KEY)
    if raw is None:
        return None
    work = WorkIdentity.from_record(raw)
    if work.user_id != record.user_id or work.thread_id != record.thread_id:
        raise ValueError('Run work scope mismatch')
    return work


async def pending_ask(checkpointer, record):
    """Only an unresolved interrupt on the current owned checkpoint is answerable.

    Do not infer questions from chat, AI prose, or historical checkpoints.
    """
    if checkpointer is None or record.status != 'interrupted' or getattr(record, 'error', None):
        return None
    if (record.metadata or {}).get('office_control') == 'cancelled':
        return None
    checkpoint = await checkpointer.aget_tuple({'configurable': {'thread_id': record.thread_id, 'checkpoint_ns': ''}})
    if checkpoint_run_id(checkpoint) != record.run_id:
        return None
    work = work_identity(record) or WorkIdentity(record.user_id, (record.metadata or {}).get('office_project_id'),
                                               record.thread_id, f'run:{record.run_id}')
    for _, channel, values in (getattr(checkpoint, 'pending_writes', None) or []):
        if channel != '__interrupt__':
            continue
        for item in values if isinstance(values, (list, tuple)) else [values]:
            payload = getattr(item, 'value', None)
            if isinstance(payload, Mapping) and payload.get('source') == 'ask_clarification':
                return PendingAsk.from_interrupt(work=work, record=record, payload=payload)
    return None
