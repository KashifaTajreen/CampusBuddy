"""Offline scope guards; no real keys, network calls or operator environment edits."""

import base64
import json
import os
from types import SimpleNamespace

import pytest

from scripts.configure_campusos_kt import SetupError, validate, replace_fields, connection_parameters
from scripts import configure_campusos_kt as setup

REF = 'abcdefghijklmnopqrst'


@pytest.fixture
def config():
    return dict(PROJECT_NAME='CampusOS-KT', PROJECT_REGION='ap-south-1',
                SUPABASE_PROJECT_REF=REF, SUPABASE_URL='https://' + REF + '.supabase.co',
                SUPABASE_PUBLISHABLE_KEY='sb_publishable_synthetic_not_a_real_key',
                MIGRATION_DATABASE_URL='postgresql://postgres:fake@db.' + REF + '.supabase.co:5432/postgres')


def test_valid_scope_needs_no_network(config):
    assert validate(config, database=True) == REF
    params = connection_parameters(config['MIGRATION_DATABASE_URL'], REF)
    assert params['sslmode'] == 'require'
    assert 'default_transaction_read_only=on' in params['options']
    assert params['connect_timeout'] == 5


@pytest.mark.parametrize('field,value', [
    ('SUPABASE_PROJECT_REF', 'lhbwctfuarovmswtejgn'),
    ('SUPABASE_URL', 'https://different-project.supabase.co'),
    ('SUPABASE_PUBLISHABLE_KEY', 'sb_secret_forbidden'),
    ('PROJECT_REGION', 'us-east-1'),
    ('MIGRATION_DATABASE_URL', 'postgresql://postgres:fake@db.otherproject.supabase.co/postgres'),
    ('MIGRATION_DATABASE_URL', 'postgresql://postgres:fake@db.' + REF + '.supabase.co/postgres?sslmode=disable'),
    ('MIGRATION_DATABASE_URL', 'postgresql://postgres:fake@db.' + REF + '.supabase.co/postgres?options=unexpected'),
    ('MIGRATION_DATABASE_URL', 'postgresql://postgres:fake@db.' + REF + '.supabase.co:6543/postgres'),
])
def test_unsafe_scope_is_rejected_without_credentials_in_error(config, field, value):
    config[field] = value
    with pytest.raises(SetupError) as exc:
        validate(config, database=True)
    assert 'fake@' not in str(exc.value)


def test_pooler_is_bound_to_project_and_mumbai(config):
    config['MIGRATION_DATABASE_URL'] = 'postgresql://postgres.' + REF + ':fake@aws-1-ap-south-1.pooler.supabase.com:5432/postgres'
    assert validate(config, database=True) == REF
    config['MIGRATION_DATABASE_URL'] = config['MIGRATION_DATABASE_URL'].replace('ap-south-1', 'us-east-1')
    with pytest.raises(SetupError):
        validate(config, database=True)


def test_legacy_service_role_key_is_never_a_frontend_key(config):
    payload = base64.urlsafe_b64encode(json.dumps(dict(role='service_role', ref=REF)).encode()).decode().rstrip('=')
    config['SUPABASE_PUBLISHABLE_KEY'] = 'header.' + payload + '.signature'
    with pytest.raises(SetupError):
        validate(config)


def test_prepare_environment_update_does_not_write_source(tmp_path):
    path = tmp_path / 'local.env'
    path.write_text('SUPABASE_URL="old"\nKEEP_ME="unrelated"\n')
    prepared = replace_fields(path, dict(SUPABASE_URL='https://' + REF + '.supabase.co'))
    assert 'old' in path.read_text()
    assert 'KEEP_ME="unrelated"' in prepared
    assert prepared.count('SUPABASE_URL=') == 1


def test_duplicate_environment_values_stop_before_overwriting(tmp_path):
    path = tmp_path / 'local.env'
    path.write_text('SUPABASE_URL=one\nSUPABASE_URL=two\n')
    with pytest.raises(SetupError):
        replace_fields(path, dict(SUPABASE_URL='new'))
    assert 'one' in path.read_text()


def test_backup_keeps_credentials_out_of_argv_and_removes_ambient_targets(config, tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    monkeypatch.setenv('PGHOSTADDR', 'forbidden-ambient-target')
    monkeypatch.setenv('PGSERVICE', 'unrelated-service')
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'artifacts/campusos-kt-preflight.json').write_text(json.dumps(
        dict(project_ref=REF, read_only_verified=True, server_version='17.6')))
    dump = tmp_path / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
    restore = dump.with_name('pg_restore.exe' if os.name == 'nt' else 'pg_restore')
    dump.touch()
    restore.touch()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert config['MIGRATION_DATABASE_URL'] not in ' '.join(args)
        if '--version' in args:
            return SimpleNamespace(stdout='pg_dump (PostgreSQL) 17.6')
        if '--file' in args:
            env = kwargs['env']
            assert env['PGHOST'] == 'db.' + REF + '.supabase.co'
            assert env['PGPASSWORD'] == 'fake'
            assert 'PGHOSTADDR' not in env and 'PGSERVICE' not in env
            assert 'default_transaction_read_only=on' in env['PGOPTIONS']
            setup.Path(args[args.index('--file') + 1]).write_bytes(b'synthetic archive test only')
            return SimpleNamespace(stdout=b'')
        assert '--list' in args
        return SimpleNamespace(stdout=b'; synthetic archive inventory')

    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.backup_new(config, REF, str(dump))
    assert result['archive_list_verified'] and result['database_writes'] is False
    assert len(calls) == 3


def test_sanitized_errors_do_not_disclose_database_credentials():
    error = ValueError('password authentication failed; postgresql://user:private-password@private-host/db')
    result = setup.sanitized_failure(error)
    assert 'private-password' not in result and 'private-host' not in result
    assert 'login rejected' in result
