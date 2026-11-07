"""Observe unchanged original QA/fixtures/scoring over real loopback HTTP.

Only model selection and measurement differ between runs. All source facts, roles,
prompts, generation options and admission/scoring predicates remain unchanged.
"""
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from http.server import ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import shutil
import statistics
import sys
import threading
import time
from unittest.mock import patch

import httpx
from scripts import original_postgres_scenario as baseline

ROOT=baseline.ROOT
CTX=ContextVar('original_benchmark_request',default=None)
OBSERVED={}
MODEL_PORT=None
KEY_PATH=ROOT/'artifacts/original_all_models_25_answer_key.json'


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def await_observation(identifier,timeout=2):
    """Synchronize after the client stopwatch stops, never charge observer wait.

    Content-Length can let HTTPX finish before ASGI emits its terminal empty body.
    Missing measurement is an observer error, never an incorrect model answer.
    """
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        observed=OBSERVED.pop(identifier,None)
        if observed is not None:
            return observed
        time.sleep(.001)
    raise RuntimeError('Terminal ASGI observation missing; comparison is invalid')


def canonical(value):
    """Compare prior semantic answer facts while ignoring freshly allocated IDs."""
    if isinstance(value,dict):
        return {k:canonical(v) for k,v in sorted(value.items()) if k not in ('id','source_id','document_id','chunk_id')}
    if isinstance(value,list):
        return sorted((canonical(v) for v in value),key=lambda v:json.dumps(v,sort_keys=True))
    return value


def link_installed(entry,target):
    store=Path(entry['source_store'])
    relative=Path(entry['manifest_relative'])
    manifest=json.loads((store/relative).read_text())
    for blob in [manifest['config'],*manifest['layers']]:
        name=blob['digest'].replace(':','-')
        source=store/'blobs'/name
        destination=target/'blobs'/name
        if not source.is_file() or source.stat().st_size!=blob['size']:
            raise ValueError('Installed model is incomplete; downloads are forbidden')
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():
            try:
                os.link(source,destination)
            except OSError:
                shutil.copyfile(source,destination)
    destination=target/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(store/relative,destination)


def fixture_fingerprint(state):
    import psycopg
    output={}
    with psycopg.connect(state['owner_url'].replace('postgresql+psycopg://','postgresql://',1)) as connection:
        connection.read_only=True
        with connection.cursor() as cursor:
            for table in ('institutions','memberships','sources','campus_records','documents','document_chunks','ingestion_jobs'):
                cursor.execute('SELECT row_to_json(t)::text FROM public.'+table+' t ORDER BY id')
                values=[r[0] for r in cursor.fetchall()]
                output[table]=dict(rows=len(values),sha256=hashlib.sha256('\n'.join(values).encode()).hexdigest())
    return output


class TimedApplication:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        identifier=headers.get(b'x-benchmark-id',b'').decode()
        measurement=dict(core_ms=0.0,model_calls=[])
        token=CTX.set(measurement)
        started=time.perf_counter()
        content_length=None
        payload_bytes=0
        async def observed_send(message):
            nonlocal content_length,payload_bytes
            if message['type']=='http.response.start':
                length=dict(message.get('headers',[])).get(b'content-length')
                content_length=int(length) if length is not None else None
            if message['type']=='http.response.body':
                payload_bytes+=len(message.get('body',b''))
                if ('asgi_payload_ready_ms' not in measurement and
                    (not message.get('more_body',False) or (content_length is not None and payload_bytes>=content_length))):
                    measurement['asgi_payload_ready_ms']=(time.perf_counter()-started)*1000
            if message['type']=='http.response.body' and not message.get('more_body',False):
                measurement['asgi_until_final_body_ms']=(time.perf_counter()-started)*1000
                if identifier:
                    OBSERVED[identifier]=measurement
            await send(message)
        try:
            await self.app(scope,receive,observed_send)
        finally:
            measurement['asgi_complete_ms']=(time.perf_counter()-started)*1000
            CTX.reset(token)


