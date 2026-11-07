"""Read-only audit of the original configured SQLite database. No app startup."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/original_campusos_inspection.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect():
    values=dotenv_values(ROOT/'.env',interpolate=False)
    url=make_url(os.environ.get('DATABASE_URL') or values.get('DATABASE_URL') or 'sqlite:///./data/campus.db')
    if url.get_backend_name()!='sqlite':
        raise ValueError('Configured database is not local SQLite; no remote database accessed')
    path=Path(url.database)
    if not path.is_absolute():
        path=ROOT/path
    path=path.resolve(strict=True)
    if not path.is_relative_to(ROOT):
        raise ValueError('Configured SQLite is outside the original project')
    before=digest(path)
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    deadline=time.monotonic()+10
    connection.set_progress_handler(lambda:time.monotonic()>deadline,10000)
    def query(sql,params=()):
        if not sql.lstrip().upper().startswith('SELECT '):
            raise ValueError('Only reviewed SELECT queries are permitted')
        return [dict(row) for row in connection.execute(sql,params).fetchall()]
    try:
        tables=[r['name'] for r in query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        columns={table:[r['name'] for r in connection.execute('PRAGMA table_info("'+table.replace('"','""')+'")')] for table in tables}
        counts={table:query('SELECT COUNT(*) AS count FROM "'+table.replace('"','""')+'"')[0]['count'] for table in tables}
        revision=query('SELECT version_num FROM alembic_version') if 'alembic_version' in tables else []
        from backend.app.models import Base
        missing={name:sorted(set(t.columns.keys())-set(columns.get(name,[]))) for name,t in Base.metadata.tables.items()}
        missing={name:items for name,items in missing.items() if items}
        stats={}
        samples={}
        if 'sources' in tables:
            stats['sources']=query("SELECT kind,demo,publication_status,file_state,(owner_id IS NOT NULL) AS private,COUNT(*) AS count FROM sources GROUP BY kind,demo,publication_status,file_state,(owner_id IS NOT NULL)")
        if 'memberships' in tables:
            stats['memberships']=query("SELECT role,active,COUNT(*) AS count FROM memberships GROUP BY role,active")
            stats['demo_student_scopes']=query("SELECT m.department,m.semester,m.section,COUNT(*) AS count FROM memberships m JOIN institutions i ON i.id=m.tenant_id WHERE m.role='student' AND m.active=1 AND i.demo=1 GROUP BY m.department,m.semester,m.section")
        if 'campus_records' in tables:
            stats['public_demo_records']=query("""SELECT r.kind,COUNT(*) AS count FROM campus_records r
                JOIN sources s ON s.id=r.source_id AND s.tenant_id=r.tenant_id
                WHERE r.owner_id IS NULL AND s.owner_id IS NULL AND s.demo=1
                AND s.publication_status='published' AND s.file_state='active' GROUP BY r.kind""")
            # Only published public Demo Data; never private student/email payloads.
            for kind in ('faculty','menu','timetable','events','calendar','exceptions'):
                fields={
                    'faculty':['name','subjects','office'],
                    'menu':['name','price','vegetarian','protein','available'],
                    'timetable':['department','semester','section','subject','day','start_time','end_time','effective_from','effective_to'],
                    'events':['title','kind','start','end'],
                    'calendar':['title','start_on','end_on','classes_cancelled'],
                    'exceptions':['date','cancelled'],
                }[kind]
                projection=','.join("json_extract(r.payload,'$."+f+"') AS \""+f+'"' for f in fields)
                samples[kind]=query("SELECT "+projection+" FROM campus_records r JOIN sources s ON s.id=r.source_id AND s.tenant_id=r.tenant_id WHERE r.owner_id IS NULL AND s.owner_id IS NULL AND s.demo=1 AND s.publication_status='published' AND s.file_state='active' AND r.kind=? ORDER BY r.id LIMIT 5",(kind,))
        if 'documents' in tables:
            stats['public_demo_documents']=query("""SELECT d.id,d.title,d.published,d.expires,d.topic,d.claim,d.version,d.supersedes_id
                FROM documents d JOIN sources s ON s.id=d.source_id AND s.tenant_id=d.tenant_id
                WHERE d.owner_id IS NULL AND s.owner_id IS NULL AND s.demo=1
                AND s.publication_status='published' AND s.file_state='active' ORDER BY d.title LIMIT 40""")
        if 'document_chunks' in tables:
            stats['public_demo_embeddings']=query("""SELECT c.embedding_model,COUNT(*) AS count,
                MIN(json_array_length(c.embedding)) AS min_dimensions,MAX(json_array_length(c.embedding)) AS max_dimensions
                FROM document_chunks c JOIN documents d ON d.id=c.document_id AND d.tenant_id=c.tenant_id
                JOIN sources s ON s.id=d.source_id AND s.tenant_id=d.tenant_id
                WHERE d.owner_id IS NULL AND s.owner_id IS NULL AND s.demo=1
                AND s.publication_status='published' AND s.file_state='active' GROUP BY c.embedding_model""")
        from backend.app.config import Settings
        config=Settings()
        safe_config={key:getattr(config,key) for key in ('app_env','embedding_provider','embedding_model_path','semantic_min_score',
            'llm_enabled','llm_provider','ollama_model','llm_data_policy','redis_enabled')}
        return dict(status='inspection_complete',executed_at=datetime.now().astimezone().isoformat(),
            database=dict(backend='sqlite',file=str(path.relative_to(ROOT)),mode='ro',query_only=True,revision=revision,
                sha256_before=before,sha256_after=digest(path),tables=counts,columns=columns,missing_current_model_columns=missing),
            config=safe_config,statistics=stats,public_demo_samples=samples,
            shared_benchmark_database_used=False,application_started=False,database_modified=False,benchmark_run=False)
    finally:
        connection.close()


if __name__=='__main__':
    try:
        result=inspect()
        OUT.write_text(json.dumps(result,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
        print(json.dumps(result,indent=2,ensure_ascii=True))
    except Exception as exc:
        print(json.dumps(dict(status='inspection_failed',error_class=type(exc).__name__,details_withheld=True)))
        raise SystemExit(1) from None
