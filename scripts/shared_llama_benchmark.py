"""Independent shared-data benchmark preflight. No app imports or database writes."""
import argparse
import ctypes
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

logging.disable(logging.CRITICAL)
ROOT=Path(__file__).resolve().parents[1]
TAGS=("llama3.2:1b","llama3.2:3b","llama3:8b","llama3.1:8b")
BENCHMARK_TIMESTAMP="2026-11-03T10:00:00+05:30"
TRANSACTION_GUARDS_SQL="""SELECT
 pg_catalog.set_config('statement_timeout','5000',true),
 pg_catalog.set_config('lock_timeout','1000',true),
 pg_catalog.set_config('idle_in_transaction_session_timeout','10000',true),
 pg_catalog.set_config('search_path','pg_catalog',true)"""
VERIFY_GUARDS_SQL="""SELECT
 pg_catalog.current_setting('transaction_read_only'),
 pg_catalog.current_setting('statement_timeout'),
 pg_catalog.current_setting('lock_timeout'),
 pg_catalog.current_setting('idle_in_transaction_session_timeout'),
 pg_catalog.current_setting('search_path')"""


def initialize_readonly_guards(connection):
    """Poolers may override startup options; verify local guards before reads.

    The only configuration changes affect this transaction, not database objects,
    grants, roles or persistent settings. All queries remain fixed SELECTs.
    """
    with connection.cursor() as cursor:
        cursor.execute(TRANSACTION_GUARDS_SQL)
        cursor.fetchone()
        cursor.execute(VERIFY_GUARDS_SQL)
        settings=cursor.fetchone()
    if settings != ("on","5s","1s","10s","pg_catalog"):
        raise ValueError("Read-only transaction guards could not be verified")

ROLE_SQL="""
WITH me AS (SELECT * FROM pg_roles WHERE rolname=current_user),
members AS (SELECT * FROM pg_roles WHERE pg_has_role(current_user,oid,'MEMBER') OR pg_has_role(session_user,oid,'MEMBER'))
SELECT me.rolsuper AS superuser,me.rolbypassrls AS bypass_rls,
 me.rolcreaterole AS create_roles,me.rolcreatedb AS create_databases,
 me.rolreplication AS replication,
 current_user<>session_user AS session_role_changed,
 current_user IN ('postgres','supabase_admin','service_role','authenticated','anon') AS shared_builtin_login,
 EXISTS(SELECT 1 FROM members WHERE rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication
   OR rolname IN ('pg_write_all_data','pg_execute_server_program','pg_read_server_files','pg_write_server_files','pg_signal_backend')) AS assumable_privileged_role,
 EXISTS(SELECT 1 FROM pg_database d JOIN members m ON m.oid=d.datdba WHERE d.datname=current_database()) AS database_owner,
 current_setting('transaction_read_only') AS transaction_read_only
FROM me
"""

GRANTS_SQL="""
WITH members AS (SELECT oid,rolname FROM pg_roles WHERE pg_has_role(current_user,oid,'MEMBER') OR pg_has_role(session_user,oid,'MEMBER'))
SELECT
 EXISTS(SELECT 1 FROM members m WHERE has_database_privilege(m.oid,current_database(),'CREATE')) AS database_create,
 EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN members m
   WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
     AND (n.nspowner=m.oid OR has_schema_privilege(m.oid,n.oid,'CREATE'))) AS schema_create_or_owner,
 EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN members m
   WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f')
     AND (c.relowner=m.oid OR has_table_privilege(m.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR has_any_column_privilege(m.oid,c.oid,'INSERT,UPDATE,REFERENCES'))) AS persistent_relation_write_or_owner,
 EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN members m
   WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND p.prosecdef
     AND has_function_privilege(m.oid,p.oid,'EXECUTE')) AS executable_security_definer,
 has_database_privilege(current_user,current_database(),'TEMPORARY') AS temporary_objects_possible
"""


def role_violations(result):
    return [name for name,value in result.items() if name not in
            {"transaction_read_only","temporary_objects_possible"} and value is True]


