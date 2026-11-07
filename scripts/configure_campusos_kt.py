"""Local configuration and SELECT-only preflight for the new CampusOS-KT project.

No migrations, account creation, role grants or database writes are implemented.
Credentials are loaded from an ignored local file and never included in reports.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / '.env.campusos-kt.local'
OLD_PROJECT = 'lhbwctfuarovmswtejgn'


class SetupError(ValueError):
    pass


def sanitized_failure(exc):
    """Map errors to fixed labels; never expose libpq/HTTP exception text."""
    message = str(exc).lower()
    state = getattr(exc, 'sqlstate', None)
    if state == '28P01' or 'password authentication failed' in message:
        return 'Database login rejected (password authentication failed); verify credentials or pooler refresh after password rotation.'
    if 'tenant or user not found' in message:
        return 'Pooler rejected the project/user: verify the exact Connect panel endpoint and username.'
    if 'getaddrinfo' in message or 'could not translate host' in message or 'name or service' in message:
        return 'DNS resolution failed.'
    if 'certificate' in message or 'ssl' in message or 'tls' in message:
        return 'TLS/certificate negotiation failed.'
    if 'ipv6' in message or 'network is unreachable' in message:
        return 'IP connectivity failed; an IPv4 session-pooler endpoint may be required.'
    if 'timeout' in message or 'timed out' in message:
        return 'Connection or query timeout.'
    if state == '42501':
        return 'Database permission denied for read-only inspection.'
    return type(exc).__name__ + ': details withheld; no write attempted.'


def validate(values, database=False):
    required = ['SUPABASE_PROJECT_REF', 'SUPABASE_URL', 'SUPABASE_PUBLISHABLE_KEY']
    if database:
        required.append('MIGRATION_DATABASE_URL')
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise SetupError('Fill these local fields: ' + ', '.join(missing))
    ref = values['SUPABASE_PROJECT_REF']
    if not re.fullmatch(r'[a-z0-9]{20}', ref) or ref == OLD_PROJECT:
        raise SetupError('A new, exact CampusOS-KT project reference is required; old project refused.')
    if values['SUPABASE_URL'] != 'https://' + ref + '.supabase.co':
        raise SetupError('Project URL and approved project reference must match exactly.')
    if values.get('PROJECT_NAME') != 'CampusOS-KT' or values.get('PROJECT_REGION') != 'ap-south-1':
        raise SetupError('Local project confirmation must identify CampusOS-KT in ap-south-1.')
    key = values['SUPABASE_PUBLISHABLE_KEY']
    if not re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]+', key):
        try:
            data = json.loads(base64.urlsafe_b64decode(key.split('.')[1] + '==='))
        except (ValueError, IndexError, TypeError) as exc:
            raise SetupError('Enter a publishable key or legacy anon key, not a secret/service-role key.') from exc
        if data.get('role') != 'anon' or data.get('ref') != ref:
            raise SetupError('Legacy anon key metadata does not match the new project. Secret keys refused.')
    if values.get('MIGRATION_DATABASE_URL'):
        validate_database(values['MIGRATION_DATABASE_URL'], ref)
    if values.get('API_DATABASE_URL'):
        validate_database(values['API_DATABASE_URL'], ref)
    return ref


def validate_database(value, ref):
    try:
        url = make_url(value)
        direct = url.host == 'db.' + ref + '.supabase.co'
        pooler = bool(url.host and re.fullmatch(r'aws-\d+-ap-south-1\.pooler\.supabase\.com', url.host)
                      and url.username and url.username.endswith('.' + ref))
        if (url.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg')
                or not (direct or pooler) or not url.password or not url.database
                or (url.port or 5432) != 5432):
            raise ValueError
        if set(url.query) - {'sslmode', 'sslrootcert', 'channel_binding'}:
            raise ValueError
        if url.query.get('sslmode', 'require') not in ('require', 'verify-ca', 'verify-full'):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise SetupError('Database URL must target the same NEW project, use port 5432 and require TLS; details withheld.') from exc
    return url


def connection_parameters(value, ref):
    url = validate_database(value, ref)
    return dict(host=url.host, port=url.port or 5432, user=url.username, password=url.password,
                dbname=url.database, sslmode=url.query.get('sslmode', 'require'),
                **{k: v for k, v in url.query.items() if k != 'sslmode'},
                connect_timeout=5, application_name='campusos_kt_read_only_preflight',
                options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_fields(path, changes):
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    for key, value in changes.items():
        value = json.dumps(value)  # dotenv quoted values, never shell text.
        pattern = re.compile(r'^' + re.escape(key) + r'=.*$', re.MULTILINE)
        if len(pattern.findall(content)) > 1:
            raise SetupError('Duplicate configuration field; no automatic overwrite permitted.')
        if pattern.search(content):
            content = pattern.sub(lambda _: key + '=' + value, content)
        else:
            content = content.rstrip('\n') + '\n' + key + '=' + value + '\n'
    return content


def apply_local(values, ref):
    directory = ROOT / '.scratch/campusos-kt' / ('config-backup-' + str(time.time_ns()))
    directory.mkdir(parents=True, exist_ok=False)
    changes = {
        ROOT / '.env': dict(SUPABASE_URL=values['SUPABASE_URL'],
                            SUPABASE_PUBLISHABLE_KEY=values['SUPABASE_PUBLISHABLE_KEY'],
                            SUPABASE_SERVICE_ROLE_KEY='', STORAGE_BACKEND='local',
                            FRONTEND_ORIGIN='http://localhost:3001'),
        ROOT / 'frontend/.env.local': dict(NEXT_PUBLIC_SUPABASE_URL=values['SUPABASE_URL'],
                            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=values['SUPABASE_PUBLISHABLE_KEY'],
                            NEXT_PUBLIC_API_URL='http://localhost:8001', NEXT_PUBLIC_ONBOARDING_TENANT_ID=''),
    }
    # Prepare every update before touching either active file.
    prepared = {p: replace_fields(p, c) for p, c in changes.items()}
    for path in changes:
        if path.exists():
            backup = directory / ('backend.env' if path.name == '.env' else 'frontend.env')
            shutil.copyfile(path, backup)
            if sha(path) != sha(backup):
                raise SetupError('Local configuration backup verification failed; active files unchanged.')
    for path, content in prepared.items():
        path.write_text(content, encoding='utf-8')
    return dict(local_configuration_updated=True, project_ref=ref,
                configuration_backup=str(directory.relative_to(ROOT)),
                database_url_unchanged=True, api_started=False,
                note='Runtime database credentials must be separately reviewed; migration credentials were not copied to the API.')


def inspect_auth(values, ref):
    import httpx
    report = dict(project_ref=ref, configured_name='CampusOS-KT', configured_region='ap-south-1',
                  database_writes=False, old_project_accessed=False)
    # Neither discovery nor API errors are allowed to follow redirects to another project.
    with httpx.Client(timeout=7, trust_env=False, follow_redirects=False) as client:
        response = client.get(values['SUPABASE_URL'] + '/auth/v1/settings',
                              headers={'apikey': values['SUPABASE_PUBLISHABLE_KEY']})
        report['auth_settings_http_status'] = response.status_code
        if response.status_code == 200:
            report['google_provider_enabled'] = response.json().get('external', {}).get('google') is True
        response = client.get(values['SUPABASE_URL'] + '/auth/v1/.well-known/jwks.json')
        report['jwks_http_status'] = response.status_code
        if response.status_code == 200:
            report['jwks_algorithms'] = sorted({k.get('alg', 'unspecified') for k in response.json().get('keys', [])})
    report['redirect_allowlist_verified'] = False
    report['live_google_login_verified'] = False
    return report


def inspect(values, ref):
    import psycopg
    from psycopg import sql
    report = inspect_auth(values, ref)
    params = connection_parameters(values['MIGRATION_DATABASE_URL'], ref)
    with psycopg.connect(**params) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1, current_setting(\'transaction_read_only\'), current_setting(\'server_version\')')
            one, read_only, version = cursor.fetchone()
            if one != 1 or read_only != 'on':
                raise SetupError('Read-only database protection was not established.')
            report.update(database_connected=True, read_only_verified=True, server_version=version,
                          tls_in_use=bool(connection.pgconn.ssl_in_use),
                          certificate_hostname_verification=params['sslmode'] == 'verify-full')
            cursor.execute('SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname=current_user')
            role = cursor.fetchone()
            report['inspection_role_flags'] = dict(superuser=role[0], bypass_rls=role[1], create_role=role[2])
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
            names = [r[0] for r in cursor.fetchall()]
            tables = []
            for name in names:
                cursor.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier('public'), sql.Identifier(name)))
                tables.append(dict(table=name, visible_rows=cursor.fetchone()[0]))
            report['public_tables'] = tables
            if 'alembic_version' in names:
                cursor.execute('SELECT version_num FROM public.alembic_version')
                report['database_revisions'] = [r[0] for r in cursor.fetchall()]
            else:
                report['database_revisions'] = []
            cursor.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname")
            report['row_security'] = [dict(table=r[0], enabled=r[1], forced=r[2]) for r in cursor.fetchall()]
            cursor.execute("SELECT extname FROM pg_extension ORDER BY extname")
            report['extensions'] = [r[0] for r in cursor.fetchall()]
        connection.rollback()
    report['redirect_allowlist_verified'] = False
    report['live_google_login_verified'] = False
    return report


def backup_original():
    source = ROOT / 'data/campus.db'
    before = sha(source)
    target = ROOT / '.scratch/campusos-kt' / ('original-sqlite-' + str(time.time_ns()) + '.db')
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(target) as backup:
            original.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SetupError('SQLite backup integrity check failed.')
            records = backup.execute('SELECT count(*) FROM campus_records').fetchone()[0]
            revision = backup.execute('SELECT version_num FROM alembic_version').fetchone()[0]
            logical_original = hashlib.sha256('\n'.join(original.iterdump()).encode()).hexdigest()
            logical_backup = hashlib.sha256('\n'.join(backup.iterdump()).encode()).hexdigest()
            if logical_original != logical_backup:
                raise SetupError('SQLite backup schema/data fingerprint differs; stop and investigate.')
    if sha(source) != before:
        raise SetupError('Original SQLite changed during backup; stop and investigate.')
    return dict(backup=str(target.relative_to(ROOT)), original_sha256=before,
                backup_sha256=sha(target), logical_schema_data_sha256=logical_backup,
                schema_and_data_match=True, records=records, revision=revision, integrity='ok', original_unchanged=True)


def backup_new(values, ref, executable=None):
    """Read-only pg_dump with credentials in the child environment, never argv."""
    dump = Path(executable or shutil.which('pg_dump') or '')
    restore = dump.with_name('pg_restore.exe' if os.name == 'nt' else 'pg_restore') if dump.name else Path('')
    if not dump.is_file() or not restore.is_file():
        raise SetupError('pg_dump and matching pg_restore are required for a verified new-project backup; no remote operation performed.')
    preflight_path = ROOT / 'artifacts/campusos-kt-preflight.json'
    preflight = json.loads(preflight_path.read_text()) if preflight_path.exists() else {}
    if preflight.get('project_ref') != ref or not preflight.get('read_only_verified'):
        raise SetupError('Complete the matching new-project read-only preflight before backup.')
    version = subprocess.run([str(dump), '--version'], capture_output=True, text=True, timeout=10, check=True)
    match = re.search(r'PostgreSQL\) (\d+)', version.stdout)
    if not match or int(match[1]) < int(preflight['server_version'].split('.')[0]):
        raise SetupError('Backup utility is older than the new server; backup not attempted.')
    params = connection_parameters(values['MIGRATION_DATABASE_URL'], ref)
    # Ambient libpq service/hostaddr variables must not redirect to another target.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    env.update({k: str(v) for k, v in dict(
        PGHOST=params['host'], PGPORT=params['port'], PGDATABASE=params['dbname'],
        PGUSER=params['user'], PGPASSWORD=params['password'], PGSSLMODE=params['sslmode'],
        PGCONNECT_TIMEOUT=5, PGAPPNAME='campusos_kt_read_only_backup',
        PGOPTIONS='-c default_transaction_read_only=on -c lock_timeout=3000').items()})
    for name, key in [('PGSSLROOTCERT', 'sslrootcert'), ('PGCHANNELBINDING', 'channel_binding')]:
        if key in params:
            env[name] = params[key]
    directory = ROOT / '.scratch/campusos-kt' / ('postgres-backup-' + str(time.time_ns()))
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / 'campusos-kt.dump'
    subprocess.run([str(dump), '--no-password', '--format=custom',
                    '--file', str(target)], env=env, capture_output=True, timeout=1800, check=True)
    inventory = subprocess.run([str(restore), '--list', str(target)], capture_output=True, timeout=30, check=True)
    if not target.stat().st_size or not inventory.stdout:
        raise SetupError('Archive verification did not produce a readable backup; migration remains blocked.')
    (directory / 'archive-inventory.txt').write_bytes(inventory.stdout)
    return dict(project_ref=ref, backup=str(target.relative_to(ROOT)), backup_sha256=sha(target),
                archive_list_verified=True, restore_rehearsal_completed=False,
                database_writes=False, old_project_accessed=False,
                note='Database archive excludes cluster roles and actual Storage object bytes. Verify restoration before migration approval.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply-local', action='store_true')
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--auth-only', action='store_true')
    mode.add_argument('--backup-original', action='store_true')
    mode.add_argument('--backup-new', action='store_true')
    parser.add_argument('--pg-dump', help='Optional local pg_dump executable path; no credentials in arguments.')
    args = parser.parse_args()
    try:
        if args.backup_original:
            result = backup_original()
        else:
            ignored = subprocess.run(['git', 'check-ignore', '--quiet', str(INPUT)], cwd=ROOT, capture_output=True)
            if ignored.returncode:
                raise SetupError('Credential input must be ignored by Git.')
            values = dotenv_values(INPUT, interpolate=False)
            ref = validate(values, database=args.inspect or args.backup_new)
            if args.auth_only:
                result = inspect_auth(values, ref)
            elif args.inspect:
                result = inspect(values, ref)
            elif args.backup_new:
                result = backup_new(values, ref, args.pg_dump)
            elif args.apply_local:
                result = apply_local(values, ref)
            else:
                result = dict(configuration_valid=True, network_accessed=False, active_files_changed=False, project_ref=ref)
        result['checked_at'] = datetime.now(timezone.utc).isoformat()
        if args.inspect or args.auth_only or args.backup_original or args.backup_new:
            name = ('campusos-kt-auth-preflight.json' if args.auth_only else
                    'campusos-kt-preflight.json' if args.inspect else
                    'campusos-kt-new-backup.json' if args.backup_new else 'campusos-kt-original-backup.json')
            (ROOT / 'artifacts' / name).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))
        return 0
    except SetupError as exc:
        print(str(exc))  # Only our fixed sanitized messages may be printed.
    except Exception as exc:
        print('Preflight stopped: ' + sanitized_failure(exc))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
