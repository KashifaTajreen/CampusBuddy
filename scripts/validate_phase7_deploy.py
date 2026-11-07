"""Validate local descriptors against public schemas; no account/credential API."""
import hashlib
import json
from pathlib import Path
import httpx
import jsonschema
import yaml

ROOT=Path(__file__).resolve().parents[1]


def main():
    results=[]
    with httpx.Client(timeout=30,follow_redirects=True,trust_env=False) as client:
        for name,url in (("render.yaml","https://render.com/schema/render.yaml.json"),
                         ("frontend/vercel.json","https://openapi.vercel.sh/vercel.json")):
            response=client.get(url)
            response.raise_for_status()
            assert len(response.content)<2_000_000
            schema=response.json()
            text=(ROOT/name).read_text(encoding="utf-8")
            value=yaml.safe_load(text) if name.endswith("yaml") else json.loads(text)
            errors=list(jsonschema.validators.validator_for(schema)(schema).iter_errors(value))
            results.append(dict(file=name,source=url,schema_sha256=hashlib.sha256(response.content).hexdigest(),
                valid=not errors,errors=[e.message[:300] for e in errors]))
    config=yaml.safe_load((ROOT/"render.yaml").read_text())
    assert all(s["autoDeployTrigger"]=="off" for s in config["services"])
    assert not any("sync" in v for group in config["envVarGroups"] for v in group["envVars"])
    assert not any(s.get("preDeployCommand") or s.get("initialDeployHook") for s in config["services"])
    report=dict(checks=results,account_api_called=False,resources_changed=False)
    (ROOT/"artifacts/phase7-deployment-schema.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))
    assert all(r["valid"] for r in results)


if __name__=="__main__":
    main()
