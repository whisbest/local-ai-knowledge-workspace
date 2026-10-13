"""Create a consistent private baseline snapshot without exporting any secrets."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination already exists')
    if destination == source or source in destination.parents:
        raise ValueError('Snapshot must be outside the source')
    destination.mkdir(parents=True)
    database = source / 'state/current/app.sqlite'
    target = destination / 'state/current/app.sqlite'
    target.parent.mkdir(parents=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(target) as copied:
        original.backup(copied)
        if copied.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot integrity failed')
    for name in ('documents', 'parsed'):
        path = source / 'state/current' / name
        if path.exists():
            shutil.copytree(path, target.parent / name)
    manifest = {str(p.relative_to(destination)).replace('\\', '/'):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in destination.rglob('*') if p.is_file()}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    print(json.dumps({'files': len(snapshot(args.source, args.destination)), 'secrets': 'excluded'}))
