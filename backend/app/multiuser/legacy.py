"""Read-only V1 format reader, never a live V2 backend."""
import json
import sqlite3
from pathlib import Path


def read_records(source):
    database = Path(source).resolve() / 'state/current/app.sqlite'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Legacy database integrity check failed')
        connection.row_factory = sqlite3.Row
        return [{**dict(row), 'payload': json.loads(row['payload'])}
                for row in connection.execute('SELECT * FROM records ORDER BY created_at, id')]
