from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID
import time
import re
import jwt
import httpx
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, case
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import SessionLocal, set_identity
from backend.app.models import Membership, RateBucket

bearer = HTTPBearer(auto_error=False)
ADMIN_ROLES = {"department_admin", "institution_admin", "section_admin", "canteen_manager"}
TOP_ROLES = {"institution_admin"}  # Displayed as College Admin.
ROLES = ADMIN_ROLES | {"student"}


@lru_cache(maxsize=8)
def jwks_client(url):
    # Cache the JWKS for five minutes, not individual keys indefinitely. Otherwise
    # a key removed during rotation/revocation can bypass the JWKS cache expiry.
    return jwt.PyJWKClient(url, cache_keys=False, lifespan=300, timeout=5)


def verify_google_identity(token, subject, email, config):
    """Check provider-owned identity data, never editable user_metadata."""
    key = getattr(config, "supabase_publishable_key", "")
    if not key:
        raise HTTPException(503, "Google identity verification is not configured.")
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = client.get(config.supabase_url.rstrip("/") + "/auth/v1/user",
                                  headers={"apikey": key, "Authorization": "Bearer " + token})
        if response.status_code in (401, 403):
            raise HTTPException(401, "Your session is invalid or expired. Sign in again.")
        if response.status_code != 200:
            raise HTTPException(503, "Google identity verification is temporarily unavailable.")
        user = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Google identity verification is temporarily unavailable.") from exc
    if not isinstance(user, dict):
        raise HTTPException(503, "Google identity verification is temporarily unavailable.")
    identities = user.get("identities")
    if not isinstance(identities, list):
        identities = []
    verified = any(
        isinstance(identity, dict) and identity.get("provider") == "google"
        and isinstance(identity.get("identity_data"), dict)
        and identity["identity_data"].get("email_verified") is True
        and str(identity["identity_data"].get("email", "")).lower() == email
        for identity in identities
    )
    if (user.get("id") != subject or not user.get("email_confirmed_at")
            or str(user.get("email", "")).lower() != email or not verified):
        raise HTTPException(403, "Use a verified, authorized Google identity.")


def active_google_admin(subject):
    """Recheck membership for every exception; JWT metadata cannot grant roles."""
    with SessionLocal() as session:
        set_identity(session, subject, "")
        return session.scalar(select(Membership.id).where(
            Membership.user_id == subject,
            Membership.active.is_(True),
            Membership.role == "institution_admin",
        ).limit(1)) is not None


def configured_test_student(config=None):
    """Return one exact operator pin; no email-only or first-login enrollment."""
    config=config or settings()
    email=getattr(config,'google_test_student_email','').strip().lower()
    subject=getattr(config,'google_test_student_user_id','').strip()
    tenant=getattr(config,'google_test_student_tenant_id','').strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._%+-]*@gmail\.com',email):
        return None
    try:
        subject,tenant=str(UUID(subject)),str(UUID(tenant))
    except (ValueError,TypeError,AttributeError):
        return None
    if (email==getattr(config,'google_admin_email','').strip().lower()
            or subject==getattr(config,'google_admin_user_id','').strip()):
        return None  # Never reinterpret the existing administrator as a student.
    return email,subject,tenant


def restrict_test_student(user_id,tenant_id,role='student',config=None):
    pin=configured_test_student(config)
    if pin and user_id==pin[1] and (tenant_id!=pin[2] or role!='student'):
        raise HTTPException(403,'The temporary test identity is restricted to Student access in its configured institution.')


def verify_test_student_memberships(subject,config):
    pin=configured_test_student(config)
    if not pin or subject!=pin[1]:
        raise HTTPException(403,'The temporary test student identity has not been verified and pinned.')
    with SessionLocal() as session:
        set_identity(session,subject,pin[2])
        memberships=session.scalars(select(Membership).where(Membership.user_id==subject)).all()
        for membership in memberships:
            restrict_test_student(subject,membership.tenant_id,membership.role,config)
            if not membership.active:
                raise HTTPException(403,'This test student membership has been revoked. Contact the administrator.')
        # No membership is acceptable only so the normal join-code onboarding
        # screen can be reached; this function creates no membership or scope.


