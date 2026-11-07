"""Run only timetable checks in a new loopback/tmpfs PostgreSQL container."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from sqlalchemy.engine import URL

ROOT=Path(__file__).resolve().parents[1]


def main():
    nonce=secrets.token_hex(8)
    password=secrets.token_urlsafe(24)
    def docker(*args):
        result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=60,
                              env={**os.environ,'POSTGRES_PASSWORD':password})
        if result.returncode:
            raise RuntimeError('Owned timetable Docker operation failed.')
        return result.stdout.strip()
    docker('image','inspect','pgvector/pgvector:pg17')  # Reuse installed image; no downloads.
    cid=docker('run','--rm','-d','--name','campusos-timetable-'+nonce,'--label','campusos.timetable='+nonce,
        '-p','127.0.0.1::5432','--tmpfs','/var/lib/postgresql/data','--env','POSTGRES_PASSWORD',
        '--env','POSTGRES_DB=campus_timetable_rehearsal','pgvector/pgvector:pg17')
    try:
        info=json.loads(docker('inspect',cid))[0]
        assert info['Config']['Labels']['campusos.timetable']==nonce
        port=info['NetworkSettings']['Ports']['5432/tcp'][0]
        assert port['HostIp']=='127.0.0.1' and all(m['Type']=='tmpfs' for m in info['Mounts'])
        params=dict(host='127.0.0.1',port=int(port['HostPort']),user='postgres',password=password,
                    dbname='campus_timetable_rehearsal',connect_timeout=2)
        for attempt in range(60):
            try:
                with psycopg.connect(**params) as c:
                    c.execute('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN')
                break
            except psycopg.OperationalError:
                if attempt==59:
                    raise
                time.sleep(.25)
        url=URL.create('postgresql+psycopg',username='postgres',password=password,host='127.0.0.1',
            port=int(port['HostPort']),database='campus_timetable_rehearsal').render_as_string(hide_password=False)
        env={**os.environ,'DATABASE_URL':url,'TEST_TIMETABLE_POSTGRES_URL':url}
        for name,command in [('migration',[sys.executable,'-m','alembic','upgrade','0009']),
            ('tests',[sys.executable,'-m','pytest','backend/tests/test_timetable_postgres.py','-q','--junitxml=artifacts/timetable-postgres-tests.xml'])]:
            result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
            (ROOT/'.scratch/timetable-phase1'/('postgres-'+name+'.log')).write_text(
                (result.stdout+result.stderr).replace(password,'[REDACTED]'),encoding='utf-8')
            if result.returncode:
                raise RuntimeError('Timetable local '+name+' failed; sanitized details saved.')
        report=dict(passed=True,revision='0009',loopback_only=True,tmpfs_only=True,
                    existing_containers_untouched=True,remote_resources_untouched=True)
        (ROOT/'artifacts/timetable-postgres-rehearsal.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        owned=json.loads(docker('inspect',cid))[0]
        if owned['Config']['Labels'].get('campusos.timetable')==nonce:
            docker('rm','-f',cid)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Timetable rehearsal stopped: '+type(exc).__name__+'. No remote migrations performed.')
        raise SystemExit(2)
