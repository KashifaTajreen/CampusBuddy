"""Add independent file state and restricted, auditable deletion functions.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "file_state" not in {c["name"] for c in sa.inspect(bind).get_columns("sources")}:
        op.add_column("sources", sa.Column("file_state", sa.String(20), nullable=False, server_default="active"))
    if bind.dialect.name != "postgresql":
        return
    # Preserve existing predicates and privileges; require active files in addition.
    for function in ("campus_source_visible(text,text,text,text)", "campus_source_writable(text,text,text,text)"):
        definition = bind.scalar(sa.text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": function})
        if "s.file_state" not in definition:
            definition = definition.replace("s.id=sid AND s.tenant_id=t", "s.id=sid AND s.file_state='active' AND s.tenant_id=t")
            op.execute(definition)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.campus_file_action(sid text,act text,confirmation text,replace_id text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    DECLARE s sources%ROWTYPE; previous sources%ROWTYPE; who text;
    BEGIN
      who := current_setting('app.user_id',true);
      SELECT * INTO s FROM sources WHERE id=sid FOR UPDATE;
      PERFORM 1 FROM memberships WHERE tenant_id=s.tenant_id AND user_id=who FOR SHARE;
      IF NOT FOUND OR s.tenant_id IS DISTINCT FROM current_setting('app.tenant_id',true)
        OR NOT coalesce(campus_permitted(s.tenant_id,s.owner_id,s.department),false)
        OR NOT coalesce(((s.owner_id=who) OR (s.owner_id IS NULL AND campus_admin(s.tenant_id,s.department)
          AND (s.kind='document' OR s.uploaded_by=who OR campus_top_admin(s.tenant_id)))),false) THEN
        RAISE EXCEPTION 'File permission required' USING ERRCODE='42501';
      END IF;
      IF act='delete' AND s.file_state='active' THEN
        UPDATE sources SET file_state='deleted',publication_status='revoked' WHERE id=sid;
      ELSIF act='begin_purge' AND s.file_state IN ('deleted','purge_pending') AND confirmation='DELETE '||sid THEN
        UPDATE sources SET file_state='purge_pending',publication_status='revoked' WHERE id=sid;
      ELSIF act='finish_purge' AND s.file_state='purge_pending' AND confirmation='DELETE '||sid THEN
        UPDATE sources SET file_state='purged',filename='[purged]',storage_key='',checksum='purged:'||sid,review_note='',
          approved_by=NULL,approved_at=NULL,published_by=NULL,published_at=NULL WHERE id=sid;
      ELSIF (act='restore' AND s.file_state='deleted') OR
            (act='rollback' AND s.file_state='active' AND s.publication_status='replaced') THEN
        IF act='rollback' THEN
          SELECT * INTO previous FROM sources WHERE id=replace_id FOR UPDATE;
          IF NOT FOUND OR previous.id=s.id OR previous.tenant_id!=s.tenant_id OR previous.kind!=s.kind
            OR previous.department!=s.department OR previous.owner_id IS NOT NULL
            OR previous.file_state!='active' OR previous.publication_status!='published' THEN
            RAISE EXCEPTION 'Matching current published replacement required' USING ERRCODE='22023';
          END IF;
        END IF;
        UPDATE sources SET file_state='active',publication_status='draft',uploaded_by=who,
          approved_by=NULL,approved_at=NULL,published_by=NULL,published_at=NULL,review_note='',
          replaces_source_id=CASE WHEN act='rollback' THEN replace_id ELSE NULL END WHERE id=sid;
      ELSE
        RAISE EXCEPTION 'Invalid file state or confirmation' USING ERRCODE='22023';
      END IF;
      IF act IN ('delete','begin_purge','finish_purge','rollback') THEN
        DELETE FROM document_chunks WHERE tenant_id=s.tenant_id AND document_id IN
          (SELECT id FROM documents WHERE source_id=sid AND tenant_id=s.tenant_id);
        DELETE FROM campus_records WHERE tenant_id=s.tenant_id AND source_id=sid;
        UPDATE messages SET content='{"answer":"Source withdrawn. Ask again using current published information.","kind":"text","records":[],"sources":[],"followups":[]}'::json
          WHERE tenant_id=s.tenant_id AND role='assistant' AND content::text LIKE '%'||sid||'%';
        UPDATE conversations SET context='{}'::json WHERE tenant_id=s.tenant_id AND context->>'document_id' IN
          (SELECT id FROM documents WHERE source_id=sid AND tenant_id=s.tenant_id);
      END IF;
      UPDATE ingestion_jobs SET status='cancelled',preview='{}'::json,error=NULL
        WHERE source_id=sid AND tenant_id=s.tenant_id;
      IF act='finish_purge' THEN
        DELETE FROM documents WHERE source_id=sid AND tenant_id=s.tenant_id;
        UPDATE ingestion_jobs SET options='{}'::json,staged_data='{}'::json WHERE source_id=sid AND tenant_id=s.tenant_id;
      END IF;
      INSERT INTO audit_logs(id,tenant_id,user_id,action,details,created_at)
        VALUES(md5(random()::text||clock_timestamp()::text),s.tenant_id,who,'file.'||act,
          json_build_object('source_id',sid,'replaces_source_id',replace_id),now());
    END $$;
    REVOKE ALL ON FUNCTION public.campus_file_action(text,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_file_action(text,text,text,text) TO campus_api;
    """)


def downgrade():
    raise RuntimeError("File tombstones must not be discarded automatically; restore from a reviewed backup.")
