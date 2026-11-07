"""Inventory existing weights and reuse the unchanged isolated DB orchestrator.

No model download, original-data migration or application/scoring modification.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
INVENTORY=ROOT/'artifacts/original_all_models_inventory.json'


def inventory():
    stores=[ROOT/'.scratch/phase7-models/ollama',Path.home()/'.ollama/models',ROOT/'.scratch/shared-llama-models/ollama']
    found={}
    for store in stores:
        directory=store/'manifests/registry.ollama.ai/library'
        if not directory.is_dir():
            continue
        for path in directory.glob('*/*'):
            if not path.is_file():
                continue
            try:
                manifest=json.loads(path.read_text())
                layers=[manifest['config'],*manifest['layers']]
                if not all((store/'blobs'/x['digest'].replace(':','-')).is_file() and
                    (store/'blobs'/x['digest'].replace(':','-')).stat().st_size==x['size'] for x in layers):
                    continue
                config=json.loads((store/'blobs'/manifest['config']['digest'].replace(':','-')).read_text())
            except (ValueError,KeyError,OSError):
                continue
            tag=path.parent.name+':'+path.name
            embedding=config.get('model_family') in ('bert','nomic-bert')
            entry=dict(model=tag,quantization=config.get('file_type'),family=config.get('model_family'),
                capability='embedding_only' if embedding else 'completion',size_bytes=sum(x['size'] for x in layers),
                source_store=str(store),manifest_relative=str(path.relative_to(store)),
                model_weight_digests=[x['digest'] for x in manifest['layers'] if x.get('mediaType','').endswith('.image.model')],
                source_stores=[str(store)])
            if tag in found:
                if str(store) not in found[tag]['source_stores']:
                    found[tag]['source_stores'].append(str(store))
            else:
                found[tag]=entry
    priority={'llama3.2:1b':0,'llama3.2:3b':1,'qwen3.5:2b':2,'llama3:8b':3}
    rows=sorted(found.values(),key=lambda r:(r['capability']=='embedding_only',priority.get(r['model'],50),r['model']))
    if not any(r['capability']=='completion' for r in rows):
        raise RuntimeError('No complete installed chat-model weights found; no download attempted')
    result=dict(models=rows,models_downloaded=False,embedding_provider_for_all_runs='Existing E5-small, unchanged',
        source_scope='Default Ollama store plus previously approved workspace-local stores; incomplete downloads excluded')
    INVENTORY.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    rows=inventory()
    print(json.dumps(dict(installed=[dict(model=m['model'],quantization=m['quantization'],capability=m['capability']) for m in rows['models']])),flush=True)
    from scripts import benchmark_original_postgres as base
    frozen=ROOT/'scripts/original_postgres_scenario.py'
    before=hashlib.sha256(frozen.read_bytes()).hexdigest()
    base.REPORT=ROOT/'artifacts/original_all_models_benchmark.json'
    original_popen=subprocess.Popen
    def launch(args,*positional,**kwargs):
        # Redirect only the owned fixture child. Docker and all other processes
        # use the original Popen unchanged; application routes are not redirected.
        if isinstance(args,list) and args[:3]==[sys.executable,'-m','scripts.original_postgres_scenario']:
            args=[*args[:2],'scripts.original_all_models_scenario',*args[3:]]
        return original_popen(args,*positional,**kwargs)
    with patch.object(subprocess,'Popen',launch):
        code=base.main()
    assert hashlib.sha256(frozen.read_bytes()).hexdigest()==before,'Baseline fixture/scoring source changed'
    return code


if __name__=='__main__':
    raise SystemExit(main())
