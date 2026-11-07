"""Scoped administrator self-review and atomic draft-only deletion."""
from alembic import op
import sqlalchemy as sa

revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None

DEMO_SQL="""
CREATE FUNCTION public.campus_mark_draft_demo(sid text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public SET row_security=off AS $$
DECLARE s sources%ROWTYPE;
BEGIN
  SELECT * INTO s FROM sources WHERE id=sid FOR UPDATE;
  PERFORM 1 FROM memberships WHERE tenant_id=s.tenant_id AND user_id=current_setting('app.user_id',true) FOR SHARE;
  IF s.tenant_id IS DISTINCT FROM current_setting('app.tenant_id',true)
    OR NOT coalesce(campus_manage_source(sid),false) OR s.publication_status!='draft' OR s.file_state!='active' THEN
    RAISE EXCEPTION 'Scoped draft administrator required' USING ERRCODE='42501';
  END IF;
  UPDATE sources SET demo=true WHERE id=sid;
END $$;
REVOKE ALL ON FUNCTION public.campus_mark_draft_demo(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.campus_mark_draft_demo(text) FROM anon,authenticated;
GRANT EXECUTE ON FUNCTION public.campus_mark_draft_demo(text) TO campus_api;
"""


def reviewed_definitions(connection):
    definitions=[]
    for signature in ('campus_source_transition(text,text,text,text)','campus_file_action(text,text,text,text)'):
        definition=connection.scalar(sa.text('SELECT pg_get_functiondef(to_regprocedure(:name))'),{'name':signature})
        if not definition:
            raise RuntimeError('Expected reviewed security function is missing; migration stopped.')
        if signature.startswith('campus_source_transition'):
            changes={
                "IF target='approved' AND who=s.uploaded_by THEN\n        RAISE EXCEPTION 'Independent approval required' USING ERRCODE='42501';\n      END IF;":"",
                "IF target='approved' AND s.kind!='document' AND NOT campus_top_admin(s.tenant_id) THEN\n        RAISE EXCEPTION 'Institution administrator review required for unredacted structured imports' USING ERRCODE='42501';\n      END IF;":"",
                's.approved_by IS NULL OR s.approved_by=s.uploaded_by OR NOT EXISTS(':'s.approved_by IS NULL OR NOT EXISTS(',
                " AND (s.kind='document' OR m.role='institution_admin')":"",
                'Current independent approval required':'Current authorized approval required',
            }
        else:
            changes={
                "IF act='delete' AND s.file_state='active' THEN":"IF act IN ('delete','delete_draft') AND s.file_state='active'\n        AND (act!='delete_draft' OR (s.publication_status='draft' AND s.published_at IS NULL)) THEN",
                "IF act IN ('delete','begin_purge','finish_purge','rollback') THEN":"IF act IN ('delete','delete_draft','begin_purge','finish_purge','rollback') THEN",
            }
        for before,after in changes.items():
            if definition.count(before)!=1:
                raise RuntimeError('Security function differs from the reviewed baseline; migration stopped.')
            definition=definition.replace(before,after)
        definitions.append(definition)
    return definitions


def upgrade():
    connection=op.get_bind()
    if connection.dialect.name=='postgresql':
        for definition in reviewed_definitions(connection):
            op.execute(definition)
        op.execute(DEMO_SQL)


def downgrade():
    raise RuntimeError('Restore reviewed function definitions through an approved migration; no automatic security rollback.')
