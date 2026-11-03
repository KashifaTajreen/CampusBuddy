from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID
import time
import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, case
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import SessionLocal, set_identity
from backend.app.models import Membership, RateBucket

bearer = HTTPBearer(auto_error=False)
ADMIN_ROLES = {"department_admin", "institution_admin", "platform_admin"}
TOP_ROLES = {"institution_admin", "platform_admin"}


@lru_cache(maxsize=8)
def jwks_client(url):
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300, timeout=5)


def verify_token(token):
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
        return claims["sub"]
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(401, "Your session is invalid or expired. Sign in again.") from exc


def authenticated_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to continue.")
    return verify_token(credentials.credentials)


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

    def require_admin(self):
        if self.role not in ADMIN_ROLES:
            raise HTTPException(403, "An administrator role is required.")

    def check_department_write(self, department):
        self.require_admin()
        if self.role == "department_admin" and department != self.department:
            raise HTTPException(403, "Department administrators may import only their own department.")


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
        )
    )
    if not m:
        raise HTTPException(403, "You do not have active membership in this institution.")
    return Actor(user_id, tenant_id, m.role, m.department, m.semester, m.section, m.name, m.diet)


def actor(
    user_id: str = Depends(authenticated_user),
    x_tenant_id: str = Header(...),
    session: Session = Depends(get_session),
):
    context = resolve_actor(session, user_id, x_tenant_id)
    rate_limit(session, f"{context.tenant_id}:{context.user_id}", settings().rate_limit_per_minute)
    return context