def observe_model_send(guarded_send):
    def observed(client,request,*args,**kwargs):
        measurement=CTX.get()
        if not measurement or request.url.port!=MODEL_PORT or request.url.path!='/api/chat':
            return guarded_send(client,request,*args,**kwargs)
        started=time.perf_counter()
        call={}
        try:
            response=guarded_send(client,request,*args,**kwargs)
            call['http_status']=response.status_code
            try:
                data=response.json()
                content=data.get('message',{}).get('content','')
                call.update(eval_tokens=data.get('eval_count'),
                    ollama_total_ms=data.get('total_duration',0)/1e6,
                    ollama_eval_ms=data.get('eval_duration',0)/1e6,
                    ollama_prompt_ms=data.get('prompt_eval_duration',0)/1e6,
                    raw_content=content[:8000],content_truncated=len(content)>8000)
                if data.get('eval_count') and data.get('eval_duration'):
                    call['tokens_per_second']=data['eval_count']/(data['eval_duration']/1e9)
            except (ValueError,TypeError,AttributeError):
                call['response_json_invalid']=True
            return response
        except httpx.TimeoutException:
            call['timeout']=True
            raise
        except httpx.HTTPError as exc:
            call['transport_error']=type(exc).__name__
            raise
        finally:
            call['http_wall_ms']=(time.perf_counter()-started)*1000
            measurement['model_calls'].append(call)
    return observed


def tool_arguments():
    return {
        'get_academic_calendar':dict(start='2026-11-20',days=1),
        'get_my_timetable':dict(on='2026-11-04'),'get_next_class':{},
        'get_faculty_profile':dict(name='Ananya Rao'),'find_classroom':dict(query='A-300'),
        'search_canteen_items':dict(budget=50,vegetarian=True),'optimize_meal_budget':dict(budget=60,vegetarian=True,count=2),
        'search_documents':dict(query='library borrowing books days'),'get_latest_notices':{},
        'get_examination_schedule':dict(start='2026-11-04',days=30),'list_campus_events':dict(start='2026-11-04',days=1),
        'detect_schedule_conflicts':dict(start='2026-11-04',days=7),'find_free_time_slots':dict(on='2026-11-05'),
        'search_authorized_emails':{},'get_calendar_events':dict(start='2026-11-04',days=1)}


def safety_checks(client,headers,ids,db):
    from backend.app.auth import resolve_actor
    from backend.app.services import CampusService
    from sqlalchemy import text
    checks=[]
    checks.append(dict(check='wrong_tenant_denied',passed=client.get('/v1/me',headers={**headers,'X-Tenant-ID':baseline.OTHER_TENANT}).status_code==403))
    checks.append(dict(check='anonymous_denied',passed=client.get('/v1/me',headers={'X-Tenant-ID':baseline.TENANT}).status_code==401))
    private=ids['Other student private document']['document_id']
    draft=ids['Unpublished benchmark document']['document_id']
    for name,document in (('other_student_document_denied',private),('unpublished_document_denied',draft)):
        response=client.post('/v1/tools/search_documents',headers=headers,json=dict(query='synthetic',document_id=document))
        checks.append(dict(check=name,passed=response.status_code in (403,404)))
    for who,label in ((baseline.STUDENT,'student'),(baseline.ADMIN,'administrator')):
        with db.SessionLocal() as session:
            resolve_actor(session,who,baseline.TENANT)
            count=session.scalar(text('SELECT count(*) FROM documents WHERE id=:d'),{'d':private})
            checks.append(dict(check=label+'_rls_hides_other_private_document',passed=count==0))
    with db.SessionLocal() as session:
        own=CampusService(session,resolve_actor(session,baseline.OTHER,baseline.TENANT),clock=lambda:baseline.NOW)
        checks.append(dict(check='private_owner_can_read_document',passed=own.selected_document(private).id==private))
    return checks


