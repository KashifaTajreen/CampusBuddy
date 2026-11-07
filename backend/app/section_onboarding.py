"""Restricted PostgreSQL enrollment calls and SQLite authority revalidation."""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.auth import resolve_actor
from backend.app.db import serialize_sqlite_writes

STATEMENTS = {
    'join': 'SELECT public.campus_section_join(:tenant,:department,:semester,:section,:code)',
    'create': 'SELECT public.campus_section_create(:tenant,:department,:semester,:section,:hash,:salt,:days)',
    'revoke': 'SELECT public.campus_section_revoke(:tenant,:code_id)',
}


def postgres_call(db, operation, parameters):
    """Only fixed statements; never grant direct membership writes to the API."""
    try:
        result = db.scalar(text(STATEMENTS[operation]), parameters)
        if not isinstance(result, dict):
            raise HTTPException(503, 'Section enrollment requires the reviewed database migration.')
        if operation == 'join' and (result.get('role') != 'student' or result.get('success') is not True):
            db.rollback()
            raise HTTPException(503, 'Unexpected enrollment result; no membership change was committed.')
        db.commit()
        return result
    except DBAPIError as exc:
        state = getattr(exc.orig, 'sqlstate', None)
        db.rollback()
        if state == '42501':
            raise HTTPException(403, 'Current institution or section permission is required.') from None
        if state == '22023':
            raise HTTPException(400, 'Invalid or expired section join code or section details.') from None
        raise HTTPException(503, 'Section enrollment is unavailable. Ask the administrator to verify the reviewed migration.') from None


def sqlite_admin(service, department, semester, section):
    serialize_sqlite_writes(service.db)
    current = resolve_actor(service.db, service.actor.user_id, service.actor.tenant_id)
    current.check_department_write(department, kind='section_code', semester=semester, section=section)
