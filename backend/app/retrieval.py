"""Permission filtering precedes vector / keyword retrieval. No global result cache."""

import io
import logging
import re
from functools import lru_cache
from pathlib import Path
import numpy as np
from sqlalchemy import select, func, cast, literal_column
from pgvector.sqlalchemy import Vector
from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from backend.app.ai import OllamaProvider, ProviderError
from backend.app.config import settings
from backend.app.models import Document, DocumentChunk, EMBEDDING_DIMENSION

logger = logging.getLogger("campus.retrieval")


@lru_cache(maxsize=1)
def semantic_model():
    path = settings().embedding_model_path
    if not path or not Path(path).is_dir():
        return None
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(path, local_files_only=True)
        if model.get_sentence_embedding_dimension() != EMBEDDING_DIMENSION:
            raise ValueError(f"Use a {EMBEDDING_DIMENSION}-dimensional embedding model.")
        return model
    except ImportError:
        return None


class EmbeddingUnavailable(ValueError):
    """The configured embedding model failed. Never replaced with invented vectors."""


def embeddings(texts):
    config = settings()
    if config.ollama_embedding_model:
        try:
            vectors = OllamaProvider().embed(texts, config.ollama_embedding_model)
        except ProviderError as exc:
            raise EmbeddingUnavailable("The embedding model is unavailable. Try again shortly.") from exc
        if any(len(v) != EMBEDDING_DIMENSION for v in vectors):
            raise EmbeddingUnavailable(f"Use a {EMBEDDING_DIMENSION}-dimensional embedding model.")
        return vectors, "ollama:" + config.ollama_embedding_model
    model = semantic_model()
    if model is not None:
        return model.encode(texts, normalize_embeddings=True).tolist(), "sentence-transformers:" + Path(
            config.embedding_model_path
        ).name
    return HashingVectorizer(n_features=EMBEDDING_DIMENSION, alternate_sign=False, norm="l2").transform(
        texts
    ).toarray().tolist(), f"lexical-hashing-v2:{EMBEDDING_DIMENSION}"


RERANKER_FLOOR = 0.0001


@lru_cache(maxsize=1)
def reranker_model():
    path = settings().reranker_model_path
    if not path or not Path(path).is_dir():
        return None
    try:
        from sentence_transformers import CrossEncoder

        # CPU keeps the GPU's memory available for the LLM.
        return CrossEncoder(path, local_files_only=True, device="cpu")
    except ImportError:
        return None


def reciprocal_rank_fusion(*rankings, k=60):
    """Combine rankings by position, because vector and keyword scores are not comparable."""
    scores = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return scores


def injection(text):
    return bool(
        re.search(
            r"ignore .*instructions|system prompt|execute (?:sql|python)|reveal .*secret|override .*rules",
            text,
            re.I,
        )
    )


@lru_cache(maxsize=1)
def docling_converter():
    from docling.document_converter import DocumentConverter

    return DocumentConverter()


def docling_pages(filename, content):
    """Layout-aware Markdown per page, or None so the caller keeps the PyMuPDF text layer."""
    try:
        from docling.datamodel.base_models import DocumentStream

        result = docling_converter().convert(DocumentStream(name=filename, stream=io.BytesIO(content)))
        document = result.document
        pages = [(n, document.export_to_markdown(page_no=n)) for n in sorted(document.pages)]
    except Exception as exc:  # Docling raises many library-specific errors; parsing must still work.
        logger.warning("Docling parsing unavailable: %s", type(exc).__name__)
        return None
    return pages if any(text.strip() for _, text in pages) else None


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
        except RuntimeError as exc:
            raise ValueError("Unreadable PDF. Export it again and retry.") from exc
        if settings().document_parser == "docling":
            pages = docling_pages(filename, content) or pages
    elif suffix in (".txt", ".md"):
        try:
            pages = [(1, content.decode("utf-8-sig"))]
        except UnicodeDecodeError as exc:
            raise ValueError("Use UTF-8 encoded text.") from exc
    else:
        raise ValueError("Use PDF, TXT or Markdown documents.")
    if not any(t.strip() for _, t in pages):
        raise ValueError("No readable text found. Run OCR on scanned pages first.")
    if sum(len(t) for _, t in pages) > 2_000_000:
        raise ValueError("Split large documents into smaller files.")
    return pages


HEADING = re.compile(
    r"^(?:#{1,6}\s+(?P<md>.+)|(?P<num>\d+(?:\.\d+)*[.)]?\s+[A-Z].{2,78})|(?P<caps>[A-Z][A-Z0-9 &/,:()-]{3,78}))$"
)


def heading_of(line):
    match = HEADING.match(line.strip())
    if not match:
        return None
    return (match["md"] or match["num"] or match["caps"]).strip(" #:")[:300]