def connect(*, repeatable_read=False):
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    import psycopg
    values=dotenv_values(ROOT/".env.benchmark",interpolate=False)
    raw=values.get("BENCHMARK_DATABASE_URL")
    if not raw:
        raise ValueError("Missing BENCHMARK_DATABASE_URL")
    url=make_url(raw)
    if url.get_backend_name() not in {"postgresql","postgres"} or not all((url.host,url.username,url.password,url.database)):
        raise ValueError("Invalid PostgreSQL configuration")
    mode=url.query.get("sslmode","require")
    if mode not in {"require","verify-ca","verify-full"}:
        raise ValueError("Encrypted PostgreSQL is required")
    for name in list(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name,None)
    kwargs=dict(host=url.host,port=url.port or 5432,dbname=url.database,user=url.username,password=url.password,
        sslmode=mode,connect_timeout=5,autocommit=False,application_name="campusos-readonly-benchmark-audit",
        options="-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000")
    for name in ("sslrootcert","sslcert","sslkey"):
        if url.query.get(name):
            kwargs[name]=url.query[name]
    connection=psycopg.connect(**kwargs)
    connection.read_only=True
    if repeatable_read:
        connection.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
    try:
        initialize_readonly_guards(connection)
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def inspect_database():
    from psycopg.rows import dict_row
    connection=connect()
    try:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(ROLE_SQL)
            role=dict(cursor.fetchone())
            violations=role_violations(role)
            if violations or role["transaction_read_only"]!="on":
                return dict(passed=False,stage="role",checks=role,violations=violations,
                    schema_inspected=False,application_rows_read=False,
                    reason="Configured credentials are not a verified dedicated read-only account")
            cursor.execute(GRANTS_SQL)
            grants=dict(cursor.fetchone())
            violations=role_violations(grants)
            if violations:
                return dict(passed=False,stage="grants",checks={**role,**grants},violations=violations,
                    schema_inspected=False,application_rows_read=False,
                    reason="Effective ownership/write or security-definer rights prevent strict read-only verification")
            cursor.execute("""SELECT table_schema,table_name,column_name,data_type,udt_name
                FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema')
                ORDER BY table_schema,table_name,ordinal_position LIMIT 1500""")
            columns=[dict(row) for row in cursor.fetchall()]
            cursor.execute("""SELECT n.nspname AS schema,p.proname AS name,
                pg_get_function_identity_arguments(p.oid) AS arguments,
                pg_get_function_result(p.oid) AS result,p.provolatile AS volatility,p.prosecdef AS security_definer
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
                AND has_function_privilege(current_user,p.oid,'EXECUTE') ORDER BY n.nspname,p.proname LIMIT 200""")
            functions=[dict(row) for row in cursor.fetchall()]
            return dict(passed=True,stage="schema",checks={**role,**grants},violations=[],columns=columns,
                functions=functions,schema_inspected=True,application_rows_read=False,
                reason="Dedicated read-only privilege checks passed; identity/scope mapping must precede data retrieval")
    finally:
        connection.rollback()
        connection.close()


def hardware():
    from ctypes import wintypes as w
    class Memory(ctypes.Structure):
        _fields_=[("length",w.DWORD),("load",w.DWORD)]+[(n,ctypes.c_ulonglong) for n in
            ("physical_total","physical_available","pagefile_total","pagefile_available","virtual_total","virtual_available","extended")]
    ram={}
    if os.name=="nt":
        value=Memory()
        value.length=ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            ram=dict(total_gib=round(value.physical_total/2**30,1),available_gib=round(value.physical_available/2**30,1))
    listed=subprocess.run(["ollama","list"],capture_output=True,text=True,timeout=15)
    installed=[line.split()[0] for line in listed.stdout.splitlines()[1:] if line.split() and line.split()[0] in TAGS]
    workspace=ROOT/".scratch/phase7-models/ollama/manifests/registry.ollama.ai/library"
    workspace_installed=[tag for tag in TAGS if (workspace/tag.split(":")[0]/tag.split(":")[1]).is_file()]
    return dict(free_disk_gib=round(shutil.disk_usage(ROOT).free/2**30,1),ram=ram,
        gpu="NVIDIA RTX 4050 Laptop, 6141 MiB VRAM",ollama_list_succeeded=listed.returncode==0,
        installed_requested_models=installed,workspace_installed_models=workspace_installed,
        download_estimates_gb={"llama3.2:1b":1.3,"llama3.2:3b":2.0,"llama3:8b":4.7,"llama3.1:8b":4.9})


