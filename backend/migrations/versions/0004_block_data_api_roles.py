"""Keep Supabase's public Data API roles away from CampusOS objects.

Supabase's default privileges grant anon and authenticated access to new objects, and migration 0002
only revokes from PUBLIC. The browser reaches campus data through FastAPI, so these roles need nothing.
Objects are revoked by name because the schema may also hold tables that belong to another application.
"""

import os
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = [
    "audit_logs",
    "campus_records",
    "conversations",
    "document_chunks",
    "documents",
    "ingestion_jobs",
    "institutions",
    "integration_connections",
    "memberships",
    "messages",
    "query_traces",
    "rate_buckets",
    "sources",
]
FUNCTIONS = [
    "campus_permitted(text,text,text)",
    "campus_admin(text,text)",
    "campus_upsert_membership(text,text,text,text,integer,text,text)",
]


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    version_table = os.getenv("ALEMBIC_VERSION_TABLE", "alembic_version")
    statements = [f"REVOKE ALL ON public.{t} FROM %1$I" for t in [*TABLES, version_table]]
    statements += [f"REVOKE ALL ON FUNCTION public.{f} FROM %1$I" for f in FUNCTIONS]
    body = "\n".join(f"      EXECUTE format('{s}', api_role);" for s in statements)
    op.execute(f"""
    DO $$
    DECLARE api_role text;
    BEGIN
      FOREACH api_role IN ARRAY ARRAY['anon','authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=api_role) THEN
{body}
        END IF;
      END LOOP;
    END $$;
    """)


def downgrade():
    # Restoring public API access to these tables would be unsafe, so this stays revoked.
    pass
