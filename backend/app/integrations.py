"""Read-only Google OAuth with PKCE, single-use state, encrypted tokens and revocation."""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from backend.app.config import settings
from backend.app.auth import actor, get_session, resolve_actor, Actor, rate_limit
from backend.app.db import SessionLocal
from backend.app.models import IntegrationConnection, CampusRecord, Source, AuditLog, utcnow, uid
from backend.app.services import CampusService
from backend.app.ingestion import Ingestion, natural_key
from backend.app.schemas import ImportOptions, EventData

router = APIRouter(prefix="/v1/integrations")
SCOPES = {
    "google_gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "google_calendar": "https://www.googleapis.com/auth/calendar.events.readonly",
}


def configured():
    c = settings()
    return bool(c.google_client_id and c.google_client_secret and c.token_encryption_key)


def cipher():
    if not configured():
        raise HTTPException(
            503, "Google OAuth is not configured. Ask the deployment operator to follow docs/deployment.md."
        )
    try:
        return Fernet(settings().token_encryption_key.encode())
    except ValueError as exc:
        raise HTTPException(503, "The integration encryption key is invalid.") from exc


def connection(db, a, provider):
    if provider not in SCOPES:
        raise HTTPException(404, "Unknown connection provider.")
    return db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == a.tenant_id,
            IntegrationConnection.user_id == a.user_id,
            IntegrationConnection.provider == provider,
        )
    )


@router.get("")
def list_connections(a: Actor = Depends(actor), db=Depends(get_session)):
    return [
        dict(
            provider=p,
            configured=configured(),
            connected=bool((c := connection(db, a, p)) and c.encrypted_token),
        )
        for p in SCOPES
    ]


@router.post("/{provider}/connect")
def start(provider: str, a: Actor = Depends(actor), db=Depends(get_session)):
    box = cipher()
    conn = connection(db, a, provider)
    if conn is None:
        conn = IntegrationConnection(tenant_id=a.tenant_id, user_id=a.user_id, provider=provider)
        db.add(conn)
    verifier = secrets.token_urlsafe(48)
    expires = utcnow() + timedelta(minutes=10)
    state = box.encrypt(
        json.dumps(
            dict(
                tenant=a.tenant_id,
                user=a.user_id,
                provider=provider,
                nonce=secrets.token_urlsafe(24),
                verifier=verifier,
                exp=int(expires.timestamp()),
            )
        ).encode()
    ).decode()
    conn.state_hash = hashlib.sha256(state.encode()).hexdigest()
    conn.state_expires = expires
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = dict(
        client_id=settings().google_client_id,
        redirect_uri=settings().google_redirect_uri,
        response_type="code",
        scope=SCOPES[provider],
        state=state,
        access_type="offline",
        prompt="consent",
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)}


@router.get("/google/callback")
def callback(state: str = "", code: str = "", error: str = ""):
    if error:
        raise HTTPException(400, "Google connection was cancelled. Return to Settings to retry.")
    if len(state) > 4000 or not code:
        raise HTTPException(400, "Invalid Google authorization callback.")
    box = cipher()
    try:
        data = json.loads(box.decrypt(state.encode(), ttl=600))
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(400, "Connection link expired. Connect again from Settings.") from exc
    with SessionLocal() as db:
        a = resolve_actor(db, data["user"], data["tenant"])
        conn = connection(db, a, data["provider"])
        if (
            not conn
            or not conn.state_hash
            or not secrets.compare_digest(conn.state_hash, hashlib.sha256(state.encode()).hexdigest())
        ):
            raise HTTPException(400, "This connection link has already been used.")
        conn.state_hash = ""
        conn.state_expires = None
        db.commit()
        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/token",
                    data=dict(
                        client_id=settings().google_client_id,
                        client_secret=settings().google_client_secret,
                        redirect_uri=settings().google_redirect_uri,
                        code=code,
                        code_verifier=data["verifier"],
                        grant_type="authorization_code",
                    ),
                )
                response.raise_for_status()
                token = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Google authorization failed. Try connecting again.") from exc
        if not token.get("access_token") or SCOPES[data["provider"]] not in token.get("scope", "").split():
            raise HTTPException(400, "The required read-only permission was not granted.")
        # Reapply transaction-local RLS identity after commit.
        resolve_actor(db, data["user"], data["tenant"])
        conn = connection(db, a, data["provider"])
        token["expires_at"] = int(utcnow().timestamp()) + int(token.get("expires_in", 3600))
        conn.encrypted_token = box.encrypt(json.dumps(token).encode()).decode()
        db.add(
            AuditLog(
                tenant_id=a.tenant_id,
                user_id=a.user_id,
                action="integration.connected",
                details={"provider": data["provider"]},
            )
        )
        db.commit()
    return RedirectResponse(settings().frontend_origin + "/?connection=connected", status_code=303)


