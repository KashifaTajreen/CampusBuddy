# Imported pytest fixtures intentionally share names with fixture dependencies.
# ruff: noqa: F811
from types import SimpleNamespace
import pytest
from backend.app.auth import verify_token, hash_section_code
from backend.app import auth
from backend.app.models import SectionCode
from backend.app.router import Router
from backend.tests.test_auth_api import token, key, client  # noqa: F401


@pytest.fixture(autouse=True)
def offline_google_identity(monkeypatch):
    # Section lifecycle tests isolate the remote identity boundary. The dedicated
    # test_google_identity module verifies the complete provider-response gate.
    monkeypatch.setattr(auth, "verify_google_identity", lambda *args: None)


def test_google_domain_restriction(key, monkeypatch):
    monkeypatch.setattr(auth, "settings", lambda: SimpleNamespace(supabase_url="https://auth.example.test", app_env="production"))
    monkeypatch.setattr(
        auth,
        "jwks_client",
        lambda _: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())),
    )
    # Valid RVCE Google identity (valid UUID sub)
    valid_uuid = "00000000-0000-0000-0000-000000000123"
    valid_jwt = token(key, valid_uuid, email="student@rvce.edu.in", app_metadata={"provider": "google"})
    assert verify_token(valid_jwt) == valid_uuid

    # Invalid non-RVCE Google identity
    intruder_uuid = "00000000-0000-0000-0000-000000000999"
    invalid_jwt = token(key, intruder_uuid, email="intruder@gmail.com", app_metadata={"provider": "google"})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        verify_token(invalid_jwt)
    assert exc.value.status_code == 403
    assert "@rvce.edu.in" in exc.value.detail


def test_section_code_generation_rotation_and_join(client, env):
    # 1. Admin generates section code for AIML semester 3 section A
    res = client.post("/v1/admin/section-codes", json={
        "department": "AIML",
        "semester": 3,
        "section": "A",
        "expiry_days": 7
    })
    assert res.status_code == 200, res.text
    data = res.json()
    raw_code = data["code"]
    assert "AIML3A" in raw_code
    code_id = data["id"]

    # Verify code is hashed at rest in DB, NOT stored plaintext
    with env.factory() as db:
        stored = db.get(SectionCode, code_id)
        assert stored is not None
        assert stored.code_hash != raw_code
        assert stored.code_hash == hash_section_code(raw_code, stored.salt)
        assert not stored.revoked

    # 2. Admin rotates code (generates a new one for same section)
    res2 = client.post("/v1/admin/section-codes", json={
        "department": "AIML",
        "semester": 3,
        "section": "A",
        "expiry_days": 7
    })
    assert res2.status_code == 200
    new_raw_code = res2.json()["code"]
    assert new_raw_code != raw_code

    # Old code must be revoked
    with env.factory() as db:
        old_stored = db.get(SectionCode, code_id)
        assert old_stored.revoked is True


def test_join_section_lifecycle(client, env, key):
    # 1. Admin creates section code
    gen_res = client.post("/v1/admin/section-codes", json={
        "department": "AIML",
        "semester": 3,
        "section": "B",
        "expiry_days": 14
    })
    assert gen_res.status_code == 200
    valid_code = gen_res.json()["code"]

    student_user_id = "00000000-0000-0000-0000-000000000888"
    student_tok = token(key, student_user_id, email="newstudent@rvce.edu.in", app_metadata={"provider": "google"})

    # 2. Student attempts with wrong code -> 400
    bad_res = client.post(
        "/v1/onboarding/join-section",
        json={"department": "AIML", "semester": 3, "section": "B", "code": "WRONG-CODE"},
        headers={"Authorization": f"Bearer {student_tok}", "X-Tenant-ID": env.tenant}
    )
    assert bad_res.status_code == 400

    # 3. Student uses valid code -> 200
    join_res = client.post(
        "/v1/onboarding/join-section",
        json={"department": "AIML", "semester": 3, "section": "B", "code": valid_code},
        headers={"Authorization": f"Bearer {student_tok}", "X-Tenant-ID": env.tenant}
    )
    assert join_res.status_code == 200
    assert join_res.json()["role"] == "student"
    assert join_res.json()["department"] == "AIML"
    assert join_res.json()["section"] == "B"

    # 4. Membership saved so user can now call /v1/me and needs_onboarding is False
    me_res = client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {student_tok}", "X-Tenant-ID": env.tenant}
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["role"] == "student"
    assert me_data["section"] == "B"
    assert me_data["needs_onboarding"] is False


