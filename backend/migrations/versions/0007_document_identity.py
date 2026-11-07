"""Verified student identifiers and row-level institutional document evidence."""
import re
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, column in (
        ("memberships",sa.Column("student_number",sa.String(20),nullable=True)),
        ("sources",sa.Column("student_records",sa.Boolean(),nullable=False,server_default=sa.false())),
        ("document_chunks",sa.Column("student_numbers",sa.JSON(),nullable=False,server_default="[]")),
        ("document_chunks",sa.Column("row_data",sa.JSON(),nullable=False,server_default="{}")),
    ):
        if column.name not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table,column)
    if "uq_membership_student_number" not in {i["name"] for i in sa.inspect(bind).get_indexes("memberships")}:
        op.create_index("uq_membership_student_number","memberships",["tenant_id","student_number"],unique=True)
    # Old spreadsheet chunks have no trustworthy row boundaries. Quarantine them
    # from student reads until a fresh row-aware version receives independent review.
    ids = {r[0] for r in bind.execute(sa.text("SELECT id FROM sources WHERE kind='document' AND owner_id IS NULL AND (lower(filename) LIKE '%.xlsx' OR lower(filename) LIKE '%.csv')"))}
    pattern = re.compile(r"(?<![a-z0-9])[1-9][a-z]{2}\d{2}[a-z]{2}\d{3}(?![a-z0-9])",re.I)
    for sid, text in bind.execute(sa.text("SELECT s.id,c.text FROM sources s JOIN documents d ON d.source_id=s.id JOIN document_chunks c ON c.document_id=d.id WHERE s.kind='document' AND s.owner_id IS NULL")):
        if pattern.search(text):
            ids.add(sid)
    for sid in ids:
        bind.execute(sa.text("UPDATE sources SET student_records=:yes WHERE id=:sid"),dict(yes=True,sid=sid))
    if bind.dialect.name != "postgresql":
        for event in ("INSERT","UPDATE"):
            op.execute(f"""CREATE TRIGGER campus_student_number_{event.lower()} BEFORE {event} ON memberships
              WHEN NEW.student_number IS NOT NULL AND NEW.student_number NOT GLOB
              '[1-9][A-Z][A-Z][0-9][0-9][A-Z][A-Z][0-9][0-9][0-9]'
              BEGIN SELECT RAISE(ABORT,'Invalid verified student number'); END""")
        return
    op.execute("ALTER TABLE memberships ADD CONSTRAINT ck_student_number CHECK(student_number IS NULL OR student_number ~ '^[1-9][A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}$')")
    op.execute("""
    CREATE FUNCTION public.campus_student_chunk(t text,doc text,nums json)
    RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public SET row_security=off AS $$
      SELECT t=current_setting('app.tenant_id',true) AND EXISTS(
        SELECT 1 FROM documents d JOIN sources s ON s.id=d.source_id AND s.tenant_id=d.tenant_id
        JOIN memberships m ON m.tenant_id=t AND m.user_id=current_setting('app.user_id',true) AND m.active
        WHERE d.id=doc AND d.tenant_id=t AND
          (NOT s.student_records OR s.owner_id=m.user_id OR
           m.role IN ('institution_admin','department_admin','section_admin') OR
           (m.student_number IS NOT NULL AND nums::jsonb ? m.student_number)))
    $$;
    REVOKE ALL ON FUNCTION public.campus_student_chunk(text,text,json) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_student_chunk(text,text,json) TO campus_api;
    DROP POLICY chunks_read ON document_chunks;
    CREATE POLICY chunks_read ON document_chunks FOR SELECT TO campus_api USING(
      tenant_id=current_setting('app.tenant_id',true) AND
      EXISTS(SELECT 1 FROM documents d WHERE d.id=document_id AND d.tenant_id=document_chunks.tenant_id) AND
      campus_student_chunk(tenant_id,document_id,student_numbers));
    CREATE INDEX ix_chunks_student_numbers ON document_chunks USING gin((student_numbers::jsonb));
    CREATE FUNCTION public.campus_set_student_number(t text,u text,n text)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
    BEGIN
      IF NOT campus_top_admin(t) OR u=current_setting('app.user_id',true) THEN
        RAISE EXCEPTION 'College Admin permission required' USING ERRCODE='42501';
      END IF;
      IF n IS NOT NULL AND n !~ '^[1-9][A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}$' THEN
        RAISE EXCEPTION 'Invalid verified student number' USING ERRCODE='22023';
      END IF;
      UPDATE memberships SET student_number=n WHERE tenant_id=t AND user_id=u;
      IF NOT FOUND THEN RAISE EXCEPTION 'Membership missing' USING ERRCODE='22023'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION public.campus_set_student_number(text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.campus_set_student_number(text,text,text) TO campus_api;
    """)


def downgrade():
    raise RuntimeError("Restore a reviewed backup to remove student record access controls.")
