"""Render executed original-CampusOS evidence without rescoring or changing gold."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from xml.etree import ElementTree

ROOT=Path(__file__).resolve().parents[1]


def main():
    report=json.loads((ROOT/'artifacts/original_postgres_benchmark.json').read_text())
    benchmark=report['benchmark']
    gold=json.loads((ROOT/'artifacts/original_postgres_25_answer_key.json').read_text())
    rows=benchmark['questions']
    assert [c['question'] for c in gold['cases']]==[r['question'] for r in rows]
    assert hashlib.sha256((ROOT/'artifacts/original_postgres_25_answer_key.json').read_bytes()).hexdigest()==benchmark['frozen_answer_key_sha256']
    assert hashlib.sha256((ROOT/'scripts/original_postgres_scenario.py').read_bytes()).hexdigest()==benchmark['frozen_scoring_source_sha256']
    summary=benchmark['summary']
    statuses=Counter(row['response'].get('provider_status') for row in rows)
    model_rows=[r for r in rows if r['response'].get('provider_status') in ('used','invalid_output','failed','unavailable')]
    explanations={
        4:'An interval request containing 09:00 is routed as point availability at 09:00; no free intervals are returned.',
        5:'Today is ignored by the general events branch; it returns a 14-day window.',
        6:'Tomorrow is ignored by the general events branch; it returns the same 14-day window starting today.',
        8:'Compound splitting does not propagate tomorrow to the first sub-question; the events component is also too broad.',
        14:'The budget parser does not recognize within 30 rupees and defaults to 100.',
        15:'Under 60 is treated inclusively, so the recommendations include a 60-rupee combination.',
        16:'Within 60 is not parsed; recommendations use the default 100-rupee budget.',
        25:'Under 50 includes the exactly 50-rupee item; document retrieval succeeds but the food boundary is wrong.',
    }
    # Additional review is recorded separately; frozen scores/key stay untouched.
    review=dict(frozen_scores_changed=False,model_question_statuses=dict(statuses),
        median_model_involved_latency_ms=statistics.median(r['latency_ms'] for r in model_rows),
        failure_diagnoses=[dict(question=r['number'],category='code',reason=explanations.get(r['number'],'Review response and frozen key'))
            for r in rows if not r['assessment']['correct']],
        scoring_limitations=[dict(question=24,note='The frozen prompt-injection predicate passes: the malicious target was retrieved/flagged and its instruction was not followed. The generated answer instead discusses library borrowing and is off-topic. This safety pass is not full semantic answer correctness. No score was changed.'),
            dict(note='Retrieval metric checks presence of the expected documents, not precision of all returned passages. Some answers include unrelated passages.'),
            dict(note='Tool metric is expected-tool coverage; date/budget argument errors are exposed by answer checks, not hidden in that number.')],
        llm_validation_failures=[r['number'] for r in rows if r['response'].get('provider_status')=='invalid_output'],
        application_source_changed=False,scoring_source_unchanged=benchmark['scoring_source_unchanged'],
        answer_key_unchanged=benchmark['answer_key_unchanged'])
    tree=ElementTree.parse(ROOT/'artifacts/original_postgres_assessor_tests.xml').getroot()
    suites=tree.findall('.//testsuite')
    review['offline_tests']=dict(passed=sum(int(s.get('tests','0'))-int(s.get('failures','0'))-int(s.get('errors','0'))-int(s.get('skipped','0')) for s in suites),
        failed=sum(int(s.get('failures','0'))+int(s.get('errors','0')) for s in suites),skipped=sum(int(s.get('skipped','0')) for s in suites))
    review['e5_index_model_proof']=dict(query_model=benchmark['e5_verification']['model'],
        stored_models=benchmark['fixture']['embedding_models'],
        all_stored_models_match_query=all(model==benchmark['e5_verification']['model'] for model,count in benchmark['fixture']['embedding_models']))
    (ROOT/'artifacts/original_postgres_quality_review.json').write_text(json.dumps(review,indent=2)+'\n',encoding='utf-8')
    lines=[
        '# Original CampusOS: isolated PostgreSQL benchmark',
        '',
        f"**Frozen answer-check score: {summary['correct']}/{summary['total']} (68%).** The original FastAPI pipeline was exercised, not the separate model-only/shared-Supabase harness. Phase 7 remains paused.",
        '',
        '**Quality caveat:** question 24 passed its instruction-resistance predicate but generated an off-topic answer. Its safety pass is not evidence of a fully correct answer. Scores are preserved as frozen; this limitation is reported rather than changing criteria after seeing results.',
        '',
        '## Where the original data comes from',
        '',
        'The original backend resolves `.env`/Settings to `data/campus.db` (SQLite). All 394 `campus_records` rows link to sources marked Demo Data, with `Demo Data ...` seed filenames. Their counts and contents match `backend/app/seed.py`, which validates and persists synthetic rows through the ingestion services. They are stored database records, not frontend hardcoded responses. This provenance identifies the seed mechanism; it does not reconstruct who historically executed the seed command.',
        '',
        '| Category | Storage and generator | Original count | Synthetic status |',
        '| --- | --- | ---: | --- |',
        '| Timetable | SQLite; seed-generated ingestion | 300 | Demo Data |',
        '| Faculty | SQLite; seed-generated ingestion | 24 | Demo Data |',
        '| Canteen | SQLite; seed-generated ingestion | 34 | Demo Data |',
        '| Events, including exams | SQLite; seed-generated ingestion | 18 | Demo Data |',
        '| Private imported emails | SQLite; seed-generated ingestion | 18 | Demo Data; original bodies not inspected |',
        '| Total campus records | SQLite `campus_records` | **394** | All marked Demo Data |',
        '| Documents / chunks | SQLite, separate tables | 13 / 14 | 12 documents marked Demo Data; non-demo content excluded |',
        '',
        '`backend/app/db.py:8` chooses SQLite versus PostgreSQL from DATABASE_URL. `frontend/src/lib/api.ts:25` fetches campus data from FastAPI with Bearer/X-Tenant-ID; `campus-app.tsx:76` uses that API for displayed data. Supabase JS is used for authentication, not direct campus table queries. Read-only frontend environment inspection found the local API target on port 8000 and matching frontend/backend Auth project configuration. No credentials are shown and no Supabase service was contacted. The intended data sources match; the original website was not migrated or browser-tested here and still retains its original schema/configuration.',
        '',
        '## Isolation and preparation verified',
        '',
        '- Used the existing `pgvector/pgvector:pg16` image from the project Docker setup. Created one new uniquely labelled container, random loopback-only port and tmpfs PGDATA; no existing volumes, bind mounts or production connection were used. Isolation was checked before migrations/seeding.',
        '- Migrated only that fresh database through **0007**. The original SQLite stayed at **0005**, with 394 records and identical SHA-256 before/after: `'+report['original_after']['sha256']+'`.',
        '- Created synthetic fixtures with the existing `seed()` and ingestion/publication services: author/reviewer, two students, academic calendar, private records, unpublished document and malicious-source notice. No original or Supabase data was copied.',
        '- Fixture counts: 397 campus records, 23 sources, 15 documents/chunks and 4 memberships. All 15 chunk vectors use the installed E5-small model identity, matching live PostgreSQL query embeddings.',
        '- API connection used a generated restricted login inheriting `campus_api`, with no superuser, ownership or BYPASSRLS privileges. Fixture setup/migrations used a separate disposable owner login.',
        '- Only the child test process received benchmark environment configuration. Operator `.env` loading was disabled there, production credential fields blanked and non-local HTTP/JWT requests blocked. Source storage and model hardlinks stayed in its ignored workspace directory.',
        '- Local Auth used the existing RSA/JWKS test handler, exercising normal JWT verification and memberships without Supabase. Llama 3.2 3B used the existing Ollama adapter on an owned loopback service. No model/image download was needed.',
        '- Temporary PostgreSQL container removed after the run; existing Docker container/volume inventory preserved. The owned model and Auth services were closed. No application source or operator configuration was changed.',
        '',
        '## Actual pipeline and fixed conditions',
        '',
        'Business time was fixed at **2026-11-04 10:00 Asia/Kolkata**. The exact 25 candidate question texts were retained. A separate SQL/arithmetic oracle read the seeded fixture facts and source documents before the first request; its answer key and scoring source hashes were frozen and checked unchanged afterward. No expected answer came from the model or Router output.',
        '',
        'Each question created a fresh conversation and request database session. All 25 responses had different conversation IDs. Requests exercised the original `/v1/chat` ASGI endpoint, real JWT/membership authorization, restricted-role PostgreSQL/RLS, unchanged deterministic Router/ToolKit/CampusService, pgvector/E5 retrieval, optional Ollama synthesis, citations, fallback and history/trace persistence. Clock injection and route observation were test-process-only; they did not replace routing or feed expected answers to the app.',
        '',
        'The 15 tools are listed in [the original inspection report](ORIGINAL_CAMPUSOS_BENCHMARK.md). All were additionally called through their real authenticated HTTP tool endpoints. Optional ambiguous-intent LLM planning was not triggered by these 25 clear questions; model synthesis was exercised. This is a backend HTTP/ASGI benchmark, not a browser/network round-trip, deployed worker or live Supabase verification.',
        '',
        '| Metric | Executed result |',
        '| --- | ---: |',
        f"| Frozen answer checks | {summary['correct']}/25 (68%) |",
        f"| Expected-tool coverage | {summary['expected_tool_coverage']}/25 (96%) |",
        f"| Multi-tool answer success | {summary['multi_tool_correct']}/{summary['multi_tool_total']} (60%) |",
        f"| Expected document present in designated retrieval cases | {summary['retrieval_correct']}/{summary['retrieval_total']} |",
        f"| Tool endpoint smoke checks | {summary['tools_passed']}/{summary['tools_total']} |",
        f"| Tenant/owner/publication/RLS safety checks | {summary['safety_passed']}/{summary['safety_total']} |",
        f"| Median per-question HTTP/ASGI latency | {summary['median_latency_ms']:.2f} ms |",
        f"| Median latency for synthesis-involved questions | {review['median_model_involved_latency_ms']:.2f} ms |",
        f"| Separate cold Llama load | {benchmark['cold_load_seconds']:.2f} seconds |",
        '',
        'Latency is steady-state application time with a warm database/model after preparation/tool checks. It includes each chat request and persistence, but excludes container startup, seeding, E5 indexing and cold model loading. Llama reported 2,561,000,078 bytes allocated in VRAM with context 4096; this is runtime allocation, not a sampled peak. No browser latency or concurrency/load claim is made.',
        '',
        '## Failures and integration limits',
        '',
        '| Question(s) | Category | Observed cause |',
        '| --- | --- | --- |',
        '| 4 | Code/routing | Interval question misrouted to availability at its first mentioned time |',
        '| 5, 6 | Code/date parsing | Today/tomorrow events returned a 14-day window |',
        '| 8 | Code/orchestration | Tomorrow not propagated to the first compound part; event window also too broad |',
        '| 14, 16 | Code/budget parsing | Within not recognized; budget defaulted to 100 |',
        '| 15, 25 | Code/budget boundary | Strict under treated as inclusive, allowing exactly 60 or 50 |',
        '| 18, 22 | Model/validation warning | Grounded output rejected; correct extractive fallback preserved system answer |',
        '| 24 | Model relevance / scoring limitation | Injection instruction resisted, but generated answer discussed borrowing instead of the requested notice |',
        '',
        'No final answer failure was caused by missing fixtures or a demonstrated permission leak. Four questions invoked synthesis: two reported `used`, two `invalid_output`; the latter fell back to source text. These are system results, not Llama-only accuracy. Expected-document presence is not precision: some retrieval answers included unrelated passages. Multilingual cases retained original-language evidence and do not prove full translation quality.',
        '',
        'Two preparation attempts are preserved: [attempt 1](../artifacts/original_postgres_benchmark_attempt1.json) stopped before migrations because Docker represents tmpfs in HostConfig rather than Mounts; [attempt 2](../artifacts/original_postgres_benchmark_attempt2.json) passed all tools/safety checks but stopped after three questions on an assessor missing-field crash. The assessor fix records the unexpected shape as incorrect instead of crashing; no question, answer predicate or application behavior was relaxed to increase results. Every attempt cleaned up only its own container and preserved the original database.',
        '',
        'Offline assessor regressions: **5 passed, 0 failed, 0 skipped**; Ruff passed for the new benchmark scripts/tests. The 25-question benchmark itself has **17 passes and 8 failures**; it must not be described as an all-passing test suite. Application code fixes remain outside this request.',
        '',
        '## Per-question evidence',
        '',
        '| # | Question | Check | Latency ms | Actual tools |',
        '| --- | --- | --- | ---: | --- |',
    ]
    for row in rows:
        tools=[t['tool'] for t in row.get('core_trace') or []]
        lines.append('| '+str(row['number'])+' | '+row['question']+' | '+('PASS' if row['assessment']['correct'] else 'FAIL')+' | '+str(row['latency_ms'])+' | '+', '.join(tools)+' |')
    lines.extend([
        '',
        'Full questions, HTTP responses, records, routes, traces, statuses, per-question timings, setup and cleanup evidence: [executed JSON](../artifacts/original_postgres_benchmark.json). Independent fixture-derived references: [25-answer key](../artifacts/original_postgres_25_answer_key.json). Additional review without rescoring: [quality review](../artifacts/original_postgres_quality_review.json).',
        '',
        '## Reproduction',
        '',
        'From the existing repository root, with Docker running and the already installed pgvector image, E5 and Llama 3B weights available:',
        '',
        '```powershell',
        '.\\.venv\\Scripts\\python.exe -m scripts.benchmark_original_postgres',
        '.\\.venv\\Scripts\\python.exe -m scripts.report_original_postgres',
        '.\\.venv\\Scripts\\python.exe -m pytest benchmarks/test_original_postgres_assessor.py -q',
        '.\\.venv\\Scripts\\python.exe -m ruff check scripts/benchmark_original_postgres.py scripts/original_postgres_scenario.py scripts/report_original_postgres.py benchmarks/test_original_postgres_assessor.py',
        '```',
        '',
        'The setup never accepts an external database URL and always generates a fresh container/database. Do not replace the command with ordinary Compose up against the existing named volumes. Generated test-only credentials remain in ignored workspace state and are never printed. Reproduction replaces the latest generated result/key; preserve those artifacts first if comparing runs.',
        '',
        'No further credentials or approval were needed for this authorized isolated test. Running migrations or fixing behavior in the original application, deploying, resuming Phase 7, live Supabase acceptance and a browser test remain separate work; none was performed.',
    ])
    (ROOT/'docs/ORIGINAL_POSTGRES_BENCHMARK.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(report='docs/ORIGINAL_POSTGRES_BENCHMARK.md',summary=summary,quality_review_written=True,
        scoring_unchanged=benchmark['scoring_source_unchanged'],answer_key_unchanged=benchmark['answer_key_unchanged'])))


if __name__=='__main__':
    main()
