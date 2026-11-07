"""Local operator setup for one verified Google TEST STUDENT; no database writes.

Prepare the local login control, let the user complete real Google OAuth, then
pin the exact verified Auth UUID through a bounded read-only Auth lookup.
Never creates/enrolls accounts, changes memberships or exposes tokens/credentials.
"""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
import re
import shutil
import time
from uuid import UUID

from dotenv import dotenv_values
import psycopg
from scripts.configure_campusos_kt import ROOT,INPUT,SetupError,validate,connection_parameters,replace_fields

TARGET='rgqrkgyzxgiwlbuhyohh'


def verify_identity(connection,email,tenant,expected_subject=''):
    """Call only inside a read-only transaction; select no passwords or tokens."""
    accounts=connection.execute('''SELECT u.id,u.email_confirmed_at,u.last_sign_in_at,
        u.deleted_at,u.banned_until,i.identity_data FROM auth.users u
        JOIN auth.identities i ON i.user_id=u.id
        WHERE lower(u.email)=%s AND i.provider='google' ''',(email,)).fetchall()
    if len(accounts)!=1:
        raise SetupError('Exactly one Google identity must exist for the configured email. Complete Google sign-in first; no enrollment was performed.')
    subject,confirmed,last_login,deleted,banned,identity=accounts[0]
    subject=str(UUID(str(subject)))
    now=datetime.now(timezone.utc)
    if (not confirmed or not last_login or last_login<now-timedelta(minutes=30)
            or deleted or (banned and banned>now) or not isinstance(identity,dict)
            or identity.get('email_verified') is not True or str(identity.get('email','')).lower()!=email):
        raise SetupError('A recent, confirmed and verified Google identity for the exact configured email is required.')
    if expected_subject and str(UUID(expected_subject))!=subject:
        raise SetupError('The existing student UUID pin differs. No identity was changed.')
    if not connection.execute('SELECT id FROM public.institutions WHERE id=%s',(tenant,)).fetchone():
        raise SetupError('The configured institution does not exist in this approved project.')
    memberships=connection.execute('SELECT tenant_id,role,active FROM public.memberships WHERE user_id=%s',(subject,)).fetchall()
    if any(str(t)!=tenant or role!='student' or not active for t,role,active in memberships):
        raise SetupError('This identity has an incompatible or revoked membership. Use a separate Student-only test account; no memberships were changed.')
    return subject


