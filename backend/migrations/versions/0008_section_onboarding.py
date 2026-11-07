"""Review draft: protected section-code enrollment and Supabase privilege boundaries."""

from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        sql = Path(__file__).resolve().parents[1] / 'sql/0008_section_onboarding.sql'
        op.execute(sql.read_text(encoding='utf-8'))
        return
    # Explicit additive SQLite schema; never rely on changed 0001 ORM metadata.
    if 'section_codes' not in sa.inspect(bind).get_table_names():
        op.create_table('section_codes',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('institutions.id'), nullable=False),
            sa.Column('department', sa.String(60), nullable=False),
            sa.Column('semester', sa.Integer(), nullable=False),
            sa.Column('section', sa.String(20), nullable=False),
            sa.Column('code_hash', sa.String(64), nullable=False),
            sa.Column('salt', sa.String(32), nullable=False),
            sa.Column('created_by', sa.String(36), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        for name, cols in [
            ('ix_section_codes_tenant_id', ['tenant_id']),
            ('ix_section_codes_department', ['department']),
            ('ix_section_codes_code_hash', ['code_hash']),
            ('ix_section_code_lookup', ['tenant_id','department','semester','section','revoked'])]:
            op.create_index(name, 'section_codes', cols)
    for event in ('INSERT', 'UPDATE'):
        op.execute(f"""CREATE TRIGGER campus_section_code_{event.lower()} BEFORE {event} ON section_codes
          WHEN NEW.semester NOT BETWEEN 1 AND 12 OR length(trim(NEW.department))=0
            OR lower(trim(NEW.department))='all' OR length(trim(NEW.section))=0
            OR lower(trim(NEW.section))='all' OR length(NEW.code_hash)!=64 OR length(NEW.salt)!=32
          BEGIN SELECT RAISE(ABORT,'Invalid section code scope or digest'); END""")


def downgrade():
    raise RuntimeError('Restore a reviewed backup; do not automatically remove enrollment security controls.')
