"""Seed persisted legacy records without retaining a production stage writer."""
import json
import sqlite3


def seed_history(store, *, workflow_id='history', project_id='p', user_id=None, **fields):
    payload = dict(workflow_id=workflow_id, project_id=project_id,
                   user_id=user_id or store.user_id, pm_employee_id='pm',
                   current_stage='executing_employees', status='active',
                   created_at=1, updated_at=2, **fields)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store.path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_results (
            run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, message_id TEXT NOT NULL,
            visible_text TEXT NOT NULL, UNIQUE(workflow_id,message_id));''')
        db.execute('INSERT OR REPLACE INTO workflows VALUES(?,?,?,?,?,?)',
                   (workflow_id, payload['user_id'], project_id, json.dumps(payload), 1, 2))
    return store.get(workflow_id, project_id=project_id)
