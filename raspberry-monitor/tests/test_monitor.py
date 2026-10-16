import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from monitor import active_alerts
from storage import events, history, init_db, record


def fixture(ts=1_750_000_000):
    return {
        'ts': ts,
        'cpu': {'percent': 41.5, 'load': [0.58, 0.55, 0.52]},
        'memory': {'percent': 53}, 'temperature': 61.3,
        'disks': [{'path': '/', 'percent': 14}],
        'network': {'down': 123, 'up': 55},
    }


def test_alert_thresholds():
    item = fixture()
    assert active_alerts(item) == []
    item['temperature'] = 81
    item['disks'][0]['percent'] = 95
    assert {x['id'] for x in active_alerts(item)} == {'temperature', 'disk:/'}


def test_alert_transitions_and_recording():
    with TemporaryDirectory() as d:
        db = os.path.join(d, 'test.sqlite3')
        init_db(db)
        sample = fixture()
        with patch('time.time', return_value=sample['ts'] + 1):
            record(db, sample, [])
            assert len(history(db, 3600)) == 1
            assert events(db) == []
        sample['ts'] += 20
        sample['temperature'] = 83
        record(db, sample, active_alerts(sample))
        sample['ts'] += 20
        record(db, sample, active_alerts(sample))
        assert len(events(db)) == 1  # No alert spam.
        sample['ts'] += 20
        sample['temperature'] = 61
        record(db, sample, [])
        assert len(events(db)) == 2
        assert events(db)[0]['active'] == 0


def test_no_data_path_leak_when_not_mounted():
    # Disk helper should not count a mounted directory's missing mount as a real drive.
    from monitor import _disk_snapshot
    with patch.dict(os.environ, {'MONITOR_DISKS': '/, /definitely-unmounted-abc'}):
        disks = _disk_snapshot()
    assert all(d['path'] != '/definitely-unmounted-abc' for d in disks)
