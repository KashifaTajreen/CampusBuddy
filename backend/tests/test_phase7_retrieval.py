import pytest
from backend.app.ranking import reciprocal_rank_fusion
from backend.app.ingestion import Ingestion
from backend.app.schemas import ImportOptions
from backend.app.retrieval import Retrieval
from backend.tests.test_product_workflows import finish, member


def test_fusion_rewards_agreement_without_counting_duplicate_hits():
    scores=reciprocal_rank_fusion([["dense-only","shared","shared"],["shared","keyword-only"]])
    assert max(scores,key=scores.get)=="shared"
    assert scores==reciprocal_rank_fusion([["dense-only","shared"],["shared","keyword-only"]])


@pytest.mark.parametrize("reference,neighbor",[("RV-2026-07","RV-2026-071"),("2026-11-14","2026-11-15")])
def test_explicit_document_reference_is_exact_before_ranking(env,reference,neighbor):
    for value in (reference,neighbor):
        job=Ingestion(env.service,env.store).receive(value+".txt",
            f"Official reference {value}. The library scholarship deadline is recorded in this circular.".encode(),
            ImportOptions(kind="document",title=value))
        finish(env,env.service,job)
    answer=Retrieval(member(env,"student")).search("What does circular "+reference+" say about the scholarship deadline?")
    assert answer["hits"] and all(neighbor not in h["text"] for h in answer["hits"])
    assert not Retrieval(member(env,"student")).search("What does circular ZZ-2099-99 say?")["hits"]
