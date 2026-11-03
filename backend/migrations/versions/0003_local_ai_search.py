"""Local-model search: 1024-dimension embeddings, passage sections and section-aware search.

Existing embeddings cannot be converted between models, so they are cleared and each document
must be re-indexed. Keyword search keeps working until then.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
    ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section varchar(300) NOT NULL DEFAULT '';
    DROP INDEX IF EXISTS ix_chunks_vector;
    DROP INDEX IF EXISTS ix_chunks_fts;
    ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1024) USING NULL;
    UPDATE document_chunks SET embedding_model = '';
    CREATE INDEX ix_chunks_vector ON document_chunks USING hnsw(embedding vector_cosine_ops);
    CREATE INDEX ix_chunks_fts ON document_chunks
      USING gin(to_tsvector('english', section || ' ' || text));
    """)


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
    DROP INDEX IF EXISTS ix_chunks_vector;
    DROP INDEX IF EXISTS ix_chunks_fts;
    ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(384) USING NULL;
    UPDATE document_chunks SET embedding_model = '';
    ALTER TABLE document_chunks DROP COLUMN IF EXISTS section;
    CREATE INDEX ix_chunks_vector ON document_chunks USING hnsw(embedding vector_cosine_ops);
    CREATE INDEX ix_chunks_fts ON document_chunks USING gin(to_tsvector('english', text));
    """)
