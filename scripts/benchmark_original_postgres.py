"""Own one disposable local pgvector container; never accepts an external DB URL."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
IMAGE='pgvector/pgvector:pg16'
LABEL='campusos.original-benchmark'
REPORT=ROOT/'artifacts/original_postgres_benchmark.json'


def original_snapshot():
    path=ROOT/'data/campus.db'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as c:
        return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            records=c.execute('SELECT COUNT(*) FROM campus_records').fetchone()[0],
            revision=c.execute('SELECT version_num FROM alembic_version').fetchone()[0])


def main():
    token=secrets.token_hex(8)
    name='campusos-original-bench-'+token
    work=ROOT/'.scratch'/name
    report=dict(status='preflight',started_at=datetime.now(timezone.utc).isoformat(),
        phase7_resumed=False,original_before=original_snapshot(),work_directory=str(work.relative_to(ROOT)))
    def save():
        REPORT.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def docker(*args,env=None,check=True):
        process=subprocess.run(['docker',*args],cwd=ROOT,env=env,capture_output=True,text=True,
            encoding='utf-8',errors='replace',timeout=90)
        if check and process.returncode:
            raise RuntimeError('Docker command failed; no unrelated resource action attempted')
        return process
    def inventory():
        return dict(containers=docker('ps','-aq','--no-trunc').stdout.split(),
            volumes=docker('volume','ls','-q').stdout.split())
    created=None
    try:
        docker('info','--format','{{.ServerVersion}}')
        image_id=docker('image','inspect',IMAGE,'--format','{{.Id}}').stdout.strip()
        before=inventory()
        work.mkdir(parents=True,exist_ok=False)
        report.update(existing_docker_inventory=before,image_id=image_id)
        password=secrets.token_urlsafe(32)
        api_password=secrets.token_urlsafe(32)
        database='original_bench_'+token
        env=dict(os.environ,POSTGRES_PASSWORD=password)
        created=docker('run','--rm','-d','--name',name,'--label',LABEL+'='+token,
            '--publish','127.0.0.1::5432','--tmpfs','/var/lib/postgresql/data',
            '--env','POSTGRES_PASSWORD','--env','POSTGRES_USER=bench_owner',
            '--env','POSTGRES_DB='+database,IMAGE,env=env).stdout.strip()
        info=json.loads(docker('inspect',created).stdout)[0]
        ports=info['NetworkSettings']['Ports']['5432/tcp']
        report['isolation_probe']=dict(mounts=[dict(type=m['Type'],destination=m['Destination']) for m in info['Mounts']],
            tmpfs_targets=list(info['HostConfig'].get('Tmpfs',{})),ports=ports)
        assert info['Id']==created and created not in before['containers'],'Container identity was not newly allocated'
        assert info['Config']['Labels'][LABEL]==token and info['Image']==image_id,'Ownership label/image mismatch'
        # Docker can report --tmpfs only in HostConfig.Tmpfs, with Mounts empty.
        pgdata_tmpfs='/var/lib/postgresql/data' in info['HostConfig'].get('Tmpfs',{}) or any(
            m['Type']=='tmpfs' and m['Destination']=='/var/lib/postgresql/data' for m in info['Mounts'])
        assert pgdata_tmpfs and all(m['Type']=='tmpfs' for m in info['Mounts']),'Persistent or missing PostgreSQL data mount'
        assert not info['HostConfig'].get('Binds'),'Host bind mounts are not allowed'
        assert len(ports)==1 and ports[0]['HostIp']=='127.0.0.1','Port is not loopback-only'
        port=int(ports[0]['HostPort'])
        assert port!=5432
        report['isolation']=dict(verified_before_migrations=True,container_id=created,name=name,
            loopback_host='127.0.0.1',host_port=port,tmpfs_only=True,no_existing_volumes=True,
            no_existing_containers_modified=True,original_database_not_a_test_target=True)
        private=dict(token=token,container_id=created,database=database,port=port,
            owner_url=f'postgresql+psycopg://bench_owner:{password}@127.0.0.1:{port}/{database}',
            api_url=f'postgresql+psycopg://bench_api:{api_password}@127.0.0.1:{port}/{database}',
            api_password=api_password)
        (work/'private-test-state.json').write_text(json.dumps(private),encoding='utf-8')
        save()
        print('Isolation verified: new labelled container, random loopback port, tmpfs only, original SQLite excluded.',flush=True)
        for _ in range(60):
            if docker('exec',created,'pg_isready','-U','bench_owner','-d',database,check=False).returncode==0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Disposable PostgreSQL startup timed out')
        child_env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        with (work/'scenario.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-m','scripts.original_postgres_scenario',str(work)],
                cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            while process.poll() is None:
                progress=work/'progress.json'
                if progress.exists():
                    try:
                        current=json.loads(progress.read_text())
                        report['progress']=current
                        save()
                        print(json.dumps(current),flush=True)
                    except (OSError,ValueError):
                        pass
                time.sleep(10)
        result=work/'scenario-result.json'
        if result.exists():
            report['benchmark']=json.loads(result.read_text(encoding='utf-8'))
        if process.returncode:
            raise RuntimeError('Isolated scenario failed; sanitized stage is recorded in the result')
        report['status']='completed'
    except Exception as exc:
        report.update(status='failed',error_class=type(exc).__name__,reason=str(exc) if isinstance(exc,(RuntimeError,AssertionError)) else 'Details withheld')
    finally:
        if created:
            try:
                info=json.loads(docker('inspect',created).stdout)[0]
                if info['Id']!=created or info['Config']['Labels'].get(LABEL)!=token:
                    raise RuntimeError('Disposable container ownership mismatch; cleanup stopped')
                docker('rm','--force',created)
                report['temporary_container_removed']=True
            except Exception:
                report['temporary_container_removed']=False
                report['cleanup_requires_review']=True
        report['original_after']=original_snapshot()
        report['original_unchanged']=report['original_before']==report['original_after']
        try:
            report['existing_docker_inventory_preserved']=inventory()==report.get('existing_docker_inventory')
        except Exception:
            report['existing_docker_inventory_preserved']=False
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps(dict(status=report['status'],original_unchanged=report['original_unchanged'],
        temporary_container_removed=report.get('temporary_container_removed'),report=str(REPORT.relative_to(ROOT)))),flush=True)
    return 0 if report['status']=='completed' and report['original_unchanged'] else 1


if __name__=='__main__':
    raise SystemExit(main())
