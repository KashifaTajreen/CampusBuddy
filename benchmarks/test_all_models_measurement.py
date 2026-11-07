"""Offline observers must preserve behavior, context and frozen answer facts."""
import asyncio
import threading
import time

import httpx
import pytest
from scripts import original_all_models_scenario as observer


def test_fact_comparison_ignores_generated_ids_but_not_changed_answers():
    first=[dict(id='old',source_id='source-old',subject='Synthetic class',start='09:00')]
    same=[dict(id='new',source_id='source-new',subject='Synthetic class',start='09:00')]
    changed=[dict(id='new',subject='Synthetic class',start='10:00')]
    assert observer.canonical(first)==observer.canonical(same)
    assert observer.canonical(first)!=observer.canonical(changed)


def test_http_observer_keeps_response_and_resets_request_context():
    sent=[]
    async def application(scope,receive,send):
        assert observer.CTX.get() is not None
        observer.CTX.get()['core_ms']=1
        await send(dict(type='http.response.start',status=200,headers=[]))
        await send(dict(type='http.response.body',body=b'unchanged',more_body=False))
    async def send(message):
        sent.append(message)
    asyncio.run(observer.TimedApplication(application)(
        dict(type='http',headers=[(b'x-benchmark-id',b'offline-observer')]),None,send))
    assert sent[-1]['body']==b'unchanged'
    assert observer.CTX.get() is None
    assert observer.OBSERVED.pop('offline-observer')['core_ms']==1


def test_model_observer_records_native_metrics_without_replacing_response(monkeypatch):
    monkeypatch.setattr(observer,'MODEL_PORT',12345)
    response=httpx.Response(200,json=dict(message=dict(content='{"test":true}'),eval_count=10,
        eval_duration=2_000_000_000,total_duration=3_000_000_000))
    def original(*args,**kwargs):
        return response
    request=httpx.Request('POST','http://127.0.0.1:12345/api/chat')
    measure=dict(model_calls=[])
    token=observer.CTX.set(measure)
    try:
        actual=observer.observe_model_send(original)(None,request)
    finally:
        observer.CTX.reset(token)
    assert actual is response
    assert measure['model_calls'][0]['tokens_per_second']==5
    assert measure['model_calls'][0]['ollama_total_ms']==3000


def test_model_timeout_remains_a_timeout_in_original_adapter(monkeypatch):
    monkeypatch.setattr(observer,'MODEL_PORT',12345)
    def original(*args,**kwargs):
        raise httpx.ReadTimeout('Synthetic local timeout')
    measure=dict(model_calls=[])
    token=observer.CTX.set(measure)
    try:
        with pytest.raises(httpx.ReadTimeout):
            observer.observe_model_send(original)(None,httpx.Request('POST','http://127.0.0.1:12345/api/chat'))
    finally:
        observer.CTX.reset(token)
    assert measure['model_calls'][0]['timeout'] is True


def test_terminal_observation_can_arrive_after_client_body_completion():
    def finish_asgi():
        time.sleep(.01)
        observer.OBSERVED['delayed-terminal']=dict(core_ms=3)
    thread=threading.Thread(target=finish_asgi)
    thread.start()
    assert observer.await_observation('delayed-terminal')['core_ms']==3
    thread.join()


def test_missing_observation_stops_comparison_instead_of_scoring_model_wrong():
    with pytest.raises(RuntimeError,match='comparison is invalid'):
        observer.await_observation('absent-terminal',timeout=.005)
