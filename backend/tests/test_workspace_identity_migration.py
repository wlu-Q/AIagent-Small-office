import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from deerflow.config.paths import Paths
from deerflow.office.execution_facts import WorkIdentity
from deerflow.office.execution_workspace import ExecutionWorkspaceService


spec = importlib.util.spec_from_file_location('migrate_workspace_identity', Path(__file__).parents[1] / 'scripts/migrate_workspace_identity.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_offline_migration_preserves_workspaces_receipts_and_commands(tmp_path):
    paths = Paths(str(tmp_path / 'data'))
    path = paths.user_dir('alice') / 'office-execution-receipts.v1.sqlite3'
    path.parent.mkdir(parents=True)
    project = tmp_path / 'project'
    project.mkdir()
    work = WorkIdentity('alice', 'project', 'thread', 'work')
    value = dict(work.to_record(), workspace_id='a' * 32, employee_id='employee', owner_key='direct:employee',
                 project_path=str(project), root_path=None, batch_key='old-batch', dependency_refs=[],
                 baseline_commit=None, result_commit=None, direct_run_id='run')
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE execution_workspaces (
            workspace_id TEXT PRIMARY KEY,user_id TEXT,project_id TEXT,thread_id TEXT,work_id TEXT,
            employee_id TEXT,owner_key TEXT,batch_key TEXT,state_json TEXT);
            CREATE TABLE execution_baselines(batch_key TEXT PRIMARY KEY,state_json TEXT);
            CREATE TABLE command_executions(execution_id TEXT PRIMARY KEY,state_json TEXT);
            CREATE TABLE retained_receipts(receipt_key TEXT PRIMARY KEY,payload TEXT);''')
        db.execute('INSERT INTO execution_workspaces VALUES (?,?,?,?,?,?,?,?,?)',
                   ('a' * 32, 'alice', 'project', 'thread', 'work', 'employee', 'direct:employee', 'old-batch', json.dumps(value)))
        db.execute('INSERT INTO execution_baselines VALUES (?,?)', ('old-batch', '{}'))
        db.execute('INSERT INTO command_executions VALUES (?,?)', ('command', '{"status":"running"}'))
        db.execute('INSERT INTO retained_receipts VALUES (?,?)', ('receipt', 'unchanged'))
    before = path.read_bytes()
    assert migration.migrate_database(path)['status'] == 'ready'
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='maintenance'):
        ExecutionWorkspaceService(paths=paths, user_id='alice')
    result = migration.migrate_database(path, apply=True)
    assert result['status'] == 'migrated'
    assert Path(result['backup']).is_file()
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT payload FROM retained_receipts').fetchone()[0] == 'unchanged'
        assert db.execute('SELECT state_json FROM command_executions').fetchone()[0] == '{"status":"running"}'
        assert db.execute("SELECT name FROM sqlite_master WHERE name='execution_baselines'").fetchone() is None
        state = json.loads(db.execute('SELECT state_json FROM execution_workspaces').fetchone()[0])
        assert 'batch_key' not in state
        assert 'dependency_refs' not in state
        assert 'direct_run_id' not in state
        assert state['workspace_id'] == value['workspace_id']
    service = ExecutionWorkspaceService(paths=paths, user_id='alice')
    assert service.allocate(work, employee_id='employee', owner_key='direct:employee', project_path=project) == {'workspace_id': 'a' * 32}
    assert migration.migrate_database(path, apply=True)['status'] == 'already_current'
    assert list(project.iterdir()) == []
    path.unlink()
    Path(result['backup']).unlink()


def test_current_schema_migration_removes_retired_protocol_only(tmp_path):
    paths = Paths(str(tmp_path / 'data'))
    work = WorkIdentity('alice', 'project', 'thread', 'work')
    project = tmp_path / 'project'
    project.mkdir()
    service = ExecutionWorkspaceService(paths=paths, user_id='alice')
    reference = service.allocate(work, employee_id='employee', owner_key='task', project_path=project)
    path = paths.user_dir('alice') / 'office-execution-receipts.v1.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        original_state = json.loads(db.execute('SELECT state_json FROM execution_workspaces').fetchone()[0])
        original_selection = json.loads(db.execute('SELECT selection_json FROM workspace_uses').fetchone()[0])
        db.execute('UPDATE execution_workspaces SET state_json=?', (json.dumps(dict(
            original_state, dependency_versions=[{'version': 'old'}], dependency_outcomes=['old'],
            latest_run_id='old-run', direct_run_id='old-direct', integration_status='conflict', conflict_files=['file.txt'],
            published_commit='old', published_files=['old.txt'], result_commit='cached-head', result_ref='cached-ref')),))
        db.execute('UPDATE workspace_uses SET selection_json=?',
                   (json.dumps(dict(original_selection, dependency_refs=[])),))
    before = path.read_bytes()
    report = migration.migrate_database(path)
    assert report['retired_records'] == 2
    assert path.read_bytes() == before
    result = migration.migrate_database(path, apply=True)
    assert result['status'] == 'migrated'
    with closing(sqlite3.connect(path)) as db:
        assert json.loads(db.execute('SELECT state_json FROM execution_workspaces').fetchone()[0]) == original_state
        assert json.loads(db.execute('SELECT selection_json FROM workspace_uses').fetchone()[0]) == original_selection
    assert service.allocate(work, employee_id='employee', owner_key='task', project_path=project) == reference
    assert migration.migrate_database(path, apply=True)['status'] == 'already_current'
    assert list(project.iterdir()) == []
    path.unlink()
    Path(result['backup']).unlink()


def test_migration_rejects_unknown_schema_without_rewriting(tmp_path):
    path = tmp_path / 'unknown.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE execution_workspaces(unexpected TEXT)')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Unexpected'):
        migration.migrate_database(path, apply=True)
    assert path.read_bytes() == before
    path.unlink()