def test_kannada_events_routing(client, env):
    # Test router directly for route and kind
    router = Router(env.service)
    direct_res = router.ask("ಇಂದು ಯಾವ ಕಾರ್ಯಕ್ರಮಗಳಿವೆ?", language="kn")
    assert direct_res["route"] == "Published events SQL"
    assert direct_res["kind"] == "events"
    assert "ಕಾರ್ಯಕ್ರಮಗಳು" in direct_res["answer"]

    # Test via chat API
    res = client.post("/v1/chat", json={
        "question": "ಇಂದು ಯಾವ ಕಾರ್ಯಕ್ರಮಗಳಿವೆ?",
        "language": "kn"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "events"
    assert "ಕಾರ್ಯಕ್ರಮಗಳು" in data["answer"]


def test_join_cannot_self_assign_admin(client, env, key):
    signed = token(key, "00000000-0000-4000-8000-000000000888",
                   email="newstudent@rvce.edu.in", app_metadata={"provider": "google"})
    response = client.post("/v1/onboarding/join-section", headers={"Authorization": "Bearer " + signed},
                           json=dict(department="AIML", semester=3, section="A", code="FAKE-CODE",
                                     role="institution_admin"))
    assert response.status_code == 422


def test_join_cannot_reactivate_revoked_membership(client, env, key):
    from backend.app.models import Membership
    from sqlalchemy import select
    with env.factory() as db:
        member = db.scalar(select(Membership).where(Membership.tenant_id == env.tenant,
                                                   Membership.user_id == env.other))
        member.active = False
        db.commit()
    signed = token(key, env.other, email="student@rvce.edu.in", app_metadata={"provider": "google"})
    response = client.post("/v1/onboarding/join-section", headers={"Authorization": "Bearer " + signed},
                           json=dict(department="AIML", semester=3, section="A", code="FAKE-CODE"))
    assert response.status_code == 403
    with env.factory() as db:
        member = db.scalar(select(Membership).where(Membership.tenant_id == env.tenant,
                                                   Membership.user_id == env.other))
        assert member.active is False
        assert member.department == "CSE"


def test_join_cannot_demote_existing_admin(client, env, key):
    signed = token(key, env.user, email="admin@rvce.edu.in", app_metadata={"provider": "google"})
    response = client.post("/v1/onboarding/join-section", headers={"Authorization": "Bearer " + signed},
                           json=dict(department="AIML", semester=3, section="A", code="FAKE-CODE"))
    assert response.status_code == 403


def test_failed_join_attempts_are_rate_limited(client, env, key):
    signed = token(key, "00000000-0000-4000-8000-000000000888",
                   email="student@rvce.edu.in", app_metadata={"provider": "google"})
    for _ in range(10):
        response = client.post("/v1/onboarding/join-section", headers={"Authorization": "Bearer " + signed},
                               json=dict(department="AIML", semester=3, section="A", code="FAKE-CODE"))
        assert response.status_code == 400
    response = client.post("/v1/onboarding/join-section", headers={"Authorization": "Bearer " + signed},
                           json=dict(department="AIML", semester=3, section="A", code="FAKE-CODE"))
    assert response.status_code == 429


def test_section_codes_use_long_random_tokens_and_metadata_never_exposes_secrets(client, env):
    result = client.post('/v1/admin/section-codes', json=dict(department='AIML', semester=3, section='A'))
    assert result.status_code == 200
    code = result.json()['code']
    assert len(code.rsplit('-', 1)[1]) == 16
    listed = client.get('/v1/admin/section-codes')
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r['id'] == result.json()['id'])
    assert row['is_active'] is True
    assert not {'code', 'code_hash', 'salt'} & row.keys()
    assert code not in listed.text


@pytest.mark.parametrize('field,value', [('department', 'All'), ('section', 'all'), ('section', '   ')])
def test_section_codes_cannot_create_broadcast_or_empty_scope(client, field, value):
    body = dict(department='AIML', semester=3, section='A')
    body[field] = value
    assert client.post('/v1/admin/section-codes', json=body).status_code == 422


def test_revoking_creator_membership_invalidates_unexpired_code(client, env, key):
    from backend.app.models import Membership
    from sqlalchemy import select
    result = client.post('/v1/admin/section-codes', json=dict(department='AIML', semester=3, section='A'))
    assert result.status_code == 200
    with env.factory() as db:
        creator = db.scalar(select(Membership).where(Membership.tenant_id == env.tenant,
                                                     Membership.user_id == env.user))
        creator.active = False
        db.commit()
    who = '00000000-0000-4000-8000-000000000888'
    signed = token(key, who, email='student@rvce.edu.in', app_metadata={'provider': 'google'})
    response = client.post('/v1/onboarding/join-section', headers={'Authorization': 'Bearer ' + signed},
        json=dict(department='AIML', semester=3, section='A', code=result.json()['code']))
    assert response.status_code == 400
    with env.factory() as db:
        assert db.scalar(select(Membership).where(Membership.tenant_id == env.tenant, Membership.user_id == who)) is None


def test_canteen_manager_cannot_list_section_codes(client, env):
    from backend.app.models import Membership
    from sqlalchemy import select
    with env.factory() as db:
        member = db.scalar(select(Membership).where(Membership.tenant_id == env.tenant,
                                                    Membership.user_id == env.user))
        member.role = 'canteen_manager'
        db.commit()
    assert client.get('/v1/admin/section-codes').status_code == 403
