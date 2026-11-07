"""Google verification uses only mocked Auth responses and synthetic signing keys."""
# Imported pytest fixtures intentionally share names with fixture dependencies.
# ruff: noqa: F811

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend.app import auth
from backend.tests.test_auth_api import key, token  # noqa: F401

SUBJECT = "00000000-0000-4000-8000-000000000888"
EMAIL = "student@rvce.edu.in"


@pytest.fixture
def identity(key, monkeypatch):
    state = dict(status=200, user=dict(
        id=SUBJECT, email=EMAIL, email_confirmed_at="2026-11-05T00:00:00Z",
        identities=[dict(provider="google", identity_data=dict(email=EMAIL, email_verified=True))]))
    monkeypatch.setattr(auth, "settings", lambda: SimpleNamespace(
        supabase_url="https://auth.example.test", supabase_publishable_key="test-only-public-key"))
    monkeypatch.setattr(auth, "jwks_client", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    original = httpx.Client

    def handler(request):
        assert request.url == "https://auth.example.test/auth/v1/user"
        assert request.headers["apikey"] == "test-only-public-key"
        assert request.headers["authorization"].startswith("Bearer ")
        state["called"] = True
        return httpx.Response(state["status"], json=state["user"])

    monkeypatch.setattr(auth.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    signed = token(key, SUBJECT, email=EMAIL, app_metadata={"provider": "google"})
    return state, signed


def test_verified_google_identity_is_accepted(identity):
    state, signed = identity
    assert auth.verify_token(signed) == SUBJECT
    assert state["called"]


@pytest.mark.parametrize("field,value", [("id", "different-user"), ("email", "other@rvce.edu.in"),
                                         ("email_confirmed_at", None), ("identities", []),
                                         ("identities", 123)])
def test_incomplete_or_mismatched_identity_is_rejected(identity, field, value):
    state, signed = identity
    state["user"][field] = value
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403


def test_editable_metadata_cannot_prove_email_verification(identity):
    state, signed = identity
    state["user"]["identities"][0]["identity_data"]["email_verified"] = False
    state["user"]["user_metadata"] = dict(email_verified=True, role="institution_admin")
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 401), (429, 503), (500, 503)])
def test_auth_service_errors_fail_closed(identity, status, expected):
    state, signed = identity
    state["status"] = status
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == expected


def test_non_institution_google_email_is_denied_before_auth_request(identity, key):
    state, _ = identity
    signed = token(key, SUBJECT, email="intruder@gmail.com", app_metadata={"provider": "google"})
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403
    assert not state.get("called")


def test_onboarding_does_not_accept_unlinked_password_identity(identity, key):
    state, _ = identity
    state["user"]["identities"][0]["provider"] = "email"
    signed = token(key, SUBJECT, email=EMAIL, app_metadata={"provider": "email"})
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed, require_google=True)
    assert exc.value.status_code == 403


def test_linked_google_identity_cannot_skip_the_domain_check(identity, key):
    state, _ = identity
    signed = token(key, SUBJECT, email="intruder@gmail.com",
                   app_metadata={"provider": "email", "providers": ["email", "google"]})
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403
    assert not state.get("called")


def test_google_identity_requires_valid_jwt_signature_before_remote_lookup(identity):
    state, signed = identity
    header, payload, _ = signed.split(".")
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(header + "." + payload + ".Zm9yZ2Vk")
    assert exc.value.status_code == 401
    assert not state.get("called")


@pytest.fixture
def gmail_admin(identity, key, monkeypatch):
    state, _ = identity
    email = "approved-admin@gmail.com"
    state["user"]["email"] = email
    state["user"]["identities"][0]["identity_data"]["email"] = email
    config = auth.settings()
    config.google_admin_email = email
    config.google_admin_user_id = SUBJECT
    monkeypatch.setattr(auth, "settings", lambda: config)
    monkeypatch.setattr(auth, "active_google_admin", lambda subject: subject == SUBJECT)
    return state, token(key, SUBJECT, email=email, app_metadata={"provider": "google"}), config


def test_exact_verified_pinned_gmail_admin_is_accepted(gmail_admin):
    state, signed, _ = gmail_admin
    assert auth.verify_token(signed) == SUBJECT
    assert state["called"]


@pytest.mark.parametrize("field,value", [("google_admin_email", "other@gmail.com"),
                                        ("google_admin_user_id", "different-subject"),
                                        ("google_admin_user_id", "")])
def test_gmail_admin_requires_both_configured_pins(gmail_admin, field, value):
    state, signed, config = gmail_admin
    setattr(config, field, value)
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403
    assert not state.get("called")


def test_revoked_gmail_admin_is_rechecked_each_request(gmail_admin, monkeypatch):
    _, signed, _ = gmail_admin
    assert auth.verify_token(signed) == SUBJECT
    monkeypatch.setattr(auth, "active_google_admin", lambda subject: False)
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403


def test_gmail_admin_cannot_use_student_onboarding(gmail_admin):
    state, signed, _ = gmail_admin
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed, require_google=True)
    assert exc.value.status_code == 403
    assert not state.get("called")


def test_pinned_gmail_requires_provider_owned_google_identity(gmail_admin, key):
    state, _, config = gmail_admin
    state["user"]["identities"][0]["provider"] = "email"
    signed = token(key, SUBJECT, email=config.google_admin_email, app_metadata={"provider": "email"})
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code == 403


def test_active_google_admin_checks_database_role_and_revocation(env, monkeypatch):
    monkeypatch.setattr(auth, "SessionLocal", env.factory)
    assert auth.active_google_admin(env.user)
    for member in env.db.scalars(auth.select(auth.Membership).where(
            auth.Membership.user_id == env.other)):
        member.role = "student"
    env.db.commit()
    assert not auth.active_google_admin(env.other)
    member = env.db.scalar(auth.select(auth.Membership).where(
        auth.Membership.user_id == env.user, auth.Membership.tenant_id == env.tenant))
    member.active = False
    env.db.commit()
    assert not auth.active_google_admin(env.user)