def save_local(changes):
    directory=ROOT/'.scratch/test-student-config'/str(time.time_ns())
    directory.mkdir(parents=True,exist_ok=False)
    prepared={path:replace_fields(path,fields) for path,fields in changes.items()}
    for index,path in enumerate(prepared):
        if path.exists():
            backup=directory/(str(index)+'.env.before')
            shutil.copyfile(path,backup)
            if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(backup.read_bytes()).digest():
                raise SetupError('Configuration backup verification failed; no settings applied.')
    for path,content in prepared.items():
        path.write_text(content,encoding='utf-8')
    return str(directory.relative_to(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare-login',action='store_true',help='Enable only the local test-login button; backend exception remains disabled until verified pinning.')
    group.add_argument('--pin-identity',action='store_true',help='Read only the exact configured Google Auth identity, then write local identity pins. No enrollment.')
    group.add_argument('--disable',action='store_true',help='Disable exception and hide test login; leave audit pins and all database data intact.')
    args=parser.parse_args()
    values=dotenv_values(INPUT,interpolate=False)
    runtime=dotenv_values(ROOT/'.env',interpolate=False)
    frontend=dotenv_values(ROOT/'frontend/.env.local',interpolate=False)
    if validate(values,database=args.pin_identity)!=TARGET or runtime.get('SUPABASE_URL')!=values['SUPABASE_URL']:
        raise SetupError('Only the approved CampusOS-KT project may be used.')
    if os.environ.get('APP_ENV',runtime.get('APP_ENV','development')) not in ('development','test'):
        raise SetupError('Test student setup is disabled outside development/test mode.')
    if frontend.get('NEXT_PUBLIC_SUPABASE_URL')!=values['SUPABASE_URL']:
        raise SetupError('Frontend and backend Auth projects must match; no configuration was changed.')
    if args.disable:
        changes={INPUT:{'GOOGLE_TEST_STUDENT_ENABLED':'false'},ROOT/'.env':{'GOOGLE_TEST_STUDENT_ENABLED':'false'},
                 ROOT/'frontend/.env.local':{'NEXT_PUBLIC_ENABLE_TEST_STUDENT_LOGIN':'false'}}
    else:
        if str(values.get('GOOGLE_TEST_STUDENT_ENABLED','')).lower()!='true':
            raise SetupError('Set GOOGLE_TEST_STUDENT_ENABLED=true locally to authorize this exact test-account setup.')
        email=str(values.get('GOOGLE_TEST_STUDENT_EMAIL') or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9._%+-]*@gmail\.com',email):
            raise SetupError('Enter exactly one Gmail address in GOOGLE_TEST_STUDENT_EMAIL locally; do not paste credentials into chat.')
        if email in {str(values.get('ADMIN_EMAIL') or '').strip().lower(),str(runtime.get('GOOGLE_ADMIN_EMAIL') or '').strip().lower()}:
            raise SetupError('The administrator account cannot be configured as the test student.')
        tenant=str(UUID(str(values.get('INSTITUTION_ID') or '')))
        requested_tenant=values.get('GOOGLE_TEST_STUDENT_TENANT_ID')
        if requested_tenant and str(UUID(requested_tenant))!=tenant:
            raise SetupError('Test student institution must match the existing configured CampusOS-KT institution.')
        if frontend.get('NEXT_PUBLIC_ONBOARDING_TENANT_ID')!=tenant:
            raise SetupError('The existing onboarding institution does not match; no automatic scope change is permitted.')
        subject=''
        if args.pin_identity:
            params=connection_parameters(values['MIGRATION_DATABASE_URL'],TARGET)
            params['application_name']='campusos_test_student_read_only_verification'
            with psycopg.connect(**params) as connection:
                connection.read_only=True
                subject=verify_identity(connection,email,tenant,values.get('GOOGLE_TEST_STUDENT_USER_ID') or '')
                connection.rollback()
            if subject in {values.get('ADMIN_AUTH_USER_ID'),runtime.get('GOOGLE_ADMIN_USER_ID')}:
                raise SetupError('The verified identity is the administrator. Stopped without any role change.')
        elif runtime.get('GOOGLE_TEST_STUDENT_USER_ID'):
            raise SetupError('A verified pin already exists. Use --pin-identity to verify it again or --disable to turn access off.')
        fields={'GOOGLE_TEST_STUDENT_EMAIL':email,'GOOGLE_TEST_STUDENT_TENANT_ID':tenant,
                'GOOGLE_TEST_STUDENT_ENABLED':'true' if args.pin_identity else 'false','GOOGLE_TEST_STUDENT_USER_ID':subject}
        local={'GOOGLE_TEST_STUDENT_TENANT_ID':tenant}
        if args.pin_identity:
            local['GOOGLE_TEST_STUDENT_USER_ID']=subject
        changes={INPUT:local,ROOT/'.env':fields,ROOT/'frontend/.env.local':{'NEXT_PUBLIC_ENABLE_TEST_STUDENT_LOGIN':'true'}}
    backup=save_local(changes)
    print(json.dumps({'local_configuration_updated':True,'identity_verified_and_pinned':bool(args.pin_identity),
        'backend_exception_enabled':bool(args.pin_identity),'onboarding_bypassed':False,'database_writes':0,
        'administrator_changes':0,'credentials_printed':False,'api_restart_required':True,'frontend_env_reload_required':True,
        'configuration_backup':backup}))


if __name__=='__main__':
    try:
        main()
    except SetupError as exc:
        print(str(exc))
        raise SystemExit(2)
    except Exception as exc:
        print('Test student setup stopped: '+type(exc).__name__+'. Details withheld; no database writes performed.')
        raise SystemExit(2)
