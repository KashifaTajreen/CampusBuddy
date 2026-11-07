"""Permission filtering precedes vector / keyword retrieval. No global result cache."""

import re
import hashlib
from functools import lru_cache
from pathlib import Path
import numpy as np
from sqlalchemy import func, cast
from pgvector.sqlalchemy import Vector
from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from backend.app.config import settings
from backend.app.models import Document, DocumentChunk
from backend.app.ranking import reciprocal_rank_fusion, exact_references


@lru_cache(maxsize=1)
def semantic_model():
    if settings().embedding_provider == "lexical":
        return None
    path = settings().embedding_model_path
    if not path or not Path(path).is_dir():
        return None
    try:
        if settings().embedding_provider in ("auto", "onnx") and (Path(path)/"model_quantized.onnx").is_file():
            from backend.app.semantic import LocalE5
            return LocalE5(path)
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(path, local_files_only=True)
        if model.get_sentence_embedding_dimension() != 384:
            raise ValueError("Use a 384-dimensional embedding model.")
        digest = hashlib.sha256()
        for filename in sorted(p for p in Path(path).rglob("*") if p.suffix in (".json", ".safetensors", ".bin", ".model")):
            digest.update(str(filename.relative_to(path)).encode())
            with filename.open("rb") as stream:
                for block in iter(lambda:stream.read(1024*1024), b""):
                    digest.update(block)
        model.identity = "sentence-transformers-v2:"+digest.hexdigest()[:32]
        return model
    except ImportError:
        return None


def embeddings(texts, query=False):
    model = semantic_model()
    if model is not None:
        if hasattr(model, "identity"):
            values = model.encode(texts, query=query) if getattr(model, "supports_query_prefix", False) else model.encode(texts, normalize_embeddings=True)
            return values.tolist(), model.identity
        return model.encode(texts, normalize_embeddings=True).tolist(), "sentence-transformers:" + Path(
            settings().embedding_model_path
        ).name
    return HashingVectorizer(n_features=384, alternate_sign=False, norm="l2").transform(
        texts
    ).toarray().tolist(), "lexical-hashing-v1:384"


def injection(text):
    return bool(
        re.search(
            r"ignore .*instructions|system prompt|execute (?:sql|python)|reveal .*secret|override .*rules",
            text,
            re.I,
        )
    )


def extract_pages(filename, content):
    from backend.app.parsing import parse_file
    return [tuple(page) for page in parse_file(filename, content)["pages"]]


def chunks(pages):
    output = []
    for page, raw in pages:
        text = re.sub(r"[ \t]+", " ", raw).strip()
        cursor = 0
        while cursor < len(text):
            end = min(cursor + 1000, len(text))
            if end < len(text):
                split = max(text.rfind("\n", cursor + 500, end), text.rfind(". ", cursor + 500, end))
                if split > cursor:
                    end = split + 1
            output.append((page, text[cursor:end]))
            if end == len(text):
                break
            cursor = max(cursor + 1, end - 120)
    return output


