"""Enable the existing loopback-only provider; never change database settings."""
import json
import shutil
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from scripts.configure_campusos_kt import replace_fields


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / '.env'
    before = dotenv_values(target, interpolate=False)
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get('http://127.0.0.1:11434/api/tags')
        response.raise_for_status()
    models = response.json()['models']
    model = next(m for m in models if m['name'] == 'qwen3.5:2b')
    backup = root / '.scratch' / 'local-qwen' / str(time.time_ns())
    backup.mkdir(parents=True)
    shutil.copy2(target, backup / 'env.backup')
    target.write_text(replace_fields(target, {
        'LLM_ENABLED': 'true', 'LLM_PROVIDER': 'ollama',
        'LLM_DATA_POLICY': 'private_local',
        'LLM_POLICY_REFERENCE': 'User-approved loopback Qwen on permission-filtered evidence only',
        'LLM_TIMEOUT_SECONDS': '8', 'OLLAMA_BASE_URL': 'http://127.0.0.1:11434',
        'OLLAMA_MODEL': 'qwen3.5:2b',
    }), encoding='utf-8')
    after = dotenv_values(target, interpolate=False)
    assert before.get('DATABASE_URL') == after.get('DATABASE_URL')
    print(json.dumps({'provider':'ollama', 'model':model['name'], 'size_bytes':model['size'],
                      'loopback_only':True, 'database_configuration_unchanged':True}))


if __name__ == '__main__':
    main()