def sanitized_error(exc):
    message=str(exc).casefold()
    if getattr(exc,"sqlstate",None)=="28P01" or "password authentication failed" in message:
        return "Authentication rejected"
    if "10013" in message or "forbidden by its access permissions" in message:
        return "Network permission blocked"
    if "ssl" in message or "certificate" in message:
        return "SSL connection failed"
    if "getaddrinfo" in message or "translate host name" in message:
        return "DNS lookup failed"
    if "timeout" in message or "timed out" in message:
        return "Connection or read-only audit timeout"
    return "Read-only preflight failed; details withheld"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight",action="store_true")
    parser.parse_args()
    questions=[line.strip() for line in (ROOT/"benchmark_questions.txt").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert len(questions)==14,"Exactly 14 questions are required"
    try:
        audit=inspect_database()
    except Exception as exc:
        audit=dict(passed=False,reason=sanitized_error(exc),schema_inspected=False,application_rows_read=False)
    result=dict(status="preflight_passed" if audit["passed"] else "blocked_readonly_account",
        executed_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),database_audit=audit,hardware=hardware(),
        database_connection_succeeded=bool(audit.get("checks")),
        phase7_resumed=False,shared_database_modified=False,models_downloaded=False,benchmark_started=False,
        benchmark_timestamp=BENCHMARK_TIMESTAMP,downloads_status="approved_separate_download_report",
        qwen_comparison_requested=False,
        qwen_methodology_available=False,expected_answers_available=False,
        questions=[dict(number=i,question=q,status="not_run",model_responses=[]) for i,q in enumerate(questions,1)],
        models=[dict(model=tag,status="not_run",correct_out_of_14=None,tool_accuracy=None,ttft_seconds=None,
            latency_seconds=None,tokens_per_second=None,cold_load_seconds=None,peak_ram_mib=None,peak_vram_mib=None,
            gpu_cpu_execution=None,hallucination_rate=None,grounding_rate=None,
            results=[dict(question_number=i,question=q,response=None,status="not_run",
                reason="Read-only account and scope/evidence must be verified before benchmarking") for i,q in enumerate(questions,1)],
            reason="Read-only account and scope/evidence must be verified before benchmarking") for tag in TAGS],
        pending_requirements=["Dedicated least-privilege account and verified authorized scope; read-only transactions do not remove BYPASSRLS",
            "Approved test-student identity and tenant/scope; never infer authorization from owner-visible rows",
            "See shared_llama_downloads.json for approved model downloads; fresh memory check before inference",
            "Evidence-supported rubric after authorized mapping; no Qwen comparison requested"],
        recommendations={key:None for key in ("fastest","most_accurate","best_lightweight","best_overall")},
        recommendation_reason="No shared-data model evaluation was run; no winner can be measured")
    test_report=ROOT/"artifacts/shared_llama_preflight_tests.xml"
    if test_report.is_file():
        suites=ElementTree.parse(test_report).getroot().findall(".//testsuite")
        result["offline_tests"]=dict(evidence=test_report.name,
            passed=sum(int(s.get("tests","0"))-int(s.get("failures","0"))-int(s.get("errors","0"))-int(s.get("skipped","0")) for s in suites),
            failed=sum(int(s.get("failures","0"))+int(s.get("errors","0")) for s in suites),
            skipped=sum(int(s.get("skipped","0")) for s in suites))
    (ROOT/"artifacts/llama_benchmark_results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(dict(status=result["status"],database_audit=audit,hardware=result["hardware"])))


if __name__=="__main__":
    main()
