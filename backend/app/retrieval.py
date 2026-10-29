"""Permission filtering precedes vector / keyword retrieval. No global result cache."""

import re
from functools import lru_cache
from pathlib import Path
import numpy as np
from sqlalchemy import select, func, cast
from pgvector.sqlalchemy import Vector
from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from backend.app.config import settings
from backend.app.models import Document, DocumentChunk


@lru_cache(maxsize=1)
def semantic_model():
    path = settings().embedding_model_path
    if not path or not Path(path).is_dir():
        return None
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(path, local_files_only=True)
        if model.get_sentence_embedding_dimension() != 384:
            raise ValueError("Use a 384-dimensional embedding model.")
        return model
    except ImportError:
        return None


def embeddings(texts):
    model = semantic_model()
    if model is not None:
        return model.encode(texts, normalize_embeddings=True).tolist(), "sentence-transformers:" + Path(
            settings().embedding_model_path
        ).name
    config = settings()
    if config.gemini_api_key and config.app_env != "test" and texts:
        try:
            import httpx
            reqs = [
                {
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": str(t)[:2000]}]},
                    "outputDimensionality": 384,
                }
                for t in texts
            ]
            vectors = []
            for i in range(0, len(reqs), 50):
                batch = reqs[i : i + 50]
                with httpx.Client(timeout=15) as client:
                    resp = client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents?key={config.gemini_api_key}",
                        json={"requests": batch},
                    )
                if resp.status_code == 200:
                    for emb in resp.json().get("embeddings", []):
                        vals = emb.get("values", [])
                        if len(vals) == 384:
                            vectors.append(vals)
            if len(vectors) == len(texts):
                return vectors, "gemini-embedding-001:384"
        except Exception:
            pass
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


def ocr_scanned_pdf(document):
    config = settings()
    if not config.gemini_api_key:
        return []
    import base64
    import httpx
    ocr_pages = []
    for i, page in enumerate(document):
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        b64_data = base64.b64encode(png_bytes).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.gemini_model}:generateContent"
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcribe all text from this scanned academic document page accurately into markdown. Preserve tables, dates, numbers, headings, and structure."},
                    {"inline_data": {"mime_type": "image/png", "data": b64_data}}
                ]
            }]
        }
        try:
            with httpx.Client(timeout=45) as client:
                resp = client.post(url, headers={"x-goog-api-key": config.gemini_api_key}, json=payload)
                resp.raise_for_status()
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                if text.strip():
                    ocr_pages.append((i + 1, text))
        except Exception:
            continue
    return ocr_pages


def extract_pages(filename, content):
    if not content or len(content) > 15 * 1024 * 1024:
        raise ValueError("Upload a nonempty file under 15 MB.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        import fitz

        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Unlock this PDF before uploading.")
                if len(document) > 200:
                    raise ValueError("Split PDFs larger than 200 pages.")
                pages = [(i + 1, p.get_text()) for i, p in enumerate(document)]
                if not any(t.strip() for _, t in pages):
                    pages = ocr_scanned_pdf(document)
        except RuntimeError as exc:
            raise ValueError("Unreadable PDF. Export it again and retry.") from exc
    elif suffix in (".txt", ".md"):
        try:
            pages = [(1, content.decode("utf-8-sig"))]
        except UnicodeDecodeError as exc:
            raise ValueError("Use UTF-8 encoded text.") from exc
    elif suffix in (".png", ".jpg", ".jpeg"):
        config = settings()
        if not config.gemini_api_key:
            raise ValueError("Image OCR requires GEMINI_API_KEY.")
        import base64
        import httpx
        b64_data = base64.b64encode(content).decode("utf-8")
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.gemini_model}:generateContent"
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcribe all text from this campus notice/document image accurately into markdown. Preserve tables, dates, numbers, headings, and structure."},
                    {"inline_data": {"mime_type": mime, "data": b64_data}}
                ]
            }]
        }
        with httpx.Client(timeout=45) as client:
            resp = client.post(url, headers={"x-goog-api-key": config.gemini_api_key}, json=payload)
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            pages = [(1, text)]
    else:
        raise ValueError("Use PDF, TXT, Markdown, PNG or JPG documents.")
    if not any(t.strip() for _, t in pages):
        raise ValueError("No readable text found. Run OCR on scanned pages first.")
    if sum(len(t) for _, t in pages) > 2_000_000:
        raise ValueError("Split large documents into smaller files.")
    return pages


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

    def search(self, query, limit=5, include_outdated=False):
        if not query.strip() or len(query) > 2000:
            raise ValueError("Use a document question of 1–2,000 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Retrieve 1–20 passages.")
        c = self.c
        visible = c.scope(Document)
        if not include_outdated:
            visible = visible.where(
                (Document.expires.is_(None)) | (Document.expires >= str(c.clock().date()))
            )
        doc_ids = visible.with_only_columns(Document.id)
        base = select(DocumentChunk).where(
            DocumentChunk.tenant_id == c.actor.tenant_id, DocumentChunk.document_id.in_(doc_ids)
        )
        qvec, model = embeddings([query])
        keyword_ids = []
        if c.db.bind.dialect.name == "postgresql":
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
                answer="I could not find supporting campus information. Ask an administrator to upload the relevant document.",
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
            scores.append(
                0.7 * lexical[ix] + 0.3 * max(0, vector_score) + (0.04 if row.id in keyword_ids else 0)
            )
        picked = sorted(
            [(r, float(score)) for r, score in zip(candidates, scores) if score >= 0.05],
            key=lambda pair: -pair[1],
        )[:max(limit, 8)]
        hits = []

        def hit(row, score):
            doc = c.db.scalar(visible.where(Document.id == row.document_id))
            source = c.source(doc.source_id)
            return dict(
                id=row.id,
                document_id=doc.id,
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
                outdated=bool(doc.expires and doc.expires < str(c.clock().date())),
                suspicious=injection(row.text),
            )

        for row, score in picked:
            hits.append(hit(row, score))
        conflicts = []
        for topic in {h["topic"] for h in hits if h["topic"]}:
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
            answer = "I could not find supporting campus information. Try a specific subject or upload the relevant notice."
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
        return dict(
            answer=answer,
            hits=hits,
            conflicts=conflicts,
            mode=model,
            missing=not hits,
            score_note="Similarity ranks passages; it is not a probability of correctness.",
        )
