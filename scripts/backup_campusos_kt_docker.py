"""Read-only PostgreSQL 17 backup; no database server, migration or restore runs."""

import json
import os
import re
import secrets
import subprocess
import time
from urllib.parse import quote, quote_plus

from dotenv import dotenv_values
from scripts.configure_campusos_kt import ROOT, INPUT, validate, connection_parameters, sha, sanitized_failure, SetupError


def main():
    values = dotenv_values(INPUT, interpolate=False)
    ref = validate(values, database=True)
    if ref != 'rgqrkgyzxgiwlbuhyohh':
        raise SetupError('Approved new-project reference mismatch.')
    preflight = json.loads((ROOT / 'artifacts/campusos-kt-preflight.json').read_text())
    if preflight.get('project_ref') != ref or not preflight.get('read_only_verified'):
        raise SetupError('Matching successful read-only preflight is required.')
    if preflight['server_version'].split('.')[0] != '17':
        raise SetupError('Reviewed PostgreSQL 17 utility image does not match the inspected server.')
    image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'postgres:17-alpine'],
                           capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    if not image.startswith('sha256:'):
        raise SetupError('Installed official backup image identity could not be determined.')
    params = connection_parameters(values['MIGRATION_DATABASE_URL'], ref)
    if '\n' in params['password'] or '\r' in params['password']:
        raise SetupError('The private stdin password channel cannot accept a line break.')
    work = ROOT / '.scratch/campusos-kt' / ('postgres-backup-' + str(time.time_ns()))
    work.mkdir(parents=True, exist_ok=False)
    nonce = secrets.token_hex(10)
    name = 'campusos-kt-backup-' + nonce
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    env.update({k: str(v) for k, v in dict(
        PGHOST=params['host'], PGPORT=params['port'], PGUSER=params['user'], PGDATABASE=params['dbname'],
        PGSSLMODE=params['sslmode'], PGCONNECT_TIMEOUT=5,
        PGAPPNAME='campusos_kt_readonly_backup',
        PGOPTIONS='-c default_transaction_read_only=on -c lock_timeout=5000').items()})
    if params.get('sslrootcert') or params.get('channel_binding'):
        raise SetupError('Explicit certificate/channel-binding options need reviewed container mounts before backup.')
    common = ['docker', 'run', '--rm', '--read-only', '--cap-drop=ALL',
              '--security-opt=no-new-privileges', '--tmpfs', '/tmp',
              '--tmpfs', '/var/lib/postgresql/data']
    dump = [*common, '-i', '--name', name, '--label', 'campusos.kt.backup=' + nonce,
            '--mount', 'type=bind,src=' + str(work) + ',dst=/backup']
    for key in env:
        if key.startswith('PG'):
            dump.extend(['--env', key])  # No credential values in argv or Docker Config.Env.
    dump += [image, 'sh', '-c',
             'IFS= read -r PGPASSWORD; export PGPASSWORD; exec pg_dump --no-password --format=custom --lock-wait-timeout=5s --file=/backup/archive.partial']
    try:
        # Binary stdin avoids Windows TextIO translating LF to CRLF, which would
        # otherwise become an extra character in the shell's password variable.
        result = subprocess.run(dump, input=(params['password'] + '\n').encode('utf-8'), env=env,
                                capture_output=True, timeout=1800)
        if result.returncode:
            hint = result.stderr.decode('utf-8', errors='replace')
            for value in [params['password'], quote(params['password'], safe=''), quote_plus(params['password']),
                          params['user'], params['host']]:
                hint = hint.replace(value, '[withheld]')
            hint = re.sub(r'postgres(?:ql)?://\S+', '[connection withheld]', hint)
            reason = hint.splitlines()[0][:400] if hint.splitlines() else 'dump failed; details withheld'
            raise SetupError('Read-only backup stopped: ' + reason + '. No migration performed.')
        listing = subprocess.run([*common, '--mount', 'type=bind,src=' + str(work) + ',dst=/backup,readonly',
                                  image, 'pg_restore', '--list', '/backup/archive.partial'],
                                 capture_output=True, timeout=30, check=True)
        if not listing.stdout:
            raise SetupError('Backup archive inventory verification failed.')
        (work / 'archive-inventory.txt').write_bytes(listing.stdout)
        target = work / 'campusos-kt.dump'
        (work / 'archive.partial').rename(target)
        report = dict(project_ref=ref, backup=str(target.relative_to(ROOT)), sha256=sha(target),
                      bytes=target.stat().st_size, archive_list_verified=True,
                      restore_rehearsal_executed=False, read_only_backup=True, database_writes=False,
                      postgres_image_id=image, password_in_argv=False, password_in_docker_config=False,
                      existing_volumes_mounted=False, old_project_accessed=False,
                      excludes='Cluster role definitions/passwords and Storage object bytes are not in pg_dump.')
        (ROOT / 'artifacts/campusos-kt-new-backup.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        # A timed-out Docker client can leave its own utility running. Verify our
        # unguessable label before removing that one container; never enumerate/delete.
        probe = subprocess.run(['docker', 'container', 'inspect', '--format',
                                '{{index .Config.Labels "campusos.kt.backup"}}', name],
                               capture_output=True, text=True, timeout=10)
        if probe.returncode == 0 and probe.stdout.strip() == nonce:
            subprocess.run(['docker', 'container', 'rm', '-f', name], capture_output=True, timeout=15, check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, SetupError) else sanitized_failure(exc))
        raise SystemExit(2)
