"""Temporary Gmail Student gate, using synthetic JWTs/Google replies/local DB only."""
from datetime import datetime,timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select,func

from backend.app import auth
from backend.app.config import Settings
from backend.app.models import Membership
from backend.tests.test_google_identity import identity as identity,SUBJECT
from backend.tests.test_auth_api import client as client,key as key,token
from scripts.configure_test_student import verify_identity,SetupError

EMAIL='one-test-student@gmail.com'


@pytest.fixture
def student_auth(client,identity,key,env,monkeypatch):
    state,_=identity
    state['user']['email']=EMAIL
    state['user']['identities'][0]['identity_data']['email']=EMAIL
    config=auth.settings()
    config.app_env='development'
    config.google_test_student_enabled=True
    config.google_test_student_email=EMAIL
    config.google_test_student_user_id=SUBJECT
    config.google_test_student_tenant_id=env.tenant
    monkeypatch.setattr(auth,'settings',lambda:config)
    monkeypatch.setattr(auth,'SessionLocal',env.factory)
    signed=token(key,SUBJECT,email=EMAIL,app_metadata={'provider':'google'})
    return state,config,signed


def test_verified_exact_pin_allows_only_normal_onboarding_not_auto_enrollment(student_auth,env,client):
    state,_,signed=student_auth
    assert auth.verify_token(signed)==SUBJECT
    assert auth.verify_token(signed,require_google=True)==SUBJECT
    assert state['called']
    response=client.get('/v1/memberships',headers={'Authorization':'Bearer '+signed})
    assert response.status_code==200 and response.json()==[]
    assert env.db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id==SUBJECT))==0
    assert client.get('/v1/me',headers={'Authorization':'Bearer '+signed}).status_code==403


@pytest.mark.parametrize('field,value',[
    ('google_test_student_enabled',False),('app_env','production'),('app_env','staging'),
    ('google_test_student_email','different@gmail.com'),('google_test_student_user_id',''),
    ('google_test_student_user_id','00000000-0000-4000-8000-000000000889'),
    ('google_test_student_tenant_id',''),('google_test_student_tenant_id','not-a-uuid'),
    ('google_admin_email',EMAIL),('google_admin_user_id',SUBJECT),
])
def test_exception_fails_closed_without_every_required_condition(student_auth,field,value):
    state,config,signed=student_auth
    setattr(config,field,value)
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed,require_google=True)
    assert exc.value.status_code==403
    assert not state.get('called')


@pytest.mark.parametrize('provider,verified',[('email',True),('google',False)])
def test_provider_owned_verified_google_identity_required(student_auth,provider,verified):
    state,_,signed=student_auth
    state['user']['identities'][0]['provider']=provider
    state['user']['identities'][0]['identity_data']['email_verified']=verified
    state['user']['user_metadata']={'email_verified':True,'role':'institution_admin'}
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code==403


def test_password_session_cannot_use_google_test_exception(student_auth,key):
    state,_,_=student_auth
    signed=token(key,SUBJECT,email=EMAIL,app_metadata={'provider':'email','providers':['email','google']})
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code==403 and not state.get('called')


@pytest.mark.parametrize('role,active',[('institution_admin',True),('department_admin',True),('section_admin',True),('canteen_manager',True),('student',False)])
def test_existing_nonstudent_or_revoked_membership_is_denied(student_auth,env,role,active):
    _,_,signed=student_auth
    env.db.add(Membership(tenant_id=env.tenant,user_id=SUBJECT,role=role,active=active,department='AIML',semester=3,section='B',name='Synthetic test student'))
    env.db.commit()
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed)
    assert exc.value.status_code==403
    if role!='student':
        with pytest.raises(HTTPException):
            auth.resolve_actor(env.db,SUBJECT,env.tenant)


def test_test_identity_cannot_be_assigned_an_admin_role(student_auth,client,env):
    response=client.post('/v1/admin/memberships',json=dict(user_id=SUBJECT,role='institution_admin',department='All',semester=1,section='All',name='Synthetic test identity'))
    assert response.status_code==403
    assert env.db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id==SUBJECT))==0


