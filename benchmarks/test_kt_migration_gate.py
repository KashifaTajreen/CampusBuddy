"""Offline approval/source/backup gate checks; no database or migration execution."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import migrate_campusos_kt as gate


@pytest.fixture
def review(tmp_path,monkeypatch):
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    manifest=tmp_path/'artifacts/campusos-kt-migration-approval.json'
    manifest.parent.mkdir()
    monkeypatch.setattr(gate,'MANIFEST',manifest)
    source=tmp_path/'draft.sql'
    source.write_text('-- synthetic reviewed source')
    archive=tmp_path/'.scratch/campusos-kt/backup.dump'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b'synthetic backup fixture')
    payload=dict(project_ref=gate.TARGET,revision='0008',source_hashes={'draft.sql':gate.checksum(source)},
                 backup='.scratch/campusos-kt/backup.dump',backup_sha256=gate.checksum(archive))
    data=dict(plan=payload,review_sha256=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    manifest.write_text(json.dumps(data))
    (manifest.parent/'campusos-kt-local-rehearsal.json').write_text(json.dumps(dict(status='passed',original_unchanged=True)))
    return data,source,archive


def test_approved_sources_are_validated_without_network(review):
    data,_,_=review
    assert gate.approved_sources()==data


def test_changed_reviewed_sql_refuses_execution(review):
    _,source,_=review
    source.write_text('-- changed after review')
    with pytest.raises(gate.SetupError,match='source changed'):
        gate.approved_sources()


def test_changed_backup_refuses_execution(review):
    _,_,archive=review
    archive.write_bytes(b'corrupted')
    with pytest.raises(gate.SetupError,match='backup hash mismatch'):
        gate.approved_sources()


@pytest.mark.parametrize('project,digest',[(None,None),('old-project','reviewed'),(gate.TARGET,'unreviewed')])
def test_execution_requires_exact_acknowledgements(project,digest):
    with pytest.raises(gate.SetupError):
        gate.execution_permission(SimpleNamespace(execute=True,approved_project=project,review_sha256=digest),
                                  dict(review_sha256='reviewed'))


def test_check_mode_keeps_database_environment_unchanged(monkeypatch,capsys):
    monkeypatch.setattr(gate,'approved_sources',lambda:dict(review_sha256='reviewed'))
    monkeypatch.setattr(gate,'dotenv_values',lambda *a,**k:{})
    monkeypatch.setattr(gate,'validate',lambda *a,**k:gate.TARGET)
    monkeypatch.setattr(gate,'inspect_target',lambda *a,**k:dict(read_only=True))
    monkeypatch.setattr('sys.argv',['migration-gate','--check'])
    monkeypatch.setenv('DATABASE_URL','sqlite:///:memory:')
    assert gate.main()==0
    assert gate.os.environ['DATABASE_URL']=='sqlite:///:memory:'
    assert '"database_writes": false' in capsys.readouterr().out


def test_missing_approval_stops_before_reading_credentials(monkeypatch):
    monkeypatch.setattr(gate,'approved_sources',lambda:dict(review_sha256='reviewed'))
    def forbidden(*args,**kwargs):
        raise AssertionError('Credentials should not be loaded without execution acknowledgement')
    monkeypatch.setattr(gate,'dotenv_values',forbidden)
    monkeypatch.setattr('sys.argv',['migration-gate','--execute'])
    assert gate.main()==2


def test_another_database_in_the_same_project_is_refused_before_connect(monkeypatch):
    import psycopg
    def forbidden(*args,**kwargs):
        raise AssertionError('A mismatched database must not be contacted')
    monkeypatch.setattr(psycopg,'connect',forbidden)
    values={'MIGRATION_DATABASE_URL':'postgresql://postgres:synthetic@db.'+gate.TARGET+'.supabase.co/other_database'}
    with pytest.raises(gate.SetupError,match='another database is refused'):
        gate.inspect_target(values)
