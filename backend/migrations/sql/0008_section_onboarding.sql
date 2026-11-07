-- REVIEW DRAFT: no live execution is authorized by this file.
-- Applied by Alembic 0008 in the same transaction as the preceding revisions.
CREATE TABLE IF NOT EXISTS public.section_codes (
  id varchar(36) PRIMARY KEY,
  tenant_id varchar(36) NOT NULL REFERENCES public.institutions(id),
  department varchar(60) NOT NULL,
  semester integer NOT NULL,
  section varchar(20) NOT NULL,
  code_hash varchar(64) NOT NULL,
  salt varchar(32) NOT NULL,
  created_by varchar(36) NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_section_codes_tenant_id ON public.section_codes(tenant_id);
CREATE INDEX IF NOT EXISTS ix_section_codes_department ON public.section_codes(department);
CREATE INDEX IF NOT EXISTS ix_section_codes_code_hash ON public.section_codes(code_hash);
CREATE INDEX IF NOT EXISTS ix_section_code_lookup
  ON public.section_codes(tenant_id,department,semester,section,revoked);

DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.section_codes'::regclass
                AND conname='ck_section_codes_scope') THEN
    ALTER TABLE public.section_codes ADD CONSTRAINT ck_section_codes_scope CHECK(
      semester BETWEEN 1 AND 12 AND length(trim(department))>0 AND lower(trim(department))!='all'
      AND length(trim(section))>0 AND lower(trim(section))!='all');
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.section_codes'::regclass
                AND conname='ck_section_codes_digest') THEN
    ALTER TABLE public.section_codes ADD CONSTRAINT ck_section_codes_digest
      CHECK(length(code_hash)=64 AND length(salt)=32);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.section_codes'::regclass
                AND conname='ck_section_codes_hex') THEN
    ALTER TABLE public.section_codes ADD CONSTRAINT ck_section_codes_hex
      CHECK(code_hash ~ '^[0-9a-f]{64}$' AND salt ~ '^[0-9a-f]{32}$');
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='campus_api' AND NOT rolsuper
      AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolcanlogin) THEN
    RAISE EXCEPTION 'Reviewed NOLOGIN NOBYPASSRLS campus_api group required';
  END IF;
  IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='public' AND r.rolname='campus_api')
     OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='public' AND r.rolname='campus_api') THEN
    RAISE EXCEPTION 'The API group must not own application objects';
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
      AND pg_has_role(r.oid,(SELECT oid FROM pg_roles WHERE rolname='campus_api'),'MEMBER')) THEN
    RAISE EXCEPTION 'Public client roles must not inherit the API group';
  END IF;
END $$;

