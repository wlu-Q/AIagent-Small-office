import json

from deerflow.config.paths import Paths
from deerflow.office.execution_facts import ExecutionReceiptStore
from deerflow.office.terminal import ACTIVE, TerminalManager


def test_list_filters_finished_without_losing_history_or_unknown(tmp_path):
    store = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    manager = TerminalManager(store)
    statuses = ACTIVE | {'unknown', 'succeeded', 'failed', 'cancelled', 'timeout', 'rejected'}
    with store._connect() as db:
        for status in statuses:
            value = {'execution_id': status, 'status': status, 'workspace_id': 'workspace', 'stdout': 'retained'}
            db.execute('INSERT INTO command_executions VALUES (?, ?, ?, ?)',
                       (status, 'workspace', status, json.dumps(value)))
    assert {item['status'] for item in manager.list('workspace')} == ACTIVE | {'unknown'}
    assert {item['status'] for item in manager.list('workspace', include_finished=True)} == statuses
    assert manager.list('different-workspace', include_finished=True) == []
    assert manager._get('succeeded', 'workspace')['stdout'] == 'retained'
