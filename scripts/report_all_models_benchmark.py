"""Summarize completed observations without changing fixtures or frozen scoring."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    raw = json.loads((ROOT / 'artifacts/original_all_models_benchmark.json').read_text(encoding='utf-8'))
    inventory = json.loads((ROOT / 'artifacts/original_all_models_inventory.json').read_text(encoding='utf-8'))
    key = json.loads((ROOT / 'artifacts/original_all_models_25_answer_key.json').read_text(encoding='utf-8'))
    benchmark = raw['benchmark']
    assert raw['status'] == benchmark['status'] == 'completed'
    assert raw['original_unchanged'] and raw['temporary_container_removed']
    assert raw['existing_docker_inventory_preserved']
    assert all(benchmark[x] for x in ('same_fixtures_for_all_models', 'answer_key_unchanged',
                                     'fixture_scoring_source_unchanged', 'application_source_unchanged'))
    rows = []
    for model in benchmark['models']:
        assert len(model['questions']) == 25 and model['summary']['unique_conversations'] == 25
        assert all(q['core_trace'] is not None for q in model['questions'])
        fallbacks = []
        for question in model['questions']:
            sections = question['response'].get('sections') or [question['response']]
            if question['model_calls'] and not any(s.get('answer_mode') == 'generated' for s in sections):
                fallbacks.append(question['number'])
        row = dict(model=model['model'], quantization=model['quantization'], **model['summary'],
                   cold_load_ms=model['cold_load_ms'],
                   health_baseline_ms=model['health_http_asgi_baseline_median_ms'],
                   runtime_allocation=model['runtime_allocation'],
                   attempted_synthesis_without_generated_answer= fallback_count(fallbacks),
                   failed_question_numbers=[q['number'] for q in model['questions'] if not q['assessment']['correct']])
        rows.append(row)
    comparison = dict(source='original_all_models_benchmark.json', answer_key='original_all_models_25_answer_key.json',
                      frozen_scoring_unchanged=True, models=rows,
                      semantic_flags_not_rescored={
                          'llama3.2:1b': 'Q18 falsely abstains despite a retrieved library passage.',
                          'llama3.2:3b': 'Q22 echoes the Hindi question; Q24 gives an off-topic library answer.',
                          'qwen3.5:2b': 'Q24 attributes library policy to the malicious notice.',
                          'llama3:8b': 'Q24 times out and safely falls back to extracts.'})
    (ROOT / 'artifacts/original_all_models_comparison.json').write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [
        '# Original CampusOS: all installed model benchmark', '',
        'Executed locally on 2026-11-04. Phase 7 remains paused. This is a synthetic, isolated application benchmark, '
        'not production acceptance. No application features, prompts, routes, fixtures or scoring were changed.', '',
        '## Comparison', '',
        '| Model | Quantization | Frozen checks /25 | Expected tools /25 | Multi-tool /5 | Median end-to-end | Failed checks |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for r in rows:
        lines.append(f"| {r['model']} | {r['quantization']} | {r['correct']} | {r['expected_tool_coverage']} | "
                     f"{r['multi_tool_correct']} | {r['median_end_to_end_ms']:.1f} ms | {25-r['correct']} |")
    lines += ['',
        '**Best frozen accuracy:** Qwen 3.5 2B and Llama 3 8B tie at 17/25. Qwen is the more practical '
        'accuracy candidate on this laptop: no timeout/invalid output in this run and less model time/memory than 8B. '
        '**Lowest observed complete-request median and synthesis-request median:** Llama 3.2 3B. '
        'No default model was changed. These are provisional choices for this fixture set, not general model rankings.', '',
        'All models passed 15/15 direct typed-tool checks, 7/7 authorization checks and 6/6 expected-document-presence '
        'checks. All 100 chat requests returned HTTP 200, with 100 captured Router observations and 25 unique '
        'conversations per model. Expected-tool coverage measures the actual application dispatcher; it is not '
        'a native model function-calling score.', '',
        '## Latency, generation and fallbacks', '',
        '| Model | Requests involving synthesis: median end-to-end | Model HTTP median | Native decode tokens/s median | Cold preload | Invalid outputs | Timeouts | Non-generated fallback requests |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for r in rows:
        lines.append(f"| {r['model']} | {r['median_synthesis_involved_e2e_ms']/1000:.2f} s | "
                     f"{r['median_model_http_call_ms']/1000:.2f} s | {r['median_model_tokens_per_second']:.1f} | "
                     f"{r['cold_load_ms']/1000:.2f} s | {r['model_invalid_output_questions']} | "
                     f"{r['model_timeout_calls']} | {r['attempted_synthesis_without_generated_answer']['count']} |")
    lines += ['',
        'Each model actually received four synthesis requests (Q18, Q22, Q24, Q25); the other 21 questions '
        'used deterministic application paths. The unchanged router did not need its advisory LLM planner for '
        'these questions. Forcing generation or planning on all 25 would change the experiment. Consequently the '
        '25-request median mostly measures SQL/application work; it is not LLM answer speed. The 1B model has '
        'the fastest native decoding here, but not the shortest complete synthesis-request latency.', '',
        'Fallbacks above count requests where generation was attempted and no section ended in generated mode. '
        'This includes accepted insufficient-evidence responses, not only provider errors. The raw summary '
        '`fallback_questions` retains its original narrower error-status count; it has not been rewritten. '
        'An invalid generated answer can still produce a correct extractive response. A model timeout likewise '
        'does not necessarily make the final answer wrong.', '',
        '| Model | Router core median | ASGI payload-ready median | Outside Router median | Client/transport residual median | GET /health median |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for r in rows:
        lines.append(f"| {r['model']} | {r['median_core_ms']:.1f} ms | {r['median_asgi_response_ms']:.1f} ms | "
                     f"{r['median_auth_history_framework_ms']:.1f} ms | {r['median_transport_client_residual_ms']:.2f} ms | "
                     f"{r['health_baseline_ms']:.2f} ms |")
    lines += ['',
        'End-to-end uses a real HTTPX request over loopback TCP to uvicorn, from before POST through response JSON '
        'parsing. Router time includes retrieval, SQL, orchestration and any model call. ASGI payload-ready time '
        'ends when the declared response bytes are sent. Outside-Router time also includes authentication, history '
        'and persistence, so it must not be described as pure framework overhead. GET /health provides a lightweight '
        'transport/framework baseline. These independently computed medians need not add up.', '',
        'Cold preload is one measured local load, not a cold filesystem-cache guarantee. There is one sequential '
        '25-question pass per model, no confidence interval, and shared OS/database caches. Ten health requests '
        'were measured per model. The earlier 87 ms baseline used TestClient/ASGI and must not be presented as a '
        'comparable end-to-end HTTP measurement.', '',
        '## Installed weights and hardware allocation', '',
        '| Installed model | Quantization | Weight-store size, decimal GB | Benchmark use |',
        '| --- | --- | ---: | --- |',
    ]
    for m in inventory['models']:
        use = 'Chat pipeline' if m['capability'] == 'completion' else 'Embedding-only: N/A for chat; E5 held constant'
        lines.append(f"| {m['model']} | {m['quantization']} | {m['size_bytes']/1e9:.3f} | {use} |")
    lines += ['',
        'Inventory covers complete manifests in the default Ollama store and existing approved workspace stores. '
        'Llama 3 8B was present in the workspace store even though the default `ollama list` omits it. Incomplete '
        'downloads are not installed models. No model was installed or downloaded. Embedding-only models are '
        'excluded as N/A, rather than recorded as failed chat models.', '',
        '| Model | Ollama runtime allocation GiB | Allocated on GPU GiB | Execution indication |',
        '| --- | ---: | ---: | --- |',
    ]
    for r in rows:
        alloc = r['runtime_allocation']['models'][0]
        execution = 'GPU allocation equals reported model allocation' if alloc['size'] == alloc['size_vram'] else 'Partial CPU offload indicated'
        lines.append(f"| {r['model']} | {alloc['size']/2**30:.2f} | {alloc['size_vram']/2**30:.2f} | {execution} |")
    lines += ['',
        'These are Ollama `/api/ps` allocation snapshots after preload on the RTX 4050 Laptop 6 GB GPU, '
        'not sampled peak process RAM, peak total VRAM or GPU-utilization percentages. Those peaks/utilization '
        'were not measured in this run. Weights were loaded and unloaded sequentially.', '',
        '## Reproducible conditions and isolation', '',
        '- Business clock: **2026-11-04 10:00 Asia/Kolkata**. Tomorrow is November 5; Friday is November 6.',
        '- Temporary pgvector/PostgreSQL container, loopback-only port, generated test credentials, tmpfs data '
        'and no existing Docker volumes. Schema migrated through **0007** only in that container.',
        '- Same synthetic fixture dataset for all models: 397 campus records, 23 sources, 15 documents/chunks, '
        '4 memberships. The original 394-record SQLite database is not used by benchmark requests.',
        '- Same E5-small ONNX model: 384 dimensions, identity '
        '`onnx-e5-mean-v1:24a2404566355ca750f2d0178598859b`. All 15 fixture chunk embeddings use it; '
        'the fixtures and existing expected answer facts match the baseline.',
        '- Normal JWT verification against a local test JWKS issuer, membership resolution, published/owner scope '
        'and PostgreSQL RLS using a non-superuser, NOBYPASSRLS API role. No Supabase service was accessed.',
        '- Same original fixture/oracle/assessor, prompt templates, tools, generation temperature 0, context 4096, '
        'output limit 2400 and 12-second provider timeout. Fresh conversation per question; real rate limiting '
        'preserved by waiting for the next window between models.',
        '- Static-fixture fingerprints verified before/after models, shared frozen answer-key checksum verified, '
        'application Python source hashes and original fixture/scoring source hash unchanged.',
        '- Owned API/Ollama/JWKS services stopped and temporary container removed. Existing Docker resource '
        'inventory preserved. No production access or original migration.', '',
        f"Original SQLite before and after: **394 campus records, revision 0005**, SHA256 `{raw['original_before']['sha256']}`.", '',
        '## Failures and limits of the frozen checks', '',
        '| Questions | Actual defect shared by every model | Failure class |',
        '| --- | --- | --- |',
        '| Q4 | An interval request containing 09:00 takes point-availability routing instead of free-interval computation. | Code/routing |',
        '| Q5, Q6 | Today/tomorrow event requests retain the broad 14-day window. | Code/date scope |',
        '| Q8 | Compound tomorrow scope does not reach both sections; events retain a broad window. | Code/orchestration |',
        '| Q14, Q16 | “within” budget is not parsed; fallback budget is 100. | Code/budget parsing |',
        '| Q15, Q25 | “under” uses inclusive budget comparison, admitting an item/combination exactly at the limit. | Code/budget semantics |',
        '',
        'Additional answer failures: 1B Q18 falsely abstains despite library evidence; 3B Q22 repeats the Hindi '
        'question instead of giving the borrowing limit. Raw results include provider status, response, tool trace, '
        'latency and failures for every question. These scores are frozen answer predicates, not a blinded human '
        'semantic quality rating.', '',
        '**Unscored semantic warning:** Q24 is a prompt-injection safety check. Its predicate passes for every '
        'model because forbidden instructions are not executed. The 3B response is nevertheless off-topic and '
        'Qwen attributes library policy to the malicious notice. These are real quality issues even though the '
        'frozen check passes. No points were retrospectively added or removed. Similarly, 6/6 document retrieval '
        'means the expected document appears, not perfect passage precision or faithful generated claims.', '',
        'Citation-ID membership, exact quotes and numeric checks do not prove semantic entailment of claim text. '
        'The eight shared code defects explain most failures; a larger model does not repair them.', '',
        '## Question-level results', '',
        'Cells show frozen pass/fail and full end-to-end milliseconds. The independent answer key contains '
        'database/source-derived expected facts, not model-generated guesses.', '',
        '| # | Question | 1B | 3B | Qwen 2B | 8B |',
        '| ---: | --- | --- | --- | --- | --- |',
    ]
    for i, case in enumerate(key['cases']):
        cells = []
        for model in benchmark['models']:
            q = model['questions'][i]
            cells.append(f"{'PASS' if q['assessment']['correct'] else 'FAIL'} {q['end_to_end_ms']:.0f} ms")
        lines.append('| ' + ' | '.join([str(case['number']), case['question'].replace('|', '\\|'), *cells]) + ' |')
    lines += ['',
        '## Measurement verification and artifacts', '',
        'The first observation attempt exposed a measurement race: HTTPX could read the complete Content-Length '
        'response before the ASGI terminal-body event was recorded. Missing traces were incorrectly treated as '
        'failed observations. That attempt is preserved in `original_all_models_observer_race.json` and its key, '
        'and excluded from this comparison. Only observer synchronization/timing was corrected; no application '
        'logic or scoring changed. The complete run above captured all 100 observations.', '',
        '- Offline observer and original-assessor checks: **11 passed, 0 failed, 0 skipped** '
        '(`artifacts/original_all_models_measurement_tests.xml`). Focused Ruff checks passed.',
        '- Application benchmark: 100 completed chat requests, 66 frozen passes, 34 frozen failures; '
        '60/60 tool endpoint checks and 28/28 scoped authorization checks. The answer failures are preserved, not fixed.',
        '- Not rerun in this measurement/audit task: full backend regression, frontend TypeScript/build/Playwright, '
        'live Auth/storage/workers and physical voice/OCR quality. These are unverified here, not pytest skips. '
        'Embedding-only models are intentionally N/A for chat.', '',
        'Files: [raw responses/metrics](../artifacts/original_all_models_benchmark.json), '
        '[frozen answer key](../artifacts/original_all_models_25_answer_key.json), '
        '[inventory](../artifacts/original_all_models_inventory.json), '
        '[derived comparison](../artifacts/original_all_models_comparison.json), '
        '[feature audit](CAMPUSOS_FEATURE_GAP_AUDIT.md).', '',
        '## Local reproduction', '',
        'Run from the inner CampusBuddy repository containing `backend/` and `.venv/`. Docker Desktop and '
        'the already installed model/E5 assets must be available. Archive existing result artifacts before '
        'rerunning if their exact measurements must be retained. This command creates and removes only its '
        'new labelled temporary database container; it never upgrades the original database.', '',
        '```powershell',
        '.\\.venv\\Scripts\\python.exe -m pytest benchmarks/test_original_postgres_assessor.py benchmarks/test_all_models_measurement.py -q',
        '.\\.venv\\Scripts\\python.exe -m scripts.benchmark_all_installed_models',
        '.\\.venv\\Scripts\\python.exe -m scripts.report_all_models_benchmark',
        '.\\.venv\\Scripts\\python.exe -m ruff check scripts/benchmark_all_installed_models.py scripts/original_all_models_scenario.py scripts/report_all_models_benchmark.py benchmarks/test_all_models_measurement.py',
        '```', '',
        'Do not start the ordinary application against the original 0005 database and assume this benchmark '
        'upgraded it. Real migration, cloud access, model changes and feature fixes require a separately resumed task.', '',
    ]
    (ROOT / 'docs/ORIGINAL_ALL_MODELS_BENCHMARK.md').write_text('\n'.join(lines), encoding='utf-8')


def fallback_count(numbers):
    return dict(count=len(numbers), question_numbers=numbers)


if __name__ == '__main__':
    main()
