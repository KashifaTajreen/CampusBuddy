"""Offline adapter contracts; these do not claim to exercise PostgreSQL RLS."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from backend.app.section_onboarding import postgres_call


@pytest.mark.parametrize('state,status', [('42501', 403), ('22023', 400), ('42883', 503), ('40P01', 503)])
def test_database_errors_roll_back_and_do_not_expose_code_or_sql(state, status):
    class PrivateError(Exception):
        sqlstate = state
    session = Mock()
    session.scalar.side_effect = DBAPIError('private statement', {'code': 'PRIVATE-CODE'}, PrivateError('private detail'))
    with pytest.raises(HTTPException) as error:
        postgres_call(session, 'join', dict(code='PRIVATE-CODE'))
    assert error.value.status_code == status
    assert 'PRIVATE-CODE' not in error.value.detail and 'private statement' not in error.value.detail
    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_join_does_not_commit_an_unexpected_admin_result():
    session = Mock()
    session.scalar.return_value = dict(success=True, role='institution_admin')
    with pytest.raises(HTTPException) as error:
        postgres_call(session, 'join', {})
    assert error.value.status_code == 503
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
