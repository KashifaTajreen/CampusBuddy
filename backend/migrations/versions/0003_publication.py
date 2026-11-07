"""Source publication, staged structured imports and publication-aware RLS.

Preserves previously processed data as explicitly labelled legacy publication.
No original files or existing domain records are deleted or rewritten.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("sources")}
    additions = [
        sa.Column(
            "publication_status",
            sa.String(20),
            sa.CheckConstraint(
                "publication_status IN ('draft','pending_review','approved','published','rejected','revoked','replaced')",
                name="ck_source_publication_status",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(36), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=False, server_default=""),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("sources", column)
    if "replaces_source_id" not in columns:
        if bind.dialect.name == "sqlite":
            # SQLite supports this nullable REFERENCES addition without rebuilding
            # a parent table that existing records and documents reference.
            op.execute("ALTER TABLE sources ADD COLUMN replaces_source_id VARCHAR(36) REFERENCES sources(id)")
        else:
            op.add_column("sources", sa.Column("replaces_source_id", sa.String(36)))
            op.create_foreign_key(
                "fk_source_replacement", "sources", "sources", ["replaces_source_id"], ["id"]
            )
    job_columns = {c["name"] for c in sa.inspect(bind).get_columns("ingestion_jobs")}
    if "staged_data" not in job_columns:
        op.add_column(
            "ingestion_jobs", sa.Column("staged_data", sa.JSON(), nullable=False, server_default="{}")
        )

    # 0001 historically uses current ORM metadata, so fresh databases can already
    # contain these columns. Conditional ADDs support both fresh and 0002 databases.
    op.execute("""
        UPDATE sources SET publication_status='published',
            review_note='Legacy processed data: independent approval was not recorded.',
            published_at=created_at
        WHERE publication_status='draft' AND (
            EXISTS(SELECT 1 FROM campus_records r WHERE r.source_id=sources.id AND r.tenant_id=sources.tenant_id)
            OR EXISTS(SELECT 1 FROM documents d WHERE d.source_id=sources.id AND d.tenant_id=sources.tenant_id)
        ) AND (
            owner_id IS NOT NULL OR EXISTS(
                SELECT 1 FROM ingestion_jobs j WHERE j.source_id=sources.id
                AND j.tenant_id=sources.tenant_id AND j.status='completed'
            )
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_publication ON sources(tenant_id,publication_status)")
    if bind.dialect.name != "postgresql":
        return
    constraints = {c["name"] for c in sa.inspect(bind).get_check_constraints("sources")}
    if "ck_source_publication_status" not in constraints:
        op.create_check_constraint(
            "ck_source_publication_status",
            "sources",
            "publication_status IN ('draft','pending_review','approved','published','rejected','revoked','replaced')",
        )
    op.execute("""
    CREATE OR REPLACE FUNCTION public.campus_top_admin(t text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND EXISTS(
        SELECT 1 FROM memberships m WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true)
        AND m.active AND m.role IN ('institution_admin','platform_admin')
      )
    $$;
    REVOKE ALL ON FUNCTION public.campus_top_admin(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_top_admin(text) TO campus_api;

    CREATE OR REPLACE FUNCTION public.campus_source_visible(sid text,t text,o text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT campus_permitted(t,o,d) AND EXISTS(
        SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t
        AND s.owner_id IS NOT DISTINCT FROM o AND campus_permitted(s.tenant_id,s.owner_id,s.department)
        AND (s.publication_status='published' OR
             (s.owner_id IS NULL AND campus_admin(t,s.department)))
      )
    $$;
    CREATE OR REPLACE FUNCTION public.campus_source_writable(sid text,t text,o text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT campus_permitted(t,o,d) AND EXISTS(
        SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t
        AND s.owner_id IS NOT DISTINCT FROM o
        AND s.publication_status IN ('draft','pending_review','approved','published')
        AND campus_permitted(s.tenant_id,s.owner_id,s.department)
        AND ((o=current_setting('app.user_id',true)) OR
             (o IS NULL AND campus_admin(t,d) AND campus_admin(t,s.department)))
      )
    $$;
    CREATE OR REPLACE FUNCTION public.campus_replacement_allowed(sid text,t text,k text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT sid IS NULL OR EXISTS(
        SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t AND s.kind=k AND s.department=d
        AND s.owner_id IS NULL AND s.publication_status='published' AND campus_admin(t,d)
      )
    $$;
    REVOKE ALL ON FUNCTION public.campus_source_visible(text,text,text,text),
      public.campus_source_writable(text,text,text,text),public.campus_replacement_allowed(text,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_source_visible(text,text,text,text),
      public.campus_source_writable(text,text,text,text),public.campus_replacement_allowed(text,text,text,text) TO campus_api;

    DROP POLICY source_read ON sources;
    REVOKE UPDATE,DELETE ON sources FROM campus_api;
    CREATE POLICY source_read ON sources FOR SELECT TO campus_api USING(
      campus_permitted(tenant_id,owner_id,department) AND (
        publication_status='published' OR owner_id=current_setting('app.user_id',true)
        OR (owner_id IS NULL AND campus_admin(tenant_id,department))
      )
    );
    DROP POLICY source_insert ON sources;
    CREATE POLICY source_insert ON sources FOR INSERT TO campus_api WITH CHECK(
      campus_permitted(tenant_id,owner_id,department) AND uploaded_by=current_setting('app.user_id',true)
      AND approved_by IS NULL AND approved_at IS NULL AND published_by IS NULL AND published_at IS NULL
      AND review_note=''
      AND (
        (owner_id IS NULL AND publication_status='draft' AND campus_admin(tenant_id,department)
         AND campus_replacement_allowed(replaces_source_id,tenant_id,kind,department))
        OR (owner_id=current_setting('app.user_id',true) AND publication_status IN ('draft','published')
            AND replaces_source_id IS NULL AND kind IN ('emails','document','google_calendar'))
      )
    );
    """)
    for table in ("campus_records", "documents"):
        op.execute(f"DROP POLICY {table}_read ON {table}")
        # The old FOR ALL policy also applied to SELECT and could bypass a new
        # restrictive read policy. Replace it with command-specific policies.
        op.execute(f"DROP POLICY {table}_write ON {table}")
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT TO campus_api USING(campus_source_visible(source_id,tenant_id,owner_id,department))"
        )
        for command in ("INSERT", "UPDATE", "DELETE"):
            using = (
                ""
                if command == "INSERT"
                else "USING(campus_source_writable(source_id,tenant_id,owner_id,department))"
            )
            check = (
                ""
                if command == "DELETE"
                else "WITH CHECK(campus_source_writable(source_id,tenant_id,owner_id,department))"
            )
            op.execute(
                f"CREATE POLICY {table}_{command.lower()} ON {table} FOR {command} TO campus_api {using} {check}"
            )
    op.execute("DROP POLICY chunks_write ON document_chunks")
    for command in ("INSERT", "UPDATE", "DELETE"):
        predicate = """EXISTS(SELECT 1 FROM documents d WHERE d.id=document_id
            AND d.tenant_id=document_chunks.tenant_id
            AND campus_source_writable(d.source_id,d.tenant_id,d.owner_id,d.department))"""
        using = "" if command == "INSERT" else f"USING({predicate})"
        check = "" if command == "DELETE" else f"WITH CHECK({predicate})"
        op.execute(
            f"CREATE POLICY chunks_{command.lower()} ON document_chunks FOR {command} TO campus_api {using} {check}"
        )
    op.execute("""
    DROP POLICY ingestion_jobs_owner ON ingestion_jobs;
    CREATE POLICY ingestion_job_read ON ingestion_jobs FOR SELECT TO campus_api USING(
      tenant_id=current_setting('app.tenant_id',true) AND EXISTS(
        SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=ingestion_jobs.tenant_id
        AND ((s.owner_id=current_setting('app.user_id',true) AND requested_by=current_setting('app.user_id',true)
              AND campus_permitted(s.tenant_id,s.owner_id,s.department))
             OR (s.owner_id IS NULL AND campus_admin(s.tenant_id,s.department)
                 AND (requested_by=current_setting('app.user_id',true) OR s.kind='document' OR campus_top_admin(s.tenant_id))))
      )
    );
    CREATE POLICY ingestion_job_insert ON ingestion_jobs FOR INSERT TO campus_api WITH CHECK(
      requested_by=current_setting('app.user_id',true) AND status IN ('received','queued') AND EXISTS(
        SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=ingestion_jobs.tenant_id
        AND campus_source_writable(s.id,s.tenant_id,s.owner_id,s.department)
      )
    );
    CREATE POLICY ingestion_job_update ON ingestion_jobs FOR UPDATE TO campus_api USING(
      requested_by=current_setting('app.user_id',true) AND EXISTS(
        SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=ingestion_jobs.tenant_id
        AND campus_source_writable(s.id,s.tenant_id,s.owner_id,s.department)
      )
    ) WITH CHECK(requested_by=current_setting('app.user_id',true) AND tenant_id=current_setting('app.tenant_id',true));
    REVOKE INSERT,UPDATE,DELETE ON ingestion_jobs FROM campus_api;
    GRANT INSERT(id,tenant_id,source_id,requested_by,department,status,options,preview,error,attempts,claimed_at) ON ingestion_jobs TO campus_api;
    GRANT UPDATE(status,options,preview,error) ON ingestion_jobs TO campus_api;

    CREATE OR REPLACE FUNCTION public.campus_private_source_ready(sid text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    DECLARE s sources%ROWTYPE;
    BEGIN
      SELECT * INTO s FROM sources WHERE id=sid FOR UPDATE;
      IF NOT FOUND OR s.owner_id IS DISTINCT FROM current_setting('app.user_id',true)
         OR NOT campus_permitted(s.tenant_id,s.owner_id,s.department)
         OR s.publication_status!='draft' OR s.kind NOT IN ('emails','document') THEN
        RAISE EXCEPTION 'Only an owned private draft can become ready' USING ERRCODE='42501';
      END IF;
      UPDATE sources SET publication_status='published',published_by=s.owner_id,published_at=now() WHERE id=sid;
    END $$;
    REVOKE ALL ON FUNCTION public.campus_private_source_ready(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_private_source_ready(text) TO campus_api;

    CREATE OR REPLACE FUNCTION public.campus_source_transition(sid text,expected text,target text,note text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    DECLARE s sources%ROWTYPE; previous sources%ROWTYPE; who text;
    BEGIN
      who := current_setting('app.user_id',true);
      SELECT * INTO s FROM sources WHERE id=sid FOR UPDATE;
      IF NOT FOUND OR s.owner_id IS NOT NULL OR NOT campus_admin(s.tenant_id,s.department) THEN
        RAISE EXCEPTION 'Shared-source administrator permission required' USING ERRCODE='42501';
      END IF;
      IF s.publication_status!=expected OR NOT (
        (expected='pending_review' AND target='approved') OR
        (expected='approved' AND target='published') OR
        (expected IN ('pending_review','approved') AND target='rejected') OR
        (expected IN ('draft','pending_review','approved','published') AND target='revoked')
      ) OR length(note)>500 OR (target IN ('rejected','revoked') AND length(trim(note))=0) THEN
        RAISE EXCEPTION 'Invalid publication transition' USING ERRCODE='22023';
      END IF;
      -- Hold membership rows while validating the three participants so an
      -- in-flight revocation cannot race the publication transaction.
      PERFORM 1 FROM memberships WHERE tenant_id=s.tenant_id
        AND user_id IN (who,s.uploaded_by,s.approved_by) ORDER BY user_id FOR SHARE;
      IF NOT campus_admin(s.tenant_id,s.department) THEN
        RAISE EXCEPTION 'Publisher permissions were revoked' USING ERRCODE='42501';
      END IF;
      IF target IN ('approved','published') THEN
        IF NOT EXISTS(SELECT 1 FROM ingestion_jobs j WHERE j.source_id=sid AND j.tenant_id=s.tenant_id
                      AND j.status='completed' AND
                      (s.kind='document' OR json_array_length(j.staged_data->'records')>0)) THEN
          RAISE EXCEPTION 'Successful staged processing required' USING ERRCODE='22023';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM memberships m WHERE m.tenant_id=s.tenant_id AND m.user_id=s.uploaded_by
          AND m.active AND (m.role IN ('institution_admin','platform_admin') OR
                           (m.role='department_admin' AND m.department=s.department))) THEN
          RAISE EXCEPTION 'Uploader permissions were revoked' USING ERRCODE='42501';
        END IF;
      END IF;
      IF target='approved' AND who=s.uploaded_by THEN
        RAISE EXCEPTION 'Independent approval required' USING ERRCODE='42501';
      END IF;
      IF target='approved' AND s.kind!='document' AND NOT campus_top_admin(s.tenant_id) THEN
        RAISE EXCEPTION 'Institution administrator review required for unredacted structured imports' USING ERRCODE='42501';
      END IF;
      IF target='published' AND (s.approved_by IS NULL OR s.approved_by=s.uploaded_by OR NOT EXISTS(
        SELECT 1 FROM memberships m WHERE m.tenant_id=s.tenant_id AND m.user_id=s.approved_by AND m.active
        AND (m.role IN ('institution_admin','platform_admin') OR
             (s.kind='document' AND m.role='department_admin' AND m.department=s.department))
      )) THEN
        RAISE EXCEPTION 'Current independent approval required' USING ERRCODE='42501';
      END IF;
      IF target='published' AND s.replaces_source_id IS NOT NULL THEN
        SELECT * INTO previous FROM sources WHERE id=s.replaces_source_id FOR UPDATE;
        IF NOT FOUND OR previous.publication_status!='published' OR previous.owner_id IS NOT NULL
           OR previous.tenant_id!=s.tenant_id OR previous.kind!=s.kind OR previous.department!=s.department THEN
          RAISE EXCEPTION 'Replacement target changed' USING ERRCODE='22023';
        END IF;
      END IF;
      UPDATE sources SET publication_status=target,review_note=note,
        approved_by=CASE WHEN target='approved' THEN who ELSE approved_by END,
        approved_at=CASE WHEN target='approved' THEN now() ELSE approved_at END,
        published_by=CASE WHEN target='published' THEN who ELSE published_by END,
        published_at=CASE WHEN target='published' THEN now() ELSE published_at END WHERE id=sid;
      IF target IN ('rejected','revoked') THEN
        UPDATE ingestion_jobs SET status='cancelled' WHERE source_id=sid AND tenant_id=s.tenant_id
          AND status IN ('received','preview','queued','processing','failed');
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION public.campus_source_transition(text,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_source_transition(text,text,text,text) TO campus_api;

    CREATE OR REPLACE FUNCTION public.campus_finish_replacement(sid text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    DECLARE s sources%ROWTYPE; previous sources%ROWTYPE;
    BEGIN
      SELECT * INTO s FROM sources WHERE id=sid FOR UPDATE;
      IF NOT FOUND OR s.owner_id IS NOT NULL OR s.publication_status!='published'
         OR s.published_by IS DISTINCT FROM current_setting('app.user_id',true)
         OR NOT campus_admin(s.tenant_id,s.department) THEN
        RAISE EXCEPTION 'Authorized publication transaction required' USING ERRCODE='42501';
      END IF;
      SELECT * INTO previous FROM sources WHERE id=s.replaces_source_id FOR UPDATE;
      IF NOT FOUND OR previous.publication_status!='published' OR previous.owner_id IS NOT NULL
         OR previous.tenant_id!=s.tenant_id OR previous.kind!=s.kind OR previous.department!=s.department THEN
        RAISE EXCEPTION 'Replacement target changed' USING ERRCODE='22023';
      END IF;
      UPDATE sources SET publication_status='replaced' WHERE id=previous.id;
    END $$;
    REVOKE ALL ON FUNCTION public.campus_finish_replacement(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_finish_replacement(text) TO campus_api;
    """)


def downgrade():
    raise RuntimeError(
        "Publication controls cannot be safely removed automatically; restore a reviewed backup."
    )
