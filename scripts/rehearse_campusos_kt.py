"""Explicitly approved LOCAL-only rehearsal; never loads Supabase credentials."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'pgvector/pgvector:pg17'
LABEL = 'campusos.kt.rehearsal'
REPORT = ROOT / 'artifacts/campusos-kt-local-rehearsal.json'


def run():
    nonce = secrets.token_hex(10)
    name = 'campusos-kt-rehearsal-' + nonce
    work = ROOT / '.scratch' / name
    work.mkdir(parents=True, exist_ok=False)
    original = ROOT / 'data/campus.db'
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    result = dict(status='preparing', cloud_accessed=False, remote_migrations=False,
                  original_before_sha256=before, work=str(work.relative_to(ROOT)))
    cid = None

    def docker(*args, **kwargs):
        r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90, **kwargs)
        if r.returncode:
            raise RuntimeError('Owned rehearsal Docker command failed; details withheld')
        return r.stdout.strip()

    def inventory():
        return dict(containers=sorted(docker('ps','-aq','--no-trunc').splitlines()),
                    volumes=sorted(docker('volume','ls','-q').splitlines()))

    existing = inventory()
    try:
        image = docker('image','inspect',IMAGE,'--format','{{.Id}}')
        bootstrap_password = secrets.token_hex(24)
        # Existing process tests require this name, on our new random loopback port.
        database = 'campus_verify'
        cid = docker('run','--rm','-d','--name',name,'--label',LABEL+'='+nonce,
                     '-p','127.0.0.1::5432','--tmpfs','/var/lib/postgresql/data',
                     '--env','POSTGRES_PASSWORD','--env','POSTGRES_USER=kt_bootstrap',
                     '--env','POSTGRES_DB='+database,image,
                     env={**os.environ,'POSTGRES_PASSWORD':bootstrap_password})
        info = json.loads(docker('inspect',cid))[0]
        ports = info['NetworkSettings']['Ports']['5432/tcp']
        assert cid not in existing['containers'] and info['Config']['Labels'][LABEL] == nonce
        assert not info['HostConfig'].get('Binds') and all(m['Type']=='tmpfs' for m in info['Mounts'])
        assert '/var/lib/postgresql/data' in info['HostConfig'].get('Tmpfs',{})
        assert len(ports)==1 and ports[0]['HostIp']=='127.0.0.1'
        port = int(ports[0]['HostPort'])
        result['isolation'] = dict(verified_before_migrations=True, loopback_port=port,
                                   tmpfs_only=True, no_existing_volumes=True)
        for _ in range(60):
            r = subprocess.run(['docker','exec',cid,'pg_isready','-U','kt_bootstrap','-d',database],
                               capture_output=True, timeout=5)
            if r.returncode==0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Owned rehearsal PostgreSQL did not become ready')
        state = dict(port=port, database=database,
                     bootstrap_password=bootstrap_password,
                     owner_password=secrets.token_hex(24), api_password=secrets.token_hex(24))
        (work/'private-state.json').write_text(json.dumps(state))
        # Read/decompress the complete real archive without executing its SQL.
        backup = json.loads((ROOT/'artifacts/campusos-kt-new-backup.json').read_text())
        archive = (ROOT/backup['backup']).resolve()
        assert archive.is_relative_to((ROOT/'.scratch/campusos-kt').resolve())
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==backup['sha256']
        docker('cp',str(archive),cid+':/tmp/review-backup.dump')
        docker('exec',cid,'pg_restore','--file=/dev/null','/tmp/review-backup.dump')
        result['complete_archive_extraction_verified'] = True
        result['managed_platform_restore_executed'] = False
        env = {**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
        log = work/'local-rehearsal.log'
        with log.open('w',encoding='utf-8') as output:
            child = subprocess.Popen([sys.executable,'-m','scripts.kt_rehearsal_scenario',str(work)],
                                     cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
            while child.poll() is None:
                print('Local rehearsal running; Supabase remains untouched.',flush=True)
                time.sleep(10)
        child_report = work/'scenario.json'
        if child_report.exists():
            result['scenario'] = json.loads(child_report.read_text())
        if child.returncode:
            raise RuntimeError('Local rehearsal failed; sanitized stage and private log retained')
        result['status'] = 'passed'
    except Exception as exc:
        result.update(status='failed',error_class=type(exc).__name__,details_withheld=True)
    finally:
        if cid:
            info = json.loads(docker('inspect',cid))[0]
            if info['Id'] == cid and info['Config']['Labels'].get(LABEL) == nonce:
                docker('rm','-f',cid)
                result['owned_container_removed'] = True
            else:
                result['cleanup_requires_review'] = True
        result['existing_containers_volumes_preserved'] = inventory()==existing
        result['original_after_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        result['original_unchanged'] = result['original_after_sha256']==before
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        if REPORT.exists():
            previous = ROOT/'artifacts'/('campusos-kt-local-rehearsal-previous-'+str(time.time_ns())+'.json')
            previous.write_bytes(REPORT.read_bytes())
        REPORT.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    return 0 if result['status']=='passed' else 2


if __name__=='__main__':
    raise SystemExit(run())
