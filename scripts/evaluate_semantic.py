"""Real offline multilingual retrieval evaluation against synthetic campus facts."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import numpy as np
from sqlalchemy.orm import Session
from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer
from backend.app.config import Settings
from backend.app import retrieval
from backend.app.models import Base, Institution, Membership
from backend.app.db import make_engine
from backend.app.auth import resolve_actor
from backend.app.services import CampusService
from backend.app.ingestion import Ingestion
from backend.app.publication import Publication
from backend.app.schemas import ImportOptions
from backend.tests.conftest import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
FACTS = [
    ("Library hours", "The college library opens at eight in the morning and closes at six in the evening on weekdays.",
     ["When can I visit the library?", "पुस्तकालय कितने बजे खुलता है?", "ಗ್ರಂಥಾಲಯ ಯಾವಾಗ ತೆರೆಯುತ್ತದೆ?"]),
    ("Exam documents", "Students must bring their hall ticket and college identity card to enter an examination hall.",
     ["What documents must I carry to an exam?", "परीक्षा के लिए कौन से दस्तावेज़ लाने चाहिए?", "ಪರೀಕ್ಷೆಗೆ ಯಾವ ದಾಖಲೆಗಳನ್ನು ತರಬೇಕು?"]),
    ("College holiday", "The campus is closed for the annual festival on November fifteenth. Regular classes do not meet on that holiday.",
     ["Are classes cancelled for the annual festival?", "वार्षिक उत्सव पर कक्षाएं रद्द हैं?", "ವಾರ್ಷಿಕ ಹಬ್ಬದಂದು ತರಗತಿಗಳು ರದ್ದಾಗಿವೆಯೇ?"]),
    ("Scholarship application", "Scholarship applications must be submitted to the student welfare office before the final Friday of November.",
     ["Where should I submit my scholarship application?", "छात्रवृत्ति का आवेदन कहाँ जमा करना है?", "ವಿದ್ಯಾರ್ಥಿವೇತನ ಅರ್ಜಿಯನ್ನು ಎಲ್ಲಿ ಸಲ್ಲಿಸಬೇಕು?"]),
    ("Campus medical help", "The campus medical room provides first aid near the main administrative building. Ask security for urgent medical assistance.",
     ["Where can I get first aid on campus?", "कॉलेज में प्राथमिक उपचार कहाँ मिलेगा?", "ಕಾಲೇಜಿನಲ್ಲಿ ಪ್ರಥಮ ಚಿಕಿತ್ಸೆ ಎಲ್ಲಿ ಸಿಗುತ್ತದೆ?"]),
    ("Canteen payment", "The single campus canteen accepts cash and UPI payments. Food prices are displayed on the daily approved menu.",
     ["Can I pay for food with UPI?", "क्या कैंटीन में यूपीआई से भुगतान कर सकते हैं?", "ಕ್ಯಾಂಟೀನ್‌ನಲ್ಲಿ ಯುಪಿಐ ಮೂಲಕ ಪಾವತಿಸಬಹುದೇ?"]),
]


def main():
    config = Settings(_env_file=None, database_url="sqlite:///:memory:", embedding_provider="onnx",
        embedding_model_path=str(ROOT / ".scratch/semantic-e5"), llm_enabled=False, llm_provider="none")
    retrieval.settings = lambda: config
    retrieval.semantic_model.cache_clear()
    model = retrieval.semantic_model()
    assert model is not None, "Approved local model is not installed"
    texts = [text for _, text, _ in FACTS]
    vectors = model.encode(texts)
    lexical = HashingVectorizer(n_features=384, alternate_sign=False, norm="l2")
    baseline = lexical.transform(texts).toarray()
    tf = TfidfVectorizer(ngram_range=(1,2), stop_words="english")
    tf.fit(texts)
    work = Path(tempfile.mkdtemp(prefix="semantic-evaluation-", dir=ROOT / ".scratch"))
    engine = make_engine("sqlite:///"+(work/"evaluation.db").as_posix())
    Base.metadata.create_all(engine)
    tenant = "00000000-0000-4000-8000-000000000061"
    author, reviewer, student = [f"00000000-0000-4000-8000-{n:012}" for n in (62,63,64)]
    with Session(engine) as db:
        db.add(Institution(id=tenant, name="Synthetic semantic evaluation", demo=True))
        db.flush()
        for user, role in ((author,"institution_admin"),(reviewer,"institution_admin"),(student,"student")):
            db.add(Membership(tenant_id=tenant, user_id=user, role=role, department="AIML", name="Synthetic evaluator"))
        db.commit()
        admin = CampusService(db, resolve_actor(db, author, tenant))
        review = CampusService(db, resolve_actor(db, reviewer, tenant))
        store = MemoryStore()
        for title, text, _ in FACTS:
            ingestion = Ingestion(admin, store)
            job = ingestion.receive(title+".txt", text.encode(), ImportOptions(kind="document", title=title, demo=True))
            ingestion.confirm(job)
            ingestion.process(job)
            job.status = "completed"
            db.flush()
            Publication(review).transition(job.source_id,"approve")
            Publication(admin).transition(job.source_id,"publish")
        db.commit()
        service = CampusService(db, resolve_actor(db, student, tenant))
        cases = []
        for gold, (title, _, questions) in enumerate(FACTS):
            for language, question in zip(("en","hi","kn"), questions):
                q = model.encode([question], query=True)[0]
                semantic = vectors @ q
                lex = (baseline @ lexical.transform([question]).toarray()[0])
                lexical_tfidf = (tf.transform(texts) @ tf.transform([question]).T).toarray().reshape(-1)
                hybrid = .65*np.maximum(0,(semantic-config.semantic_min_score)/(1-config.semantic_min_score))+.35*lexical_tfidf
                result = retrieval.Retrieval(service).search(question, limit=3)
                cases.append(dict(language=language, question=question, expected=title,
                    lexical_top1=int(np.argmax(lex)) == gold, semantic_top1=int(np.argmax(semantic)) == gold,
                    hybrid_top1=int(np.argmax(hybrid)) == gold, semantic_gold_score=round(float(semantic[gold]),4),
                    actual_retrieval_top1=bool(result["hits"] and result["hits"][0]["title"] == title),
                    mode=result["mode"]))
        scores = {language:{mode:sum(c[mode] for c in cases if c["language"]==language)/6
            for mode in ("lexical_top1","semantic_top1","hybrid_top1","actual_retrieval_top1")} for language in ("en","hi","kn")}
        report = dict(executed_at=datetime.now().astimezone().isoformat(), scope="18 synthetic translated queries, six published English campus passages",
            model=model.identity, dimensions=384, scores=scores, cases=cases,
            acceptance_passed=all(v["actual_retrieval_top1"] >= .75 for v in scores.values()),
            representative_institutional_accuracy_verified=False, credentials_used=False)
        (ROOT/"artifacts/phase6b-semantic-evaluation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"scores":scores,"acceptance_passed":report["acceptance_passed"]}))
    engine.dispose()


if __name__ == "__main__":
    main()
