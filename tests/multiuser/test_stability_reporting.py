import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.multiuser.db import Reservation


def reporter():
    path = Path(__file__).with_name('stability_multiuser.py')
    spec = importlib.util.spec_from_file_location('stability_reporting', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.write_report


def test_real_postgres_aggregates_produce_numeric_json_report(store, tmp_path):
    with store.session() as session:
        calls = session.scalar(select(func.coalesce(func.sum(Reservation.amount), 0)))
        size = session.scalar(text('SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c '
                                   'JOIN pg_namespace n ON n.oid=c.relnamespace '
                                   'WHERE n.nspname=current_schema()'))
    assert isinstance(calls, Decimal) and isinstance(size, Decimal)
    path = tmp_path / 'nested' / 'stability.json'
    report = reporter()(path, {
        'stuck_tasks': 0, 'max_attempt': 1, 'model_calls': 0,
        'committed_calls': calls, 'peak_original_file_bytes': 10,
        'database_bytes': size, 'worker_restarts': 1,
        'duration_seconds': 1800.5, 'passed': True,
    })
    persisted = json.loads(path.read_text(encoding='utf8'))
    assert persisted == report
    assert type(persisted['committed_calls']) is int
    assert type(persisted['database_bytes']) is int
    assert persisted['database_bytes'] > 0
    assert persisted['duration_seconds'] == 1800.5


def test_reporting_does_not_truncate_or_hide_invalid_statistics(tmp_path):
    fields = ('stuck_tasks', 'max_attempt', 'model_calls', 'committed_calls',
              'peak_original_file_bytes', 'database_bytes', 'worker_restarts')
    report = dict.fromkeys(fields, 0)
    report['committed_calls'] = Decimal('1.5')
    path = tmp_path / 'invalid.json'
    with pytest.raises(ValueError, match='Non-integral'):
        reporter()(path, report)
    assert not path.exists()
    report['committed_calls'] = Decimal(2)
    report['unsupported'] = Decimal('0.5')
    with pytest.raises(TypeError):
        reporter()(path, report)
    assert not path.exists()
