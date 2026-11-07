# Temporary development Google test student

This exception is **disabled by default** and must remain off for production/staging. It permits exactly one configured Gmail Google identity to use the normal Student onboarding flow. It does not enroll the account, grant a role or bypass a section join code.

Server-only fields in your ignored local environment:

```dotenv
GOOGLE_TEST_STUDENT_ENABLED=false
GOOGLE_TEST_STUDENT_EMAIL=
GOOGLE_TEST_STUDENT_USER_ID=
GOOGLE_TEST_STUDENT_TENANT_ID=
```

Optional localhost UI control in `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_ENABLE_TEST_STUDENT_LOGIN=false
```

An authorized operator first verifies the exact Google identity and Supabase Auth UUID in the approved project, then pins both email and UUID plus tenant. Never match only an email or trust editable user metadata. Do not put passwords/tokens in documentation or chat. Restart the local API after configuration changes; settings are cached.

`backend/app/auth.py` verifies JWT signature/issuer/audience/expiry and current provider-owned Google identity. `configured_test_student`, `verify_test_student_memberships` and `restrict_test_student` constrain the account to Student in the pinned tenant. Revoked or elevated memberships fail closed. `backend/app/section_onboarding.py` still enforces scoped expiring join codes; selecting Section B never authorizes Section A records. Campus-wide published menus/events obey ordinary scope rules. The existing pinned Gmail admin path is separate.

`python -m scripts.configure_test_student --help` describes the existing **project-specific** operator helper. It validates its fixed approved project and local input profile; it is not a general account provisioning tool for a teammate's new project. Do not remove those guards. Tests cover the generic backend controls with mocked Google replies and disposable databases (`backend/tests/test_google_test_student.py`).

To disable, set the backend enable flag and frontend flag to false, restart/rebuild the corresponding local processes and verify the exception is denied. Preserve identity pins until an operator reviews retirement, to prevent accidental role reassignment. Supabase Auth deletion or membership cleanup is a separate approved action, not performed automatically by this feature.
