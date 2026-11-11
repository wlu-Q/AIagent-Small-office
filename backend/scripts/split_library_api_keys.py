"""Offline, backed-up split of the library's shared encrypted credential."""
import argparse
import json
from pathlib import Path
import sqlite3
from datetime import datetime


def migrate(database: Path, *, apply=False):
    with sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True) as connection:
        changes = []
        for name, text in connection.execute("SELECT key,value FROM meta WHERE key IN ('config','rebuild')"):
            record = json.loads(text)
            config = record['config'] if name == 'rebuild' else record
            if 'secret' not in config:
                continue
            secret = config.pop('secret')
            for kind in ('embedding', 'rerank'):
                config.setdefault(f'{kind}_secret', secret)
            changes.append((json.dumps(record), name))
        if apply and changes:
            backup = database.with_name(f'{database.name}.before-split-keys-{datetime.now():%Y%m%d%H%M%S%f}')
            with sqlite3.connect(backup) as target:
                connection.backup(target)
            connection.executemany('UPDATE meta SET value=? WHERE key=?', changes)
    return [name for _, name in changes]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--maintenance', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.maintenance:
        parser.error('Stop Gateway first, then pass --apply --maintenance')
    changed = migrate(args.database, apply=args.apply)
    print(f'{"Migrated" if args.apply else "Would migrate"}: {", ".join(changed) or "no changes"}')
