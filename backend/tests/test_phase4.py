"""Campus routing, multilingual and generated evidence regressions."""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from fastapi import HTTPException

from backend.app.ai import GeminiProvider, RoutePlan, RouteStep, validate_grounded
from backend.app.router import Router
from backend.app.language import normalize
from backend.tests.test_ai_providers import config, HIT, OUTPUT
from backend.tests.test_selected_documents import pdf_document, actor_service


def test_multi_intent_and_section_faculty_context(env):
    router = Router(env.service)
    first = router.ask("Who teaches Machine Learning to my section and where is their office?")
    assert first["kind"] == "multi" and len(first["sections"]) == 2
    assert first["sections"][0]["records"]
    assert first["sections"][0]["records"][0]["code"] == first["sections"][1]["records"][0]["code"]
    followup = router.ask("When is their next class?", first["context"])
    assert followup["records"][0]["faculty_code"] == first["context"]["faculty_code"]
    assert len(first["trace"]) <= 8


def test_named_miss_and_unresolved_followup_do_not_broaden(env):
    first = Router(env.service).ask("Professor Nobody Everknown")
    assert first["records"] == []
    assert Router(env.service).ask("When is their next class?")["records"] == []
    assert env.service.faculty(subject="Nonexistent subject") == []


def test_current_class_uses_campus_timezone(env):
    env.service.clock = lambda: datetime(2026, 10, 28, 6, 30, tzinfo=timezone.utc)
    result = Router(env.service).ask("My current class")
    assert result["records"] == env.service.timetable("2026-10-28", at="12:00")
    assert env.service.local_now().hour == 12


@pytest.mark.parametrize("question,language,kind", [
    ("ಇಂದು ನನ್ನ ತರಗತಿಗಳು", "kn", "classes"), ("आज मेरी classes", "hi", "classes"),
    ("naale nanna class", "kn", "classes"), ("aaj meri class", "hi", "classes"),
    ("ಸಸ್ಯಾಹಾರಿ food under 100", "kn", "food"), ("शाकाहारी खाना under १००", "hi", "food"),
    ("ನನ್ನ section OS ಯಾರು ಬೋಧಿಸುತ್ತಾರೆ", "kn", "faculty"),
])
def test_multilingual_routes(env, question, language, kind):
    result = Router(env.service).ask(question)
    assert result["kind"] == kind and result["language"] == language
    assert result["provider_status"] == "disabled"


def test_language_preference_and_proper_names(env):
    first = Router(env.service).ask("Who teaches Machine Learning?", language="hi")
    second = Router(env.service).ask("Where is their office?", first["context"])
    assert second["language"] == "hi" and second["records"][0]["name"] == first["records"][0]["name"]
    assert normalize("OS आज", "en")[1] == "en"


def test_ambiguous_plan_uses_only_typed_tools(env, monkeypatch):
    provider = GeminiProvider(config())
    monkeypatch.setattr(provider, "plan", lambda *_: RoutePlan(steps=[RouteStep(intent="menu", query="under 80")]))
    result = Router(env.service, provider).ask("Help me choose something affordable on campus")
    assert result["kind"] == "food" and result["trace"][0]["tool"] == "search_canteen_items"
    assert all(r["price"] <= 80 for r in result["records"])


def test_oversized_request_fails_before_planning(env, monkeypatch):
    provider = GeminiProvider(config())
    monkeypatch.setattr(provider, "plan", lambda *_: pytest.fail("Oversized question sent"))
    with pytest.raises(ValueError):
        Router(env.service, provider).ask("campus " * 400)


def test_selected_private_and_inaccessible_document_never_sent(env, monkeypatch):
    document, _ = pdf_document(env, private=True)
    provider = GeminiProvider(config(llm_data_policy="shared_only"))
    monkeypatch.setattr(provider, "exchange", lambda *_: pytest.fail("Private data sent externally"))
    result = Router(env.service, provider).ask("Zirconium scholarship deadline", document_id=document.id)
    assert result["answer_mode"] == "extractive" and result["provider_status"] == "private_data_blocked"
    with pytest.raises(HTTPException):
        Router(actor_service(env), provider).ask("Zirconium scholarship", document_id=document.id)


def test_generated_claims_and_selected_document_isolation(env, monkeypatch):
    document, _ = pdf_document(env)
    other, _ = pdf_document(env, "Beta", "Tuesday")
    provider = GeminiProvider(config())
    def exchange(_system, payload, _schema):
        evidence = json.loads(payload)["evidence"]
        assert all(h["document_id"] == document.id for h in evidence)
        hit = evidence[0]
        return json.dumps({"claims": [{"text": "The handbook records scholarship information.",
            "chunk_id": hit["chunk_id"], "quote": hit["text"][:80]}]})
    monkeypatch.setattr(provider, "exchange", exchange)
    result = Router(env.service, provider).ask("Zirconium scholarship deadline", document_id=document.id)
    assert result["answer_mode"] == "generated" and result["generated_claims"]
    assert all(c["document_id"] == document.id for c in result["generated_claims"])
    assert other.id not in str(result)


def test_phase4_evaluation(env):
    corpus = json.loads((Path(__file__).with_name("fixtures") / "campus_queries.json").read_text(encoding="utf-8"))
    results = []
    for case in corpus:
        answer = Router(env.service).ask(case["question"])
        source_ids = {s["id"] for s in answer["sources"]}
        citations_valid = all(env.service.source(sid) is not None for sid in source_ids)
        results.append({"question": case["question"], "routing_correct": answer["kind"] == case["kind"],
                        "citations_authorized": citations_valid, "provider_status": answer["provider_status"]})
    report = {"scope": "synthetic local deterministic campus corpus; no live model quality claim", "cases": results,
              "routing_correct": sum(r["routing_correct"] for r in results), "total": len(results),
              "citation_authorization_correct": sum(r["citations_authorized"] for r in results)}
    ground_checks = []
    for field, replacement in [(None, None), ("chunk_id", "foreign"), ("quote", "Fabricated supporting quote"), ("text", "Borrow 100 books")]:
        sample = json.loads(json.dumps(OUTPUT))
        if field:
            sample["claims"][0][field] = replacement
        try:
            validate_grounded(sample, [HIT])
            accepted = True
        except ValueError:
            accepted = False
        ground_checks.append({"case": field or "valid", "passed": accepted == (field is None)})
    document, _ = pdf_document(env, private=True)
    denied = False
    try:
        Router(actor_service(env)).ask("Zirconium scholarship", document_id=document.id)
    except HTTPException as exc:
        denied = exc.status_code == 404
    report.update(grounding_validation_checks=ground_checks, semantic_entailment_measured=False,
                  private_document_denial_correct=denied,
                  citation_metric="All emitted source IDs resolve through authorization; quote and ID checks are proxies, not factual entailment.")
    target = Path("artifacts/phase4-evaluation.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    assert all(r["routing_correct"] and r["citations_authorized"] for r in results)
    assert all(c["passed"] for c in ground_checks) and denied
