"""Owned loopback-only PostgreSQL 17 rehearsal; no Supabase connection is loaded."""
import json
import os
import secrets
import subprocess
import sys
import time

import psycopg
from sqlalchemy.engine import URL
from scripts.configure_campusos_kt import ROOT


def main():
    nonce=secrets.token_hex(10)
    name='campusos-import-rehearsal-'+nonce
    password=secrets.token_urlsafe(24)
    def docker(*args,**kwargs):
        r=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=60,**kwargs)
        if r.returncode:
            raise RuntimeError('Owned local Docker operation failed.')
        return r.stdout.strip()
    cid=docker('run','--rm','-d','--name',name,'--label','campusos.import.rehearsal='+nonce,
        '-p','127.0.0.1::5432','--tmpfs','/var/lib/postgresql/data','--env','POSTGRES_PASSWORD',
        '--env','POSTGRES_DB=campus_import_rehearsal','pgvector/pgvector:pg17',env={**os.environ,'POSTGRES_PASSWORD':password})
    try:
        info=json.loads(docker('inspect',cid))[0]
        assert info['Config']['Labels']['campusos.import.rehearsal']==nonce
        port=info['NetworkSettings']['Ports']['5432/tcp'][0]
        assert port['HostIp']=='127.0.0.1' and all(m['Type']=='tmpfs' for m in info['Mounts'])
        for _ in range(40):
            if subprocess.run(['docker','exec',cid,'pg_isready','-U','postgres'],capture_output=True).returncode==0:
                break
            time.sleep(.25)
        url=URL.create('postgresql+psycopg',username='postgres',password=password,host='127.0.0.1',
            port=int(port['HostPort']),database='campus_import_rehearsal').render_as_string(hide_password=False)
        for attempt in range(60):
            try:
                with psycopg.connect(host='127.0.0.1',port=int(port['HostPort']),user='postgres',password=password,
                                     dbname='campus_import_rehearsal',connect_timeout=2):
                    break
            except psycopg.OperationalError:
                if attempt==59:
                    raise
                time.sleep(.25)
        with psycopg.connect(host='127.0.0.1',port=int(port['HostPort']),user='postgres',password=password,dbname='campus_import_rehearsal') as c:
            c.execute('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN')
        env={**os.environ,'DATABASE_URL':url,'TEST_IMPORT_POSTGRES_URL':url}
        result=subprocess.run([sys.executable,'-m','alembic','upgrade','0009'],cwd=ROOT,env=env,capture_output=True,timeout=90)
        if result.returncode:
            (ROOT/'.scratch/import-policy-migration-failure.log').write_bytes(result.stdout+result.stderr)
            raise RuntimeError('Disposable migration rehearsal failed; local details saved.')
        result=subprocess.run([sys.executable,'-m','pytest','backend/tests/test_import_policy_postgres.py','-q',
            '--junitxml=artifacts/import-policy-postgres.xml'],cwd=ROOT,env=env,capture_output=True,timeout=90)
        (ROOT/'.scratch/import-policy-test.log').write_bytes(result.stdout+result.stderr)
        report=dict(passed=result.returncode==0,revision='0009',loopback_only=True,tmpfs_only=True,
                    existing_containers_untouched=True,supabase_accessed=False)
        (ROOT/'artifacts/import-policy-rehearsal.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
        if result.returncode:
            raise RuntimeError('Local policy test failed; inspect local test log.')
    finally:
        owned=json.loads(docker('inspect',cid))[0]
        if owned['Config']['Labels'].get('campusos.import.rehearsal')==nonce:
            docker('rm','-f',cid)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Local rehearsal stopped: '+type(exc).__name__+'; details withheld.')
        raise SystemExit(2)
