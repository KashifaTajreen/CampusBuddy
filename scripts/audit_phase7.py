"""Local, redacted deployment/model inventory. Never contacts configured services."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def main():
    from dotenv import dotenv_values
    candidates=[]
    for path in (ROOT/".env",ROOT/"frontend/.env.local",ROOT.parent/".env"):
        if not path.is_file():
            continue
        values=dotenv_values(path,interpolate=False)
        public={}
        for key in ("SUPABASE_URL","NEXT_PUBLIC_SUPABASE_URL","NEXT_PUBLIC_API_URL","FRONTEND_ORIGIN"):
            if values.get(key):
                url=urlsplit(values[key])
                public[key]=dict(scheme=url.scheme,host=url.hostname,port=url.port)
        candidates.append(dict(config=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else "../.env",
            configured_names=sorted(k for k,v in values.items() if v),public_origins=public,confirmed_staging=False,
            secrets_recorded=False,network_accessed=False))
    project_files=[]
    for path in (ROOT/".vercel/project.json",ROOT/"frontend/.vercel/project.json",ROOT.parent/".vercel/project.json"):
        if path.is_file():
            data=json.loads(path.read_text())
            project_files.append(dict(path=str(path),**{k:data[k] for k in ("projectId","orgId","projectName") if k in data}))
    baseline=json.loads((ROOT/"artifacts/development-migration-0005.json").read_text())
    original=Path(baseline["database"]).resolve()
    digest=hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest==baseline["after_sha256"],"Original database changed; stop and investigate"
    with sqlite3.connect(original.as_uri()+"?mode=ro",uri=True) as connection:
        revision=connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    versions={}
    for package in ("PyMuPDF","faster-whisper","ctranslate2","onnxruntime","tokenizers"):
        try:
            versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package]=None
    remote=subprocess.run(["git","config","--get","remote.origin.url"],cwd=ROOT,capture_output=True,text=True).stdout.strip()
    if remote.startswith("git@"):
        remote=remote.split("@",1)[1]
    else:
        parsed=urlsplit(remote)
        remote=(parsed.hostname or "")+parsed.path
    free=shutil.disk_usage(ROOT).free
    report=dict(candidates=candidates,vercel_project_metadata=project_files,git_remote_redacted=remote,
        render_linkage_found=False,proposed_render_services=["campusos-api-staging","campusos-worker-staging"],
        original_database=dict(revision=revision,unchanged=True,sha256=digest),versions=versions,free_disk_gib=round(free/2**30,1),
        cloud_accessed=False,credentials_used=False,live_deployed=False,
        approval="Local work and Llama/BGE/Whisper downloads only. Deployment targets remain unconfirmed.")
    (ROOT/"artifacts/phase7-local-audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(dict(original_revision=revision,original_unchanged=True,linked_vercel_configs=len(project_files),
        local_origin_configs=len(candidates),cloud_accessed=False,versions=versions)))


if __name__=="__main__":
    main()