def summarize(record):
    rows=record['questions']
    multi=[r for r in rows if r['assessment'].get('multi_tool')]
    retrieved=[r for r in rows if r['assessment'].get('retrieval_correct') is not None]
    invoked=[r for r in rows if r.get('model_calls')]
    calls=[c for r in rows for c in r.get('model_calls',[])]
    statuses=Counter(r.get('response',{}).get('provider_status') for r in rows)
    fallbacks=[r for r in rows if r.get('response',{}).get('provider_status') in ('invalid_output','failed','unavailable','timeout')]
    def median(values):
        values=[v for v in values if v is not None]
        return statistics.median(values) if values else None
    return dict(correct=sum(r['assessment']['correct'] for r in rows),total=len(rows),
        expected_tool_coverage=sum(r['assessment']['expected_tool_coverage'] for r in rows),
        multi_tool_correct=sum(r['assessment']['correct'] for r in multi),multi_tool_total=len(multi),
        retrieval_correct=sum(r['assessment']['retrieval_correct'] for r in retrieved),retrieval_total=len(retrieved),
        median_end_to_end_ms=median(r['end_to_end_ms'] for r in rows),
        median_core_ms=median(r.get('core_ms') for r in rows),
        median_asgi_response_ms=median(r.get('asgi_payload_ready_ms') for r in rows),
        median_auth_history_framework_ms=median(r.get('outside_router_ms') for r in rows),
        median_transport_client_residual_ms=median(r.get('transport_client_residual_ms') for r in rows),
        median_synthesis_involved_e2e_ms=median(r['end_to_end_ms'] for r in invoked),
        median_model_http_call_ms=median(c['http_wall_ms'] for c in calls),
        median_model_tokens_per_second=median(c.get('tokens_per_second') for c in calls),
        model_timeout_calls=sum(bool(c.get('timeout')) for c in calls),
        model_http_error_calls=sum(c.get('http_status',200)>=400 for c in calls),
        model_invalid_output_questions=statuses.get('invalid_output',0),fallback_questions=len(fallbacks),
        provider_question_statuses=dict(statuses),
        tools_passed=sum(c['success'] for c in record['tool_checks']),tools_total=len(record['tool_checks']),
        safety_passed=sum(c['passed'] for c in record['safety_checks']),safety_total=len(record['safety_checks']),
        unique_conversations=len({r['response'].get('conversation_id') for r in rows if r['http_status']==200}))


