"""One-time, offline workspace identity migration; no Git/file changes."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, UTC
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def backup_database(db, path):
    backup = path.with_name(path.name + '.before-workspace-identity-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ'))
    with closing(sqlite3.connect(backup)) as target:
        db.backup(target)
    return backup


def remove_retired_fields(value):
    return {key: item for key, item in value.items()
            if key not in {'dependency_refs', 'dependency_versions', 'dependency_outcomes', 'direct_run_id', 'latest_run_id',
                           'integration_status', 'integration_input_ref', 'published_commit', 'published_files',
                           'published_at', 'published_tree', 'has_unpublished_changes', 'conflict_files',
                           'changed_files', 'live_execution_ids', 'result_commit', 'result_ref'}}


def migrate_database(path: Path, *, apply=False):
    path = path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)) as db, db:
        db.row_factory = sqlite3.Row
        columns = {row['name'] for row in db.execute('PRAGMA table_info(execution_workspaces)')}
        if columns == {'workspace_id', 'user_id', 'project_id', 'state_json'}:
            rows = db.execute('SELECT workspace_id,state_json FROM execution_workspaces').fetchall()
            updates = [(json.dumps(clean), row['workspace_id']) for row in rows
                       if (clean := remove_retired_fields(json.loads(row['state_json']))) != json.loads(row['state_json'])]
            uses = db.execute('SELECT use_key,selection_json FROM workspace_uses').fetchall()
            selections = [(json.dumps(clean), row['use_key']) for row in uses
                          if (clean := remove_retired_fields(json.loads(row['selection_json']))) != json.loads(row['selection_json'])]
            if not updates and not selections:
                return {'status': 'already_current', 'workspaces': len(rows)}
            report = {'status': 'ready', 'workspaces': len(rows), 'retired_records': len(updates) + len(selections)}
            if not apply:
                return report
            backup = backup_database(db, path)
            db.executemany('UPDATE execution_workspaces SET state_json=? WHERE workspace_id=?', updates)
            db.executemany('UPDATE workspace_uses SET selection_json=? WHERE use_key=?', selections)
            return {**report, 'status': 'migrated', 'backup': str(backup)}
        expected = {'workspace_id', 'user_id', 'project_id', 'thread_id', 'work_id', 'employee_id', 'owner_key', 'batch_key', 'state_json'}
        if columns != expected:
            raise ValueError('Unexpected workspace schema; no migration performed')
        rows = db.execute('SELECT * FROM execution_workspaces').fetchall()
        report = {'status': 'ready', 'workspaces': len(rows),
                  'existing_directories': sum(bool((value := json.loads(row['state_json'])).get('root_path')
                                                    and Path(value['root_path']).is_dir()) for row in rows)}
        if not apply:
            return report
        backup = backup_database(db, path)
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE workspace_identity_new (workspace_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, '
                   'project_id TEXT NOT NULL, state_json TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS workspace_uses (use_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, '
                   'user_id TEXT NOT NULL, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, work_id TEXT NOT NULL, '
                   'employee_id TEXT NOT NULL, owner_key TEXT NOT NULL, selection_json TEXT NOT NULL, run_id TEXT)')
        for row in rows:
            value = remove_retired_fields(json.loads(row['state_json']))
            value.pop('batch_key', None)
            value['source_workspace_id'] = None
            if value.get('root_path') and not value.get('checkout_commit'):
                value['checkout_commit'] = value['baseline_commit']
            work = {key: row[key] for key in ('user_id', 'project_id', 'thread_id', 'work_id')}
            selection = dict(mode='new', source_workspace_id=None, project_path=value['project_path'])
            db.execute('INSERT INTO workspace_identity_new VALUES (?,?,?,?)',
                       (row['workspace_id'], row['user_id'], row['project_id'], json.dumps(value)))
            db.execute('INSERT INTO workspace_uses VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (digest([work, row['employee_id'], row['owner_key']]), row['workspace_id'], row['user_id'],
                        row['project_id'], row['thread_id'], row['work_id'], row['employee_id'], row['owner_key'],
                        json.dumps(selection), value.get('latest_run_id') or value.get('direct_run_id')))
        db.execute('DROP TABLE execution_workspaces')
        db.execute('ALTER TABLE workspace_identity_new RENAME TO execution_workspaces')
        db.execute('DROP TABLE IF EXISTS execution_baselines')
        db.execute('CREATE INDEX IF NOT EXISTS workspace_use_work ON workspace_uses(user_id,project_id,work_id)')
        db.commit()
        return {**report, 'status': 'migrated', 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--maintenance', action='store_true', help='Confirm Gateway and Desktop are stopped')
    args = parser.parse_args()
    if args.apply and not args.maintenance:
        parser.error('--apply requires --maintenance; stop Gateway and Desktop first')
    print(json.dumps(migrate_database(args.database, apply=args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
