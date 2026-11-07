"""Repair restricted API timestamp insertion and retained shared-record upserts.

0003 correctly protects worker staging but omitted created_at from its granular
INSERT grant. Its UPDATE predicate also prevents locking retained structured rows
after source revocation. Keep the deployed revision intact and preserve the new
source's writable-state check when reassigning such an administrator-visible row.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("GRANT INSERT(created_at) ON ingestion_jobs TO campus_api")
        op.execute("""
        DROP POLICY campus_records_update ON campus_records;
        CREATE POLICY campus_records_update ON campus_records FOR UPDATE TO campus_api
        USING(
          campus_source_writable(source_id,tenant_id,owner_id,department) OR (
            owner_id IS NULL AND campus_admin(tenant_id,department) AND EXISTS(
              SELECT 1 FROM sources s WHERE s.id=source_id AND s.tenant_id=campus_records.tenant_id
                AND s.owner_id IS NULL AND campus_admin(s.tenant_id,s.department)
            )
          )
        )
        WITH CHECK(campus_source_writable(source_id,tenant_id,owner_id,department));
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("REVOKE INSERT(created_at) ON ingestion_jobs FROM campus_api")
        op.execute("""
        DROP POLICY campus_records_update ON campus_records;
        CREATE POLICY campus_records_update ON campus_records FOR UPDATE TO campus_api
        USING(campus_source_writable(source_id,tenant_id,owner_id,department))
        WITH CHECK(campus_source_writable(source_id,tenant_id,owner_id,department));
        """)