def test_normal_join_code_and_section_b_data_boundaries(student_auth,client,env):
    _,_,signed=student_auth
    created=client.post('/v1/admin/section-codes',json=dict(department='AIML',semester=3,section='B',expiry_days=1))
    assert created.status_code==200
    headers={'Authorization':'Bearer '+signed,'X-Tenant-ID':env.tenant}
    body=dict(department='AIML',semester=3,section='B',code='WRONG')
    assert client.post('/v1/onboarding/join-section',headers=headers,json=body).status_code==400
    assert client.post('/v1/onboarding/join-section',headers={**headers,'X-Tenant-ID':env.other_tenant},json=body).status_code==403
    body['code']=created.json()['code']
    assert client.post('/v1/onboarding/join-section',headers=headers,json={**body,'role':'institution_admin'}).status_code==422
    response=client.post('/v1/onboarding/join-section',headers=headers,json=body)
    assert response.status_code==200 and response.json()['role']=='student'
    me=client.get('/v1/me',headers=headers)
    assert me.status_code==200 and (me.json()['department'],me.json()['semester'],me.json()['section'])==('AIML',3,'B')
    timetable=client.get('/v1/timetable?on=2026-10-28',headers=headers)
    assert timetable.status_code==200 and timetable.json()
    assert all(r['section']=='B' and r['semester']==3 and r['department']=='AIML' for r in timetable.json())
    menu=client.get('/v1/menu',headers=headers)
    assert menu.status_code==200 and menu.json() and all(r['department']=='All' for r in menu.json())
    events=client.get('/v1/events',headers=headers)
    assert events.status_code==200 and events.json() and all(r['department']=='All' for r in events.json())
    assert client.post('/v1/admin/section-codes',headers=headers,json=dict(department='AIML',semester=3,section='B',expiry_days=1)).status_code==403
    assert client.post('/v1/ingestion/preview',headers=headers,files={'file':('no.txt',b'No shared student uploads','text/plain')},data={'options':'{"kind":"events"}'}).status_code==403
    member=env.db.scalar(select(Membership).where(Membership.user_id==SUBJECT))
    member.active=False
    env.db.commit()
    assert client.get('/v1/me',headers=headers).status_code==403


def test_default_disabled_and_production_rejects_exception():
    assert Settings(_env_file=None).google_test_student_enabled is False
    with pytest.raises(ValueError,match='development/test only'):
        Settings(_env_file=None,app_env='production',google_test_student_enabled=True).validate_production()


class Lookup:
    def __init__(self,account,memberships=None):
        self.account=account
        self.memberships=memberships or []
        self.queries=[]
    def execute(self,sql,params):
        assert sql.lstrip().upper().startswith('SELECT')
        assert not any(v in sql.lower() for v in ('encrypted_password','access_token','refresh_token'))
        self.queries.append((sql,params))
        rows=self.account if 'auth.users' in sql else self.memberships if 'memberships' in sql else [('tenant',)]
        return SimpleNamespace(fetchall=lambda:rows,fetchone=lambda:rows[0] if rows else None)


def test_operator_pin_verifies_only_google_identity_and_never_enrolls():
    now=datetime.now(timezone.utc)
    db=Lookup([(SUBJECT,now,now,None,None,{'email':EMAIL,'email_verified':True})])
    assert verify_identity(db,EMAIL,'tenant')==SUBJECT
    assert db.queries[0][1]==(EMAIL,)
    assert len(db.queries)==3
    db.memberships=[('tenant','institution_admin',True)]
    with pytest.raises(SetupError,match='incompatible'):
        verify_identity(db,EMAIL,'tenant')
    db.memberships=[]
    db.account[0][-1]['email_verified']=False
    with pytest.raises(SetupError,match='verified Google'):
        verify_identity(db,EMAIL,'tenant')