def token_for(db, conn):
    try:
        token = json.loads(cipher().decrypt(conn.encrypted_token.encode()))
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(
            400, "Connection credentials are unavailable. Disconnect and connect again."
        ) from exc
    if token.get("expires_at", 0) <= int(utcnow().timestamp()) + 60:
        if not token.get("refresh_token"):
            raise HTTPException(401, "Google authorization expired. Connect again.")
        with httpx.Client(timeout=15) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings().google_client_id,
                    "client_secret": settings().google_client_secret,
                    "refresh_token": token["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
            if response.status_code != 200:
                raise HTTPException(401, "Google authorization expired or was revoked. Connect again.")
            token.update(response.json())
            token["expires_at"] = int(utcnow().timestamp()) + int(token.get("expires_in", 3600))
            conn.encrypted_token = cipher().encrypt(json.dumps(token).encode()).decode()
    return token


@router.post("/{provider}/sync")
def sync(provider: str, a: Actor = Depends(actor), db=Depends(get_session)):
    conn = connection(db, a, provider)
    if not conn or not conn.encrypted_token:
        raise HTTPException(400, "Connect your own Google account first.")
    rate_limit(db, f"google-sync:{a.tenant_id}:{a.user_id}", 5)
    token = token_for(db, conn)
    c = CampusService(db, a)
    try:
        with httpx.Client(timeout=20, headers={"Authorization": "Bearer " + token["access_token"]}) as client:
            if provider == "google_gmail":
                response = client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    params={"maxResults": 50, "q": "newer_than:30d"},
                )
                response.raise_for_status()
                refs = response.json().get("messages", [])
                rows = []

                def plain(part):
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                            "utf-8", errors="replace"
                        )
                    return "\n".join(plain(p) for p in part.get("parts", []))

                for ref in refs:
                    response = client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + ref["id"],
                        params={"format": "full"},
                    )
                    response.raise_for_status()
                    msg = response.json()
                    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
                    rows.append(
                        dict(
                            sender=headers.get("from", "Unknown"),
                            subject=headers.get("subject", "Untitled"),
                            body=(plain(msg["payload"]) or msg.get("snippet", ""))[:100000],
                            received=datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc)
                            .astimezone(c.zone)
                            .replace(tzinfo=None)
                            .isoformat(),
                            unread="UNREAD" in msg.get("labelIds", []),
                        )
                    )
                if not rows:
                    return {"records": 0}
                ingestion = Ingestion(c)
                job = ingestion.receive(
                    "Authorized Gmail.json", json.dumps(rows).encode(), ImportOptions(kind="emails")
                )
                if job.status != "completed":
                    ingestion.confirm(job)
                    ingestion.process(job)
                    job.status = "completed"
                count = len(rows)
            else:
                response = client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    params={
                        "timeMin": utcnow().isoformat(),
                        "timeMax": (utcnow() + timedelta(days=30)).isoformat(),
                        "singleEvents": "true",
                        "maxResults": 200,
                    },
                )
                response.raise_for_status()
                rows = []
                for event in response.json().get("items", []):
                    if event.get("status") == "cancelled":
                        continue

                    def local(part):
                        value = part.get("dateTime") or part["date"] + "T00:00:00"
                        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        return (dt.astimezone(c.zone) if dt.tzinfo else dt).replace(tzinfo=None)

                    rows.append(
                        EventData(
                            title=event.get("summary", "Personal activity"),
                            start=local(event["start"]),
                            end=local(event["end"]),
                            location=event.get("location", "Personal"),
                            kind="personal",
                        ).model_dump(mode="json")
                    )
                if not rows:
                    return {"records": 0}
                content = json.dumps(rows, sort_keys=True).encode()
                checksum = hashlib.sha256(content).hexdigest()
                source = db.scalar(
                    select(Source).where(
                        Source.tenant_id == a.tenant_id,
                        Source.uploaded_by == a.user_id,
                        Source.kind == "google_calendar",
                        Source.checksum == checksum,
                    )
                )
                if not source:
                    sid = uid()
                    key = f"{a.tenant_id}/{sid}/{checksum}"
                    from backend.app.storage import ObjectStore

                    ObjectStore().put(key, content)
                    source = Source(
                        id=sid,
                        tenant_id=a.tenant_id,
                        filename="Authorized Google Calendar.json",
                        kind="google_calendar",
                        checksum=checksum,
                        storage_key=key,
                        uploaded_by=a.user_id,
                        owner_id=a.user_id,
                        department="All",
                        publication_status="published",
                    )
                    db.add(source)
                    db.flush()
                for row in rows:
                    key = natural_key("events", row, a.user_id)
                    record = db.scalar(
                        select(CampusRecord).where(
                            CampusRecord.tenant_id == a.tenant_id,
                            CampusRecord.kind == "events",
                            CampusRecord.natural_key == key,
                            CampusRecord.owner_id == a.user_id,
                        )
                    )
                    if record:
                        record.payload = row
                        record.source_id = source.id
                    else:
                        db.add(
                            CampusRecord(
                                tenant_id=a.tenant_id,
                                kind="events",
                                natural_key=key,
                                department="All",
                                owner_id=a.user_id,
                                source_id=source.id,
                                payload=row,
                            )
                        )
                count = len(rows)
        db.add(
            AuditLog(
                tenant_id=a.tenant_id,
                user_id=a.user_id,
                action="integration.synced",
                details={"provider": provider, "records": count},
            )
        )
        return {"records": count}
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Google data could not be read. Check the connection and retry.") from exc


@router.delete("/{provider}")
def disconnect(provider: str, a: Actor = Depends(actor), db=Depends(get_session)):
    conn = connection(db, a, provider)
    if not conn:
        return {"disconnected": True}
    if conn.encrypted_token:
        try:
            token = json.loads(cipher().decrypt(conn.encrypted_token.encode()))
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/revoke",
                    data={"token": token.get("refresh_token") or token["access_token"]},
                )
                if response.status_code not in (200, 400):
                    raise HTTPException(
                        502, "Google revocation failed. Revoke CampusOS in your Google account, then retry."
                    )
        except (httpx.HTTPError, InvalidToken, ValueError) as exc:
            raise HTTPException(
                502,
                "Revocation could not be confirmed. Revoke CampusOS in your Google account, then retry disconnecting.",
            ) from exc
    db.delete(conn)
    db.add(
        AuditLog(
            tenant_id=a.tenant_id,
            user_id=a.user_id,
            action="integration.disconnected",
            details={"provider": provider},
        )
    )
    return {
        "disconnected": True,
        "retention": "Previously imported records remain private to your account. Use the retention CLI to purge them.",
    }