class Retrieval:
    def __init__(self, service):
        self.c = service

    def search(self, query, limit=5, include_outdated=False, document_id=None,event_id=None):
        if not query.strip() or len(query) > 2000:
            raise ValueError("Use a document question of 1–2,000 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Retrieve 1–20 passages.")
        c = self.c
        visible = c.document_query(include_outdated)
        event=None
        if event_id is not None:
            event=c.event_record(event_id)
            visible=visible.where(Document.source_id==event.source_id,Document.category=='Campus Event')
        if document_id is not None:
            c.selected_document(document_id, include_outdated)
            visible = visible.where(Document.id == document_id)
        from backend.app.document_rows import identifiers, identity_match, mentor_value
        requested = identifiers(query)
        mentor_question=bool(re.search(r"mentor",query,re.I))
        allotment = bool(re.search(r"skill\s*lab|project\s*team|allot|allocated",query,re.I) or
            (mentor_question and re.search(r"\b(?:my|me|our|us)\b",query,re.I)))
        number = requested[0] if len(requested)==1 else None
        def missing_identity(message):
            return dict(answer=message,hits=[],conflicts=[],mode="exact_identifier",missing=True)
        if c.actor.role in {"student","canteen_manager"} and (requested or allotment):
            if not c.actor.student_number:
                return missing_identity("Your account has no verified USN. Ask a College Admin to verify it; a USN typed in chat cannot establish your identity.")
            if requested and set(requested) != {c.actor.student_number}:
                return missing_identity("I can look up only your verified student record. I cannot use another student's USN to access their allotment.")
            number = c.actor.student_number
        doc_ids = visible.with_only_columns(Document.id)
        base = c.scope(DocumentChunk).where(
            DocumentChunk.tenant_id == c.actor.tenant_id, DocumentChunk.document_id.in_(doc_ids)
        )
        if document_id is not None:
            base = base.where(DocumentChunk.document_id == document_id)
        if event is not None:
            base=base.where(DocumentChunk.row_data['event_key'].as_string()==event.natural_key)
        exact = bool(number)
        if exact:
            base = base.where(identity_match(DocumentChunk.student_numbers,number,c.db.bind.dialect.name))
        # Reference numbers and explicit ISO dates narrow authorized SQL candidates
        # before embeddings. Similarity cannot substitute a neighboring identifier.
        for reference in exact_references(query):
            pattern = r"(^|[^a-z0-9_])" + re.escape(reference) + r"($|[^a-z0-9_])"
            base = base.where(func.lower(DocumentChunk.text).regexp_match(pattern))
        if exact:
            qvec, model = [[0]*384], "exact_identifier"
        else:
            try:
                qvec, model = embeddings([query], query=True)
            except Exception:
                qvec = HashingVectorizer(n_features=384, alternate_sign=False, norm="l2").transform([query]).toarray().tolist()
                model = "lexical-hashing-v1:384"
        semantic_enabled = not model.startswith("lexical-hashing")
        keyword_ids = []
        if exact:
            candidates = list(c.db.scalars(base.limit(100)))
            model = "exact_identifier"
        elif c.db.bind.dialect.name == "postgresql":
            vector = cast(DocumentChunk.embedding, Vector(384)).cosine_distance(qvec[0])
            semantic = c.db.scalars(
                base.where(DocumentChunk.embedding_model == model).order_by(vector).limit(40)
            ).all()
            rank = func.ts_rank_cd(
                func.to_tsvector("english", DocumentChunk.text), func.plainto_tsquery("english", query)
            )
            keyword = c.db.scalars(base.where(rank > 0).order_by(rank.desc()).limit(40)).all()
            keyword_ids = [r.id for r in keyword]
            candidates = list({r.id: r for r in semantic + keyword}.values())
        else:
            candidates = list(c.db.scalars(base.limit(2000)))
        if not candidates:
            return dict(
                answer=("Insufficient evidence: the selected document has no searchable passages."
                        if document_id else "I could not find supporting campus information. Ask an administrator to upload the relevant document."),
                hits=[],
                conflicts=[],
                mode=model,
                missing=True,
            )
        texts = [r.text for r in candidates]
        try:
            tf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
            matrix = tf.fit_transform(texts)
            lexical = cosine_similarity(tf.transform([query]), matrix)[0]
        except ValueError:
            lexical = np.zeros(len(texts))
        scores = []
        for ix, row in enumerate(candidates):
            vector_score = (
                float(np.dot(np.asarray(row.embedding), np.asarray(qvec[0])))
                if row.embedding is not None and row.embedding_model == model
                else 0
            )
            if semantic_enabled:
                score = 0.65 * max(0, (vector_score-settings().semantic_min_score)/(1-settings().semantic_min_score+1e-9)) + 0.35 * lexical[ix]
            else:
                score = 0.7 * lexical[ix] + 0.3 * max(0, vector_score)
            if exact:
                mentor = bool(mentor_value((row.row_data or {}).get("values",{})))
                score = 1.0 + float(mentor and mentor_question) + float(lexical[ix])*.1
            scores.append(score + (0.04 if row.id in keyword_ids else 0))
        eligible = {r.id for r, score in zip(candidates, scores) if score >= 0.075}
        if semantic_enabled and not exact:
            dense = sorted((r for r in candidates if r.embedding is not None and r.embedding_model == model
                and float(np.dot(np.asarray(r.embedding),np.asarray(qvec[0]))) >= settings().semantic_min_score),
                key=lambda r:-float(np.dot(np.asarray(r.embedding),np.asarray(qvec[0]))))
            sparse = [candidates[i].id for i in sorted(range(len(candidates)),key=lambda i:-lexical[i]) if lexical[i]>0]
            sparse.extend(key for key in keyword_ids if key not in sparse)
            fused = reciprocal_rank_fusion([[r.id for r in dense],sparse])
            scores = [fused.get(r.id,0)*61/2 for r in candidates]
        picked = sorted(
            [(r, float(score)) for ix, (r, score) in enumerate(zip(candidates, scores))
             if r.id in eligible and score > 0 and (not document_id or not model.startswith("lexical-hashing") or lexical[ix] > 0)],
            key=lambda pair: -pair[1],
        )[:limit]
        hits = []

        def hit(row, score):
            doc = c.db.scalar(visible.where(Document.id == row.document_id))
            source = c.source(doc.source_id)
            return dict(
                id=row.id,
                document_id=doc.id,
                private=doc.owner_id is not None or source.student_records,
                text=row.text,
                page=row.page,
                title=doc.title,
                published=doc.published,
                expires=doc.expires,
                topic=doc.topic,
                claim=doc.claim,
                version=doc.version,
                supersedes_id=doc.supersedes_id,
                score=round(score, 4),
                source_id=source.id,
                source_filename=source.filename,
                source_timestamp=source.created_at.isoformat(),
                demo=source.demo,
                outdated=bool(doc.expires and doc.expires < str(c.local_now().date())),
                suspicious=injection(row.text),
                original_download_allowed=c.original_download_allowed(source),
                match_type="exact_identifier" if exact else "ranked_passage",
                **(dict(sheet=row.row_data.get("sheet"),row=row.row_data.get("row"),
                    mentor=mentor_value(row.row_data.get("values",{}))) if row.row_data else {}),
            )

        for row, score in picked:
            hits.append(hit(row, score))
        conflicts = []
        for topic in {h["topic"] for h in hits if h["topic"] and not exact}:
            peers = list(c.db.scalars(visible.where(Document.topic == topic)))
            # Explicit supersession is authoritative metadata; unresolved claims remain visible.
            superseded = {d.supersedes_id for d in peers if d.supersedes_id}
            active = [d for d in peers if d.id not in superseded and d.claim]
            if len({d.claim for d in active}) > 1:
                group = []
                for doc in active:
                    row = c.db.scalar(base.where(DocumentChunk.document_id == doc.id).limit(1))
                    if row:
                        item = hit(row, 0)
                        group.append(item)
                        if not any(h["id"] == item["id"] for h in hits):
                            hits.append(item)
                conflicts.append(dict(topic=topic, sources=group))
        if not hits:
            answer = ("Insufficient evidence: I could not find relevant information in the selected document. "
                      "Try a more specific question, switch documents, or clear the selection."
                      if document_id else "I could not find supporting campus information. Try a specific subject or upload the relevant notice.")
        else:
            lines = (
                [
                    "Conflicting notices give different claims. Confirm with the issuing office before relying on a date."
                ]
                if conflicts
                else []
            )
            for h in hits:
                prefix = "Archived / expired. " if h["outdated"] else ""
                excerpt = (
                    "Instruction-like content withheld. Open the source to inspect it."
                    if h["suspicious"]
                    else h["text"][:700]
                )
                lines.append(f"{prefix}{h['title']}: {excerpt}")
            answer = "\n\n".join(lines)
            if exact and mentor_question:
                mentors = list(dict.fromkeys(h["mentor"] for h in hits if h.get("mentor") and not h["suspicious"]))
                answer = ("The published spreadsheet lists the faculty mentor as "+"; ".join(mentors)+". See the cited worksheet row."
                          if mentors else "Your authorized student row was found, but it does not record a faculty mentor. Ask the department to publish the allotment.")
        return dict(
            answer=answer,
            hits=hits,
            conflicts=conflicts,
            mode=model,
            missing=not hits,
            score_note="Similarity ranks passages; it is not a probability of correctness.",
        )