def verify_token(token, require_google=False):
    config = settings()
    if not config.supabase_url:
        raise HTTPException(503, "Authentication is not configured. Follow the Supabase setup guide.")
    issuer = config.supabase_url.rstrip("/") + "/auth/v1"
    try:
        key = jwks_client(issuer + "/.well-known/jwks.json").get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        UUID(claims["sub"])
        metadata = claims.get("app_metadata") or {}
        provider = metadata.get("provider")
        providers = metadata.get("providers") or []
        email = (claims.get("email") or "").lower()
        configured_admin = getattr(config, "google_admin_email", "").strip().lower()
        pinned_subject = getattr(config, "google_admin_user_id", "")
        student_email=getattr(config,'google_test_student_email','').strip().lower()
        student_subject=getattr(config,'google_test_student_user_id','').strip()
        if (provider == "google" or "google" in providers or require_google
                or (configured_admin and email == configured_admin)
                or (pinned_subject and claims["sub"] == pinned_subject)
                or (student_email and email==student_email)
                or (student_subject and claims['sub']==student_subject)):
            institution_identity = email.endswith("@rvce.edu.in")
            pinned_admin = (
                not require_google and email.endswith("@gmail.com")
                and email == configured_admin
                and claims["sub"] == pinned_subject
            )
            student_pin=configured_test_student(config)
            pinned_student=bool(getattr(config,'google_test_student_enabled',False)
                and getattr(config,'app_env','production') in ('development','test')
                and student_pin and (email,claims['sub'])==student_pin[:2]
                and provider=='google')
            if not institution_identity and not pinned_admin and not pinned_student:
                if student_email and email==student_email:
                    raise HTTPException(403,'Local test-student access is disabled or awaiting a verified identity pin. Use the operator setup helper after Google sign-in; no Student enrollment has been granted.')
                raise HTTPException(403, "Only verified @rvce.edu.in Google accounts are permitted access.")
            verify_google_identity(token, claims["sub"], email, config)
            if pinned_student:
                verify_test_student_memberships(claims['sub'],config)
            elif not institution_identity and not active_google_admin(claims["sub"]):
                raise HTTPException(403, "This Google account has no active administrator membership.")
        return claims["sub"]
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(401, "Your session is invalid or expired. Sign in again.") from exc


def authenticated_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to continue.")
    return verify_token(credentials.credentials)


def onboarding_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to continue.")
    return verify_token(credentials.credentials, require_google=True)


def get_session():
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str
    role: str
    department: str
    semester: int
    section: str
    name: str
    diet: str = "any"
    student_number: str | None = None

    @property
    def scope_key(self):
        base = f"{self.department}:{self.semester}:{self.section}:{self.role}"
        return base+(":"+self.student_number if self.student_number else "")

    def require_admin(self):
        if self.role not in ADMIN_ROLES:
            raise HTTPException(403, "An administrator role is required.")
        if self.role in {"department_admin","section_admin"} and (not self.department.strip() or self.department == "All"):
            raise HTTPException(403, "This administrator membership needs a specific department.")
        if self.role == "section_admin" and self.section == "All":
            raise HTTPException(403, "This administrator membership needs a specific section.")

    def check_department_write(self, department, kind="document", semester=None, section="All"):
        self.require_admin()
        if self.role == "canteen_manager":
            if kind != "menu" or department != "All" or semester is not None or section != "All":
                raise HTTPException(403, "Canteen managers may manage only the campus canteen menu.")
            return
        if kind == "menu" and self.role != "institution_admin":
            raise HTTPException(403, "Only College Admins and Canteen Managers may manage menus.")
        if self.role in {"department_admin", "section_admin"} and department != self.department:
            raise HTTPException(403, "Department administrators may import only their own department.")
        if self.role == "section_admin" and (semester != self.semester or section != self.section):
            raise HTTPException(403, "Section administrators may manage only their own semester and section.")

    def check_source_write(self, source):
        self.check_department_write(source.department, source.kind, source.semester, source.section)


def rate_limit(session, identity, limit=120):
    window = int(time.time()) // 60
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(RateBucket).values(key=identity, window=window, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"window": window, "count": case((RateBucket.window == window, RateBucket.count + 1), else_=1)},
    ).returning(RateBucket.count)
    count = session.execute(stmt).scalar_one()
    if count > limit:
        raise HTTPException(
            429, "Too many requests. Please try again in a minute.", headers={"Retry-After": "60"}
        )


def resolve_actor(session, user_id, tenant_id):
    try:
        UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(400, "Choose a valid institution.") from exc
    set_identity(session, user_id, tenant_id)
    m = session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id, Membership.user_id == user_id, Membership.active.is_(True)
        ).execution_options(populate_existing=True)
    )
    if not m:
        raise HTTPException(403, "You do not have active membership in this institution.")
    if m.role not in ROLES:
        raise HTTPException(403, "This legacy role requires the reviewed RBAC migration.")
    restrict_test_student(user_id,tenant_id,m.role)
    return Actor(user_id, tenant_id, m.role, m.department, m.semester, m.section, m.name, m.diet, m.student_number)


def actor(
    user_id: str = Depends(authenticated_user),
    x_tenant_id: str = Header(...),
    session: Session = Depends(get_session),
):
    context = resolve_actor(session, user_id, x_tenant_id)
    rate_limit(session, f"{context.tenant_id}:{context.user_id}")
    return context


def hash_section_code(code: str, salt: str) -> str:
    import hashlib
    cleaned = code.strip().upper().replace(" ", "").replace("-", "")
    return hashlib.sha256((cleaned + ":" + salt).encode("utf-8")).hexdigest()


def generate_section_code(department: str, semester: int, section: str) -> tuple[str, str, str]:
    import secrets
    token = secrets.token_hex(8).upper()
    dept_clean = "".join(c for c in department.upper() if c.isalnum())[:4]
    display_code = f"{dept_clean}{semester}{section.upper()}-{token}"
    salt = secrets.token_hex(16)
    code_hash = hash_section_code(display_code, salt)
    return display_code, code_hash, salt
