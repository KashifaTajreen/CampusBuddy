from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from backend.app.config import settings
from backend.app.models import Base


def make_engine(url):
    if url.startswith("sqlite"):
        Path("data").mkdir(exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_timeout=5, pool_recycle=1800)


engine = make_engine(settings().database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def initialize_local():
    if engine.dialect.name == "sqlite":
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if inspector.has_table("sources") and not {"semester", "section"} <= {c["name"] for c in inspector.get_columns("sources")}:
            raise RuntimeError("This database needs reviewed migration 0006. Back up and obtain approval before changing existing data.")
        if inspector.has_table("sources") and "student_records" not in {c["name"] for c in inspector.get_columns("sources")}:
            raise RuntimeError("This database needs reviewed migration 0007. Back up and obtain approval before changing existing data.")
        Base.metadata.create_all(engine)


def validate_production_role():
    if settings().app_env != "production":
        return
    with engine.connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ).one()
        if role.rolsuper or role.rolbypassrls:
            raise ValueError(
                "The API database login must be a restricted role without superuser or BYPASSRLS privileges."
            )
        owner = connection.execute(
            text(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='campus_records' AND tableowner=current_user"
            )
        ).first()
        if owner:
            raise ValueError("The API database login must not own the application tables.")


def set_identity(session, user_id, tenant_id):
    if session.bind.dialect.name == "postgresql":
        session.execute(
            text(
                "SELECT set_config('app.user_id',:user_id,true),set_config('app.tenant_id',:tenant_id,true)"
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )


def serialize_sqlite_writes(session):
    """SQLite has no SELECT FOR UPDATE: acquire its write reservation first."""
    if session.bind.dialect.name == "sqlite":
        connection = session.connection().connection.driver_connection
        if not connection.in_transaction:
            # Pending query traces must not autoflush an INSERT before BEGIN.
            with session.no_autoflush:
                session.execute(text("BEGIN IMMEDIATE"))
