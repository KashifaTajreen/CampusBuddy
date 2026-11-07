import json
from pathlib import Path
import os
from scripts.model_resources import rss_tree


def test_fixed_benchmark_covers_required_domains_and_excludes_unapproved_models():
    data=json.loads(Path("config/evaluation/phase7.json").read_text(encoding="utf-8"))
    assert data["synthetic"] and len({c["id"] for c in data["cases"]})==len(data["cases"])
    assert {c["language"] for c in data["cases"]}=={"en","hi","kn"}
    assert {c.get("kind") for c in data["cases"]}>={"classes","food","multi"}
    assert any(c.get("denied") for c in data["cases"]) and any(c.get("missing") for c in data["cases"])
    from scripts.phase7_models import MODELS
    assert set(MODELS.values())=={"llama3.2:3b","bge-m3:latest"}


def test_resource_counter_accepts_64_bit_owned_process_handle():
    # Regression for the Windows handle truncation found in the actual benchmark.
    value=rss_tree({os.getpid()})
    assert value is not None and value>0