def run(work,state):
    global MODEL_PORT
    result=dict(status='starting',models=[],phase7_resumed=False,models_downloaded=False)
    progress_path=work/'progress.json'
    def save():
        (work/'scenario-result.json').write_text(json.dumps(result,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
    def progress(stage,**kwargs):
        result['stage']=stage
        progress_path.write_text(json.dumps(dict(stage=stage,**kwargs)),encoding='utf-8')
        save()
    auth=None
    api_server=None
    try:
        settings=baseline.configure(work,state)
        logging.disable(logging.CRITICAL)
        application_files=list((ROOT/'backend/app').glob('*.py'))
        application_before={p.name:checksum(p) for p in application_files}
        baseline_source=ROOT/'scripts/original_postgres_scenario.py'
        frozen_source=checksum(baseline_source)
        previous=json.loads((ROOT/'artifacts/original_postgres_benchmark.json').read_text())
        assert frozen_source==previous['benchmark']['frozen_scoring_source_sha256'],'Baseline fixture/scoring source differs'
        progress('migrate_seed_existing_unchanged_fixture_functions')
        ids,fixture_info=baseline.fixture(work,state,settings)
        result['fixture']=fixture_info
        gold=baseline.oracle(state,ids)
        prior_key=json.loads((ROOT/'artifacts/original_postgres_25_answer_key.json').read_text())
        assert canonical(gold)==canonical(prior_key['cases']),'Fixture answer facts changed from the baseline'
        KEY_PATH.write_text(json.dumps(dict(anchor=baseline.NOW.isoformat(),cases=gold),indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
        result.update(answer_key_sha256=checksum(KEY_PATH),baseline_fixture_scoring_source_sha256=frozen_source,
            baseline_answer_facts_identical=True,anchor=baseline.NOW.isoformat())
        initial_fixture=fixture_fingerprint(state)
        inventory=json.loads((ROOT/'artifacts/original_all_models_inventory.json').read_text())
        text_models=[m for m in inventory['models'] if m['capability']=='completion']
        result['excluded_embedding_only_models']=[m['model'] for m in inventory['models'] if m['capability']=='embedding_only']
        target=work/'model-store'
        for entry in text_models:
            link_installed(entry,target)
        from scripts import run_shared_small_benchmark as model_server
        model_server.STORE=target
        from backend.app import db,main
        from backend.app.auth import actor,get_session
        from backend.app.services import CampusService
        from backend.app.router import Router
        from fastapi import Depends
        import uvicorn
        from backend.tests import e2e_server as identity
        auth=ThreadingHTTPServer(('127.0.0.1',int(os.environ['CAMPUS_TEST_AUTH_PORT'])),identity.TestIdentity)
        auth_thread=threading.Thread(target=auth.serve_forever,daemon=True)
        auth_thread.start()
        def fixed_service(a=Depends(actor),session=Depends(get_session)):
            return CampusService(session,a,clock=lambda:baseline.NOW)
        main.app.dependency_overrides[main.service]=fixed_service
        original_ask=Router.ask
        def observed_ask(router,*args,**kwargs):
            measure=CTX.get()
            started=time.perf_counter()
            try:
                response=original_ask(router,*args,**kwargs)
                if measure is not None:
                    measure['core_result']=deepcopy(response)
                return response
            finally:
                if measure is not None:
                    measure['core_ms']+=(time.perf_counter()-started)*1000
        with model_server.server() as (llm,owned_pid):
            result['owned_llm_pid']=owned_pid
            MODEL_PORT=llm.base_url.port
            os.environ['OLLAMA_BASE_URL']=str(llm.base_url).rstrip('/')
            settings.cache_clear()
            api_port=baseline.port()
            api_server=uvicorn.Server(uvicorn.Config(TimedApplication(main.app),host='127.0.0.1',port=api_port,
                access_log=False,log_level='critical',timeout_graceful_shutdown=5))
            guarded_send=httpx.Client.send
            with patch.object(Router,'ask',observed_ask),patch.object(httpx.Client,'send',observe_model_send(guarded_send)):
                api_thread=threading.Thread(target=api_server.run,daemon=True)
                api_thread.start()
                for _ in range(150):
                    if api_server.started:
                        break
                    time.sleep(.1)
                assert api_server.started,'Owned HTTP API failed to start'
                result['http_transport']='Real loopback TCP to unchanged FastAPI app via uvicorn, no TestClient'
                with httpx.Client(base_url=f'http://127.0.0.1:{api_port}',timeout=180,trust_env=False) as client:
                    for index,entry in enumerate(text_models):
                        model=entry['model']
                        record=dict(model=model,quantization=entry['quantization'],status='loading',questions=[],tool_checks=[])
                        result['models'].append(record)
                        progress('load_installed_model',model=model)
                        assert fixture_fingerprint(state)==initial_fixture,'Static fixtures changed between models'
                        if index:
                            # Keep the unchanged production rate-limit window from
                            # biasing later models; do not reset buckets or limits.
                            pause=60-time.time()%60+.2
                            record['rate_window_wait_seconds']=pause
                            progress('wait_fresh_rate_limit_window',model=model,seconds=round(pause,1))
                            time.sleep(pause)
                        os.environ['OLLAMA_MODEL']=model
                        settings.cache_clear()
                        metadata=llm.post('/api/show',json=dict(model=model))
                        metadata.raise_for_status()
                        record['runtime_model_metadata']={k:metadata.json().get(k) for k in ('details','capabilities')}
                        began=time.perf_counter()
                        preload=llm.post('/api/generate',json=dict(model=model,prompt='',stream=False,keep_alive='10m',options={'num_ctx':4096}))
                        record['cold_load_ms']=(time.perf_counter()-began)*1000
                        if not preload.is_success:
                            record.update(status='unavailable',reason='Installed model could not preload on this hardware',http_status=preload.status_code)
                            progress('model_preload_failed',model=model)
                            continue
                        record['runtime_allocation']=llm.get('/api/ps').json()
                        headers={'Authorization':'Bearer '+identity.session(baseline.STUDENT)['access_token'],'X-Tenant-ID':baseline.TENANT}
                        health=[]
                        for _ in range(10):
                            began=time.perf_counter()
                            response=client.get('/health')
                            response.raise_for_status()
                            health.append((time.perf_counter()-began)*1000)
                        record['health_http_asgi_baseline_median_ms']=statistics.median(health)
                        for name,arguments in tool_arguments().items():
                            response=client.post('/v1/tools/'+name,headers=headers,json=arguments)
                            body=response.json()
                            record['tool_checks'].append(dict(tool=name,http_status=response.status_code,
                                success=response.status_code==200 and 'data' in body and baseline.OTHER_MARKER not in json.dumps(body)))
                        record['safety_checks']=safety_checks(client,headers,ids,db)
                        for case in gold:
                            progress('run_original_pipeline',model=model,question=case['number'],total=25)
                            identifier=model+'-'+str(case['number'])
                            began=time.perf_counter()
                            response=client.post('/v1/chat',headers={**headers,'X-Benchmark-Id':identifier},json={'question':case['question']})
                            body=response.json()
                            elapsed=(time.perf_counter()-began)*1000
                            observed=await_observation(identifier)
                            core=observed.pop('core_result',{})
                            if response.status_code==200 and not core:
                                raise RuntimeError('Successful response missing Router observation')
                            full={**core,**body}
                            assessment=baseline.assess(case,full) if response.status_code==200 and core else dict(correct=False,
                                expected_tool_coverage=False,multi_tool=case['category']=='multi_tool',failure_category='http_or_permissions')
                            asgi=observed.get('asgi_payload_ready_ms')
                            row=dict(number=case['number'],question=case['question'],http_status=response.status_code,
                                response=body,assessment=assessment,core_route=core.get('route'),core_trace=core.get('trace'),
                                end_to_end_ms=elapsed,**observed)
                            row['outside_router_ms']=asgi-row['core_ms'] if asgi is not None else None
                            row['transport_client_residual_ms']=elapsed-asgi if asgi is not None else None
                            record['questions'].append(row)
                            save()
                        record.update(status='completed',summary=summarize(record),
                            fixtures_unchanged=fixture_fingerprint(state)==initial_fixture)
                        progress('model_complete',model=model,summary=record['summary'])
                        llm.post('/api/generate',json=dict(model=model,prompt='',stream=False,keep_alive=0)).raise_for_status()
                        for _ in range(60):
                            if not llm.get('/api/ps').json().get('models'):
                                break
                            time.sleep(.5)
                api_server.should_exit=True
                api_thread.join(timeout=15)
                assert not api_thread.is_alive(),'Owned API did not shut down'
        result.update(status='completed',same_fixtures_for_all_models=fixture_fingerprint(state)==initial_fixture,
            answer_key_unchanged=checksum(KEY_PATH)==result['answer_key_sha256'],
            fixture_scoring_source_unchanged=checksum(baseline_source)==frozen_source,
            application_source_unchanged={p.name:checksum(p) for p in application_files}==application_before,
            model_downloads_attempted=False,production_accessed=False,owned_services_stopped=True)
    except Exception as exc:
        import traceback
        result.update(status='failed',error_class=type(exc).__name__,details_withheld=True,
            error_frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)[-4:]])
    finally:
        if api_server:
            api_server.should_exit=True
        if auth:
            auth.shutdown()
            auth.server_close()
        save()
    return 0 if result['status']=='completed' else 1


if __name__=='__main__':
    work=Path(sys.argv[1]).resolve()
    if not work.is_relative_to((ROOT/'.scratch').resolve()) or not work.name.startswith('campusos-original-bench-'):
        raise SystemExit('Unowned benchmark work directory')
    state=json.loads((work/'private-test-state.json').read_text())
    raise SystemExit(run(work,state))