CREATE FUNCTION public.campus_section_admin(t text,d text,s integer,sec text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public SET row_security=off AS $$
  SELECT t=current_setting('app.tenant_id',true) AND EXISTS(
    SELECT 1 FROM public.memberships m WHERE m.tenant_id=t
      AND m.user_id=current_setting('app.user_id',true) AND m.active AND (
        m.role='institution_admin' OR
        (m.role='department_admin' AND m.department=d) OR
        (m.role='section_admin' AND m.department=d AND m.semester=s AND m.section=sec)))
$$;

CREATE FUNCTION public.campus_section_create(t text,d text,s integer,sec text,h text,salt_value text,days integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public SET row_security=off AS $$
DECLARE who text:=current_setting('app.user_id',true); newid text:=gen_random_uuid()::text;
        expiry timestamptz;
BEGIN
  IF NOT coalesce(public.campus_section_admin(t,d,s,sec),false) THEN
    RAISE EXCEPTION 'Section administrator permission required' USING ERRCODE='42501';
  END IF;
  IF days IS NULL OR s IS NULL OR d IS NULL OR sec IS NULL OR h IS NULL OR salt_value IS NULL OR
     days NOT BETWEEN 1 AND 90 OR s NOT BETWEEN 1 AND 12 OR
     length(d)>60 OR length(sec)>20 OR
     length(trim(d))=0 OR lower(trim(d))='all' OR length(trim(sec))=0 OR lower(trim(sec))='all'
     OR h !~ '^[0-9a-f]{64}$' OR salt_value !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'Invalid section code parameters' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('section-code:'||t||':'||d||':'||s||':'||sec,0));
  expiry:=statement_timestamp()+make_interval(days=>days);
  UPDATE public.section_codes SET revoked=true
    WHERE tenant_id=t AND department=d AND semester=s AND section=sec AND NOT revoked;
  INSERT INTO public.section_codes(id,tenant_id,department,semester,section,code_hash,salt,
                                   created_by,expires_at,revoked,created_at)
    VALUES(newid,t,d,s,sec,h,salt_value,who,expiry,false,statement_timestamp());
  INSERT INTO public.audit_logs(id,tenant_id,user_id,action,details,created_at)
    VALUES(gen_random_uuid()::text,t,who,'section_code.created',
           jsonb_build_object('code_id',newid,'department',d,'semester',s,'section',sec),statement_timestamp());
  RETURN jsonb_build_object('id',newid,'department',d,'semester',s,'section',sec,'expires_at',expiry);
END $$;

CREATE FUNCTION public.campus_section_revoke(t text,cid text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public SET row_security=off AS $$
DECLARE code public.section_codes%ROWTYPE;
BEGIN
  IF t IS DISTINCT FROM current_setting('app.tenant_id',true) THEN
    RAISE EXCEPTION 'Institution mismatch' USING ERRCODE='42501';
  END IF;
  SELECT * INTO code FROM public.section_codes WHERE id=cid AND tenant_id=t FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Section code unavailable' USING ERRCODE='22023'; END IF;
  IF NOT coalesce(public.campus_section_admin(t,code.department,code.semester,code.section),false) THEN
    RAISE EXCEPTION 'Section administrator permission required' USING ERRCODE='42501';
  END IF;
  IF NOT code.revoked THEN
    UPDATE public.section_codes SET revoked=true WHERE id=cid AND tenant_id=t;
    INSERT INTO public.audit_logs(id,tenant_id,user_id,action,details,created_at)
      VALUES(gen_random_uuid()::text,t,current_setting('app.user_id',true),'section_code.revoked',
             jsonb_build_object('code_id',cid),statement_timestamp());
  END IF;
  RETURN jsonb_build_object('revoked',true);
END $$;

CREATE FUNCTION public.campus_section_join(t text,d text,s integer,sec text,raw_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public SET row_security=off AS $$
DECLARE who text:=current_setting('app.user_id',true); member public.memberships%ROWTYPE;
        matched text; changed text;
BEGIN
  IF t IS DISTINCT FROM current_setting('app.tenant_id',true) OR who IS NULL OR
     who !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
    RAISE EXCEPTION 'Verified actor context required' USING ERRCODE='42501';
  END IF;
  IF raw_code IS NULL OR length(raw_code) NOT BETWEEN 4 AND 64 OR
     s IS NULL OR s NOT BETWEEN 1 AND 12 OR d IS NULL OR sec IS NULL OR
     lower(trim(d))='all' OR lower(trim(sec))='all' OR length(trim(d))=0 OR length(trim(sec))=0 THEN
    RAISE EXCEPTION 'Invalid section join parameters' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('student-join:'||t||':'||who,0));
  SELECT * INTO member FROM public.memberships WHERE tenant_id=t AND user_id=who FOR UPDATE;
  IF FOUND AND (NOT member.active OR member.role!='student') THEN
    RAISE EXCEPTION 'Membership requires administrator review' USING ERRCODE='42501';
  END IF;
  SELECT c.id INTO matched FROM public.section_codes c
    JOIN public.memberships creator ON creator.tenant_id=c.tenant_id AND creator.user_id=c.created_by
    WHERE c.tenant_id=t AND c.department=d AND c.semester=s AND c.section=sec
      AND NOT c.revoked AND c.expires_at>statement_timestamp()
      AND c.code_hash=encode(sha256(convert_to(
        replace(replace(upper(trim(raw_code)),' ',''),'-','')||':'||c.salt,'UTF8')),'hex')
      AND creator.active AND (
          creator.role='institution_admin' OR (creator.role='department_admin' AND creator.department=d) OR
          (creator.role='section_admin' AND creator.department=d AND creator.semester=s AND creator.section=sec))
    ORDER BY c.created_at DESC LIMIT 1 FOR SHARE OF c,creator;
  IF matched IS NULL THEN
    RAISE EXCEPTION 'Invalid or expired section join code' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.memberships(id,tenant_id,user_id,role,name,department,semester,section,interests,diet,active)
    VALUES(gen_random_uuid()::text,t,who,'student','Student',d,s,sec,'','any',true)
    ON CONFLICT(tenant_id,user_id) DO UPDATE SET department=d,semester=s,section=sec
      WHERE memberships.active AND memberships.role='student'
    RETURNING id INTO changed;
  IF changed IS NULL THEN
    RAISE EXCEPTION 'Membership requires administrator review' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.audit_logs(id,tenant_id,user_id,action,details,created_at)
    VALUES(gen_random_uuid()::text,t,who,'section.joined',
           jsonb_build_object('code_id',matched,'department',d,'semester',s,'section',sec),statement_timestamp());
  RETURN jsonb_build_object('success',true,'role','student','department',d,'semester',s,'section',sec);
END $$;

ALTER TABLE public.section_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.section_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY section_codes_admin_read ON public.section_codes FOR SELECT TO campus_api
  USING(public.campus_section_admin(tenant_id,department,semester,section));
ALTER TABLE public.rate_buckets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_buckets FORCE ROW LEVEL SECURITY;
CREATE POLICY rate_buckets_backend ON public.rate_buckets FOR ALL TO campus_api USING(true) WITH CHECK(true);

-- Supabase's explicit anon/authenticated defaults are separate from PUBLIC.
-- Restrict only CampusOS tables/functions; managed Auth/Storage schemas are untouched.
DO $$ DECLARE role_name text; item record; role_sql text; BEGIN
  FOREACH role_name IN ARRAY ARRAY['PUBLIC','anon','authenticated'] LOOP
    IF role_name='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
      role_sql:=CASE WHEN role_name='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(role_name) END;
      FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(ARRAY[
          'institutions','memberships','sources','campus_records','documents','document_chunks',
          'conversations','messages','ingestion_jobs','audit_logs','query_traces',
          'integration_connections','rate_buckets','section_codes','alembic_version']) LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %s',item.relname,role_sql);
      END LOOP;
      FOR item IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND left(p.proname,7)='campus_' LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s',item.signature,role_sql);
      END LOOP;
      EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %s',role_sql);
      IF role_name!='PUBLIC' THEN
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %s',role_sql);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %s',role_sql);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %s',role_sql);
      END IF;
    END IF;
  END LOOP;
END $$;
REVOKE CREATE ON SCHEMA public FROM campus_api;
REVOKE ALL PRIVILEGES ON TABLE public.section_codes FROM campus_api;
GRANT SELECT(id,tenant_id,department,semester,section,created_by,expires_at,revoked,created_at)
  ON public.section_codes TO campus_api;
GRANT EXECUTE ON FUNCTION public.campus_section_admin(text,text,integer,text),
  public.campus_section_create(text,text,integer,text,text,text,integer),
  public.campus_section_revoke(text,text),public.campus_section_join(text,text,integer,text,text) TO campus_api;