def split_sections(raw, current):
    """Split one page at its headings, carrying the open section across page breaks."""
    sections = []
    body = []
    for line in raw.splitlines():
        heading = heading_of(line)
        if heading:
            if "".join(body).strip():
                sections.append((current, "\n".join(body)))
            body = []
            current = heading
        else:
            body.append(line)
    if "".join(body).strip():
        sections.append((current, "\n".join(body)))
    return sections, current


def chunks(pages):
    """Return (page, section, text) passages, about 1,000 characters with 120 overlapping."""
    output = []
    current = ""
    for page, raw in pages:
        sections, current = split_sections(raw, current)
        for section, body in sections:
            text = re.sub(r"[ \t]+", " ", body).strip()
            cursor = 0
            while cursor < len(text):
                end = min(cursor + 1000, len(text))
                if end < len(text):
                    split = max(text.rfind("\n", cursor + 500, end), text.rfind(". ", cursor + 500, end))
                    if split > cursor:
                        end = split + 1
                output.append((page, section, text[cursor:end]))
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
        try:
            qvec, model = embeddings([query])
        except EmbeddingUnavailable:
            # Keyword search still works, and the response reports the reduced mode.
            qvec, model = None, "keyword-only"
        semantic_floor = 0.2 if model.startswith("lexical") else settings().retrieval_min_similarity
        by_id = {}
        vector_ranked = []
        keyword_ranked = []
        if c.db.bind.dialect.name == "postgresql":
            if qvec is not None:
                distance = cast(DocumentChunk.embedding, Vector(EMBEDDING_DIMENSION)).cosine_distance(qvec[0])
                rows = c.db.execute(
                    base.add_columns((1 - distance).label("similarity"))
                    .where(DocumentChunk.embedding_model == model)
                    .where((1 - distance) >= semantic_floor)
                    .order_by(distance)
                    .limit(40)
                ).all()
                by_id.update({r.id: r for r, _ in rows})
                vector_ranked = [r.id for r, _ in rows]
            # Matches the expression of the ix_chunks_fts index.
            searchable = func.to_tsvector(
                literal_column("'english'::regconfig"), DocumentChunk.section + " " + DocumentChunk.text
            )
            rank = func.ts_rank_cd(
                searchable, func.plainto_tsquery(literal_column("'english'::regconfig"), query)
            )
            rows = c.db.scalars(base.where(rank > 0).order_by(rank.desc()).limit(40)).all()
            by_id.update({r.id: r for r in rows})
            keyword_ranked = [r.id for r in rows]
        else:
            rows = list(c.db.scalars(base.limit(2000)))
            by_id.update({r.id: r for r in rows})
            if qvec is not None:
                query_vector = np.asarray(qvec[0])
                similarity = {
                    r.id: float(np.dot(np.asarray(r.embedding), query_vector))
                    for r in rows
                    if r.embedding is not None and r.embedding_model == model
                }
                vector_ranked = sorted(
                    (i for i, value in similarity.items() if value >= semantic_floor),
                    key=lambda i: -similarity[i],
                )[:40]
            if rows:
                try:
                    tf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
                    matrix = tf.fit_transform([f"{r.section} {r.text}" for r in rows])
                    lexical = cosine_similarity(tf.transform([query]), matrix)[0]
                except ValueError:
                    lexical = np.zeros(len(rows))
                keyword_ranked = [rows[i].id for i in np.argsort(-lexical)[:40] if lexical[i] >= 0.05]
        fused = reciprocal_rank_fusion(vector_ranked, keyword_ranked)
        if not fused:
            return dict(
                answer="I could not find supporting campus information. Ask an administrator to upload the relevant document.",
                hits=[],
                conflicts=[],
                mode=model,
                missing=True,
            )
        ordered = sorted(fused, key=lambda i: -fused[i])[:20]
        reranker = reranker_model()
        if reranker is not None:
            scores = reranker.predict([(query, by_id[i].text) for i in ordered])
            ranked = sorted(zip(ordered, scores), key=lambda pair: -pair[1])
            # bge-reranker scores are very small even for correct passages, so this floor only drops
            # passages that do not address the question. Re-measure it with benchmark_models.py.
            picked = [(by_id[i], float(score)) for i, score in ranked if score >= RERANKER_FLOOR][:limit]
        else:
            picked = [(by_id[i], fused[i]) for i in ordered[:limit]]
        hits = []

        def hit(row, score):
            doc = c.db.scalar(visible.where(Document.id == row.document_id))
            source = c.source(doc.source_id)
            return dict(
                id=row.id,
                document_id=doc.id,
                text=row.text,
                page=row.page,
                section=row.section,
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
