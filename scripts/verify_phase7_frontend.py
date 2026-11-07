"""Run all responsive browser cases against production Next.js, sequentially."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    reports=[]
    for project in ("desktop","mobile","tablet"):
        print("Production browser verification: "+project,flush=True)
        completed=subprocess.run([sys.executable,"-m","scripts.verify_frontend","--production","--project",project],cwd=ROOT)
        for name in ("frontend-verification.json","browser-tests.json"):
            source=ROOT/"artifacts"/name
            if source.exists():
                shutil.copyfile(source,ROOT/"artifacts"/("phase7-"+project+"-"+name))
        result=json.loads((ROOT/"artifacts/frontend-verification.json").read_text())
        reports.append(dict(project=project,**result))
        (ROOT/"artifacts/phase7-frontend.json").write_text(json.dumps(dict(projects=reports),indent=2),encoding="utf-8")
        if completed.returncode:
            raise SystemExit(completed.returncode)
    assert all(r["browser"]["unexpected"]==r["browser"]["flaky"]==r["browser"]["skipped"]==0 for r in reports)
    print(json.dumps(dict(production=True,passed=sum(r["browser"]["expected"] for r in reports),failed=0,skipped=0)),flush=True)


if __name__=="__main__":
    main()
