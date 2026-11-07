"""Five campus roles and section-scoped institutional sources; preserve old data."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None
ROLES = "'student','institution_admin','department_admin','section_admin','canteen_manager'"


def upgrade():
    bind = op.get_bind()
    unknown = bind.scalar(sa.text(f"SELECT count(*) FROM memberships WHERE role NOT IN ({ROLES},'faculty','platform_admin')"))
    invalid = bind.scalar(sa.text("""SELECT count(*) FROM memberships WHERE
      (role IN ('department_admin','section_admin') AND (department='All' OR length(trim(department))=0))
      OR (role='section_admin' AND section='All')"""))
    if unknown or invalid:
        raise RuntimeError("Unknown roles or unbounded administrator scopes require operator review before migration.")
    for table in ("sources", "documents", "campus_records"):
        columns = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "semester" not in columns:
            op.add_column(table, sa.Column("semester", sa.Integer(), nullable=True))
        if "section" not in columns:
            op.add_column(table, sa.Column("section", sa.String(20), nullable=False, server_default="All"))
    op.execute("UPDATE memberships SET role='student' WHERE role='faculty'")
    op.execute("UPDATE memberships SET role='institution_admin' WHERE role='platform_admin'")
    if bind.dialect.name == "sqlite":
        op.execute("""UPDATE campus_records SET semester=json_extract(payload,'$.semester'),
          section=json_extract(payload,'$.section') WHERE kind='timetable' AND semester IS NULL
          AND json_extract(payload,'$.semester') IS NOT NULL AND json_extract(payload,'$.section') IS NOT NULL""")
        for action in ("INSERT", "UPDATE"):
            op.execute(f"""CREATE TRIGGER IF NOT EXISTS campus_membership_roles_{action.lower()}
              BEFORE {action} ON memberships WHEN NEW.role NOT IN ({ROLES}) OR
                (NEW.role IN ('department_admin','section_admin') AND (NEW.department='All' OR length(trim(NEW.department))=0))
                OR (NEW.role='section_admin' AND NEW.section='All')
              BEGIN SELECT RAISE(ABORT,'Unsupported campus role'); END""")
            for table in ("sources", "documents", "campus_records"):
                op.execute(f"""CREATE TRIGGER IF NOT EXISTS campus_scope_{table}_{action.lower()}
                  BEFORE {action} ON {table} WHEN NOT ((NEW.semester IS NULL AND NEW.section='All') OR
                    (NEW.semester IS NOT NULL AND NEW.semester BETWEEN 1 AND 12 AND NEW.section!='All' AND NEW.department!='All'))
                  BEGIN SELECT RAISE(ABORT,'Invalid source/record section scope'); END""")
        return
    op.execute("""UPDATE campus_records SET semester=(payload->>'semester')::integer,
      section=payload->>'section' WHERE kind='timetable' AND semester IS NULL
      AND payload->>'semester' IS NOT NULL AND payload->>'section' IS NOT NULL""")
    op.execute(f"ALTER TABLE memberships ADD CONSTRAINT ck_membership_roles CHECK(role IN ({ROLES}))")
    op.execute("ALTER TABLE memberships ADD CONSTRAINT ck_membership_admin_scope CHECK((role NOT IN ('department_admin','section_admin') OR (department!='All' AND length(trim(department))>0)) AND (role!='section_admin' OR section!='All'))")
    for table in ("sources", "documents", "campus_records"):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_section_scope CHECK((semester IS NULL AND section='All') OR (semester IS NOT NULL AND semester BETWEEN 1 AND 12 AND section!='All' AND department!='All'))")
    op.execute("""
    CREATE OR REPLACE FUNCTION public.campus_permitted(t text,o text DEFAULT NULL,d text DEFAULT 'All')
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND (o IS NULL OR o=current_setting('app.user_id',true))
      AND EXISTS(SELECT 1 FROM memberships m WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true)
        AND m.active AND (d='All' OR m.department=d OR m.role='institution_admin'))
    $$;
    CREATE OR REPLACE FUNCTION public.campus_admin(t text,d text DEFAULT 'All')
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND EXISTS(SELECT 1 FROM memberships m
        WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true) AND m.active
        AND (m.role='institution_admin' OR (m.role='department_admin' AND m.department=d)))
    $$;
    CREATE OR REPLACE FUNCTION public.campus_top_admin(t text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND EXISTS(SELECT 1 FROM memberships m
        WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true) AND m.active AND m.role='institution_admin')
    $$;
    CREATE FUNCTION public.campus_role_writes(r text,md text,ms integer,msec text,k text,d text,s integer,sec text)
    RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT r='institution_admin' OR
        (r='canteen_manager' AND k='menu' AND d='All' AND s IS NULL AND sec='All') OR
        (k!='menu' AND md=d AND (r='department_admin' OR
          (r='section_admin' AND ms=s AND msec=sec AND sec!='All')))
    $$;
    CREATE FUNCTION public.campus_scope_read(t text,o text,d text,s integer,sec text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT campus_permitted(t,o,d) AND EXISTS(SELECT 1 FROM memberships m
        WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true) AND m.active
        AND (m.role='institution_admin' OR (m.role='department_admin' AND m.department=d)
             OR (s IS NULL AND sec='All') OR (m.semester=s AND m.section=sec)))
    $$;
    CREATE FUNCTION public.campus_scope_write(t text,k text,d text,s integer,sec text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND EXISTS(SELECT 1 FROM memberships m
        WHERE m.tenant_id=t AND m.user_id=current_setting('app.user_id',true) AND m.active
        AND campus_role_writes(m.role,m.department,m.semester,m.section,k,d,s,sec))
    $$;
    CREATE FUNCTION public.campus_manage_source(sid text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM sources s WHERE s.id=sid
        AND campus_scope_write(s.tenant_id,s.kind,s.department,s.semester,s.section))
    $$;
    CREATE FUNCTION public.campus_canteen_shared(sid text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=current_setting('app.tenant_id',true)
        AND s.owner_id IS NULL AND s.kind='menu' AND s.department='All' AND s.semester IS NULL
        AND s.section='All' AND s.publication_status='published' AND s.file_state='active'
        AND campus_scope_write(s.tenant_id,s.kind,s.department,s.semester,s.section)
        AND NOT EXISTS(SELECT 1 FROM campus_records r WHERE r.source_id=s.id AND r.tenant_id=s.tenant_id
          AND (r.department!='All' OR r.semester IS NOT NULL)))
    $$;
    CREATE OR REPLACE FUNCTION public.campus_source_visible(sid text,t text,o text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT campus_permitted(t,o,d) AND EXISTS(SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t
        AND s.file_state='active' AND s.owner_id IS NOT DISTINCT FROM o
        AND campus_scope_read(t,s.owner_id,s.department,s.semester,s.section)
        AND (s.publication_status='published' OR (s.owner_id IS NULL AND campus_manage_source(sid))))
    $$;
    CREATE OR REPLACE FUNCTION public.campus_source_writable(sid text,t text,o text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT campus_permitted(t,o,d) AND EXISTS(SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t
        AND s.file_state='active' AND s.owner_id IS NOT DISTINCT FROM o
        AND s.publication_status IN ('draft','pending_review','approved','published')
        AND ((o=current_setting('app.user_id',true) AND campus_scope_read(t,o,s.department,s.semester,s.section))
          OR (o IS NULL AND campus_manage_source(sid))))
    $$;
    CREATE OR REPLACE FUNCTION public.campus_replacement_allowed(sid text,t text,k text,d text)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT sid IS NULL OR EXISTS(SELECT 1 FROM sources s WHERE s.id=sid AND s.tenant_id=t AND s.kind=k
        AND s.department=d AND s.owner_id IS NULL AND s.publication_status='published' AND campus_manage_source(sid))
    $$;
    DROP POLICY source_read ON sources;
    CREATE POLICY source_read ON sources FOR SELECT TO campus_api USING(
      campus_scope_read(tenant_id,owner_id,department,semester,section) AND
      (publication_status='published' OR owner_id=current_setting('app.user_id',true)
       OR (owner_id IS NULL AND campus_scope_write(tenant_id,kind,department,semester,section))));
    DROP POLICY source_insert ON sources;
    CREATE POLICY source_insert ON sources FOR INSERT TO campus_api WITH CHECK(
      uploaded_by=current_setting('app.user_id',true) AND approved_by IS NULL AND approved_at IS NULL
      AND published_by IS NULL AND published_at IS NULL AND review_note='' AND file_state='active'
      AND campus_scope_read(tenant_id,owner_id,department,semester,section)
      AND ((owner_id IS NULL AND publication_status='draft'
        AND campus_scope_write(tenant_id,kind,department,semester,section)
        AND campus_replacement_allowed(replaces_source_id,tenant_id,kind,department)) OR
      (owner_id=current_setting('app.user_id',true) AND kind IN ('document','emails','google_calendar')
        AND publication_status IN ('draft','published') AND replaces_source_id IS NULL)));
    DROP POLICY ingestion_job_read ON ingestion_jobs;
    CREATE POLICY ingestion_job_read ON ingestion_jobs FOR SELECT TO campus_api USING(
      EXISTS(SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=ingestion_jobs.tenant_id
        AND ((s.owner_id=current_setting('app.user_id',true) AND requested_by=current_setting('app.user_id',true))
          OR (s.owner_id IS NULL AND campus_manage_source(s.id)
            AND (s.kind='document' OR requested_by=current_setting('app.user_id',true) OR campus_top_admin(s.tenant_id))))));
    CREATE OR REPLACE FUNCTION public.campus_upsert_membership(t text,u text,r text,d text,s integer,sec text,n text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    BEGIN
      IF NOT campus_top_admin(t) OR u=current_setting('app.user_id',true)
        OR r NOT IN ('student','institution_admin','department_admin','section_admin','canteen_manager')
        OR s NOT BETWEEN 1 AND 12 OR length(sec)=0 THEN
        RAISE EXCEPTION 'College administrator and valid role/scope required' USING ERRCODE='42501';
      END IF;
      INSERT INTO memberships(id,tenant_id,user_id,role,department,semester,section,name,active,interests,diet)
      VALUES(gen_random_uuid()::text,t,u,r,d,s,sec,n,true,'','any')
      ON CONFLICT(tenant_id,user_id) DO UPDATE SET role=r,department=d,semester=s,section=sec,name=n,active=true;
    END $$;
    """)
    # Preserve the deployed transition/locking/purge logic, extending only its
    # permission predicates and replacement-scope equality. Fail if the reviewed
    # definitions no longer match instead of installing an uncertain rewrite.
    for signature in ("campus_source_transition(text,text,text,text)",
                      "campus_finish_replacement(text)", "campus_file_action(text,text,text,text)"):
        definition = bind.scalar(sa.text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": signature})
        assert "campus_admin(s.tenant_id,s.department)" in definition
        definition = definition.replace("campus_admin(s.tenant_id,s.department)", "campus_manage_source(s.id)")
        definition = definition.replace("previous.department!=s.department", "(previous.department!=s.department OR previous.semester IS DISTINCT FROM s.semester OR previous.section!=s.section)")
        if signature.startswith("campus_source_transition"):
            old = "m.role IN ('institution_admin','platform_admin') OR\n                           (m.role='department_admin' AND m.department=s.department)"
            reviewer = "m.role IN ('institution_admin','platform_admin') OR\n             (s.kind='document' AND m.role='department_admin' AND m.department=s.department)"
            assert old in definition and reviewer in definition
            member = "campus_role_writes(m.role,m.department,m.semester,m.section,s.kind,s.department,s.semester,s.section)"
            definition = definition.replace(old, member).replace(reviewer, member+" AND (s.kind='document' OR m.role='institution_admin')")
            definition = definition.replace("IF target='published' AND s.replaces_source_id IS NOT NULL THEN", """
      IF target='published' AND s.replaces_source_id IS NOT NULL AND EXISTS(
        SELECT 1 FROM memberships m WHERE m.tenant_id=s.tenant_id AND m.user_id=who AND m.role='canteen_manager')
        AND NOT campus_canteen_shared(s.replaces_source_id) THEN
        RAISE EXCEPTION 'College Admin must replace legacy restricted menu' USING ERRCODE='42501';
      END IF;
      IF target='published' AND s.replaces_source_id IS NOT NULL THEN""")
            definition = definition.replace("UPDATE sources SET publication_status=target,review_note=note,", """
      IF target IN ('approved','published') AND EXISTS(
        SELECT 1 FROM sources other WHERE other.tenant_id=s.tenant_id AND other.owner_id IS NULL
          AND other.id!=s.id AND other.id IS DISTINCT FROM s.replaces_source_id AND other.kind=s.kind
          AND other.checksum=s.checksum AND other.file_state='active' AND other.publication_status='published'
          AND campus_scope_read(other.tenant_id,NULL,other.department,other.semester,other.section)) THEN
        RAISE EXCEPTION 'Identical published source already exists' USING ERRCODE='22023';
      END IF;
      UPDATE sources SET publication_status=target,review_note=note,""")
        op.execute(definition)
    for table in ("campus_records", "documents"):
        op.execute(f"DROP POLICY {table}_read ON {table}")
        op.execute(f"CREATE POLICY {table}_read ON {table} FOR SELECT TO campus_api USING(campus_source_visible(source_id,tenant_id,owner_id,department) AND campus_scope_read(tenant_id,owner_id,department,semester,section))")
        for command in ("insert", "update", "delete"):
            op.execute(f"DROP POLICY {table}_{command} ON {table}")
            predicate = """campus_source_writable(source_id,tenant_id,owner_id,department) AND EXISTS(
              SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=tenant_id
              AND (s.department='All' OR s.department=department)
              AND (s.semester IS NULL OR (s.semester=semester AND s.section=section))
              AND (owner_id=current_setting('app.user_id',true) OR campus_scope_write(tenant_id,s.kind,department,semester,section)))"""
            # Qualify every row reference: unqualified inner names would compare
            # source columns to themselves and defeat the parent-scope check.
            for field in ("source_id", "tenant_id", "owner_id", "department", "semester", "section"):
                import re
                predicate = re.sub(r"(?<![\w.])"+field+r"\b", table+"."+field, predicate)
            predicate = predicate[:-1]+(" AND (s.kind=campus_records.kind OR (s.kind='google_calendar' AND campus_records.kind='events' AND campus_records.owner_id=current_setting('app.user_id',true))))" if table == "campus_records" else " AND s.kind='document')")
            if table == "documents":
                predicate = predicate[:-1]+" AND s.department=documents.department AND s.semester IS NOT DISTINCT FROM documents.semester AND s.section=documents.section)"
            retained = f"({table}.owner_id IS NULL AND campus_manage_source({table}.source_id) AND campus_scope_read({table}.tenant_id,NULL,{table}.department,{table}.semester,{table}.section))"
            using = "" if command == "insert" else f"USING({predicate}{' OR '+retained if command == 'update' else ''})"
            check = "" if command == "delete" else f"WITH CHECK({predicate})"
            op.execute(f"CREATE POLICY {table}_{command} ON {table} FOR {command.upper()} TO campus_api {using} {check}")
    op.execute("""
    CREATE FUNCTION public.campus_record_replacement_guard() RETURNS trigger LANGUAGE plpgsql
    SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    BEGIN
      IF NEW.source_id!=OLD.source_id AND EXISTS(SELECT 1 FROM sources WHERE id=OLD.source_id AND publication_status='published')
        AND NOT EXISTS(SELECT 1 FROM sources WHERE id=NEW.source_id AND replaces_source_id=OLD.source_id) THEN
        RAISE EXCEPTION 'Explicit replacement required for published records' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION public.campus_record_replacement_guard() FROM PUBLIC;
    CREATE TRIGGER campus_record_replacement BEFORE UPDATE ON campus_records
      FOR EACH ROW EXECUTE FUNCTION campus_record_replacement_guard();
    """)
    for signature in ("campus_role_writes(text,text,integer,text,text,text,integer,text)",
                      "campus_scope_read(text,text,text,integer,text)", "campus_scope_write(text,text,text,integer,text)",
                      "campus_manage_source(text)"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO campus_api")
    op.execute("REVOKE ALL ON FUNCTION public.campus_canteen_shared(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.campus_canteen_shared(text) TO campus_api")


def downgrade():
    raise RuntimeError("Section permissions must not be removed automatically. Restore a reviewed backup.")
