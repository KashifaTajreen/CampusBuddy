import os
from alembic import context
from sqlalchemy import create_engine, pool
from backend.app.config import settings
from backend.app.models import Base

config = context.config
# Set this when sharing a database with another Alembic project, so their histories stay separate.
VERSION_TABLE = os.getenv("ALEMBIC_VERSION_TABLE", "alembic_version")


def run_migrations_online():
    engine = create_engine(settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline():
    context.configure(
        url=settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
