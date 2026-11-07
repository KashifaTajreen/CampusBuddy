"""Explicitly approved local candidate downloads and an owned Ollama process only."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx
from scripts.verify_frontend import own_windows_tree

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / ".scratch/phase7-models"
MODELS = {"llama": "llama3.2:3b", "bge": "bge-m3:latest"}
MAX_BYTES = 7_000_000_000


@contextmanager
def server():
    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama is not installed; no installer will run automatically.")
    STORE.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", OLLAMA_MODELS=str(STORE / "ollama"),
        OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_KEEP_ALIVE="0",
        OLLAMA_CONTEXT_LENGTH="4096", OLLAMA_NO_CLOUD="1")
    with (STORE / "owned-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([executable, "serve"], env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        close = own_windows_tree(process)
        try:
            with httpx.Client(base_url=origin, timeout=120, trust_env=False) as client:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Owned Ollama stopped; inspect ignored owned-server.log.")
                    try:
                        if client.get("/api/version").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError("Owned Ollama readiness timed out.")
                yield client, origin, process.pid
        finally:
            close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


def whisper_download():
    folder = STORE / "whisper-small"
    folder.mkdir(exist_ok=True)
    records = []
    with httpx.Client(timeout=300, follow_redirects=True, trust_env=False) as client:
        info = client.get("https://huggingface.co/api/models/Systran/faster-whisper-small?blobs=true")
        info.raise_for_status()
        metadata = info.json()
        for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
            item = next(x for x in metadata["siblings"] if x["rfilename"] == name)
            path = folder / name
            if not path.exists():
                partial = folder / (name + ".partial")
                url = f"https://huggingface.co/Systran/faster-whisper-small/resolve/{metadata['sha']}/{name}"
                for attempt in range(3):
                    count = partial.stat().st_size if partial.exists() else 0
                    try:
                        headers={"Range":f"bytes={count}-"} if count else {}
                        with client.stream("GET",url,headers=headers) as response:
                            response.raise_for_status()
                            append=response.status_code==206 and response.headers.get("content-range","").startswith(f"bytes {count}-")
                            if not append:
                                count=0
                            with partial.open("ab" if append else "wb") as output:
                                for chunk in response.iter_bytes():
                                    count+=len(chunk)
                                    if count>550_000_000:
                                        raise RuntimeError("Whisper artifact exceeded the approved bound.")
                                    output.write(chunk)
                        break
                    except httpx.TransportError:
                        if attempt==2:
                            raise
                        print("Retrying approved Whisper artifact with bounded resume.",flush=True)
                partial.replace(path)
            content = path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            if item.get("lfs"):
                assert sha == item["lfs"]["sha256"], "Whisper checksum mismatch"
            else:
                assert hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == item["blobId"]
            records.append(dict(file=name, bytes=len(content), sha256=sha))
    return dict(repository="Systran/faster-whisper-small", revision=metadata["sha"], files=records, license="MIT")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-approved", action="store_true")
    args = parser.parse_args()
    if not args.download_approved:
        print("Plan only: approved candidates llama3.2:3b, bge-m3:latest, Whisper-small; no Qwen. No downloads.")
        return
    assert shutil.disk_usage(ROOT).free >= 10 * 2**30, "Need 10 GiB free disk"
    report = dict(approved=[*MODELS.values(), "Systran/faster-whisper-small"], qwen="not approved",
        production_changed=False, external_campus_uploads=False, models=[])
    with server() as (client, _, _):
        report["ollama_version"] = client.get("/api/version").json()["version"]
        sizes = {}
        for tag in MODELS.values():
            print("Pulling approved candidate " + tag, flush=True)
            with client.stream("POST", "/api/pull", json={"model": tag, "stream": True}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    progress = json.loads(line)
                    if progress.get("error"):
                        raise RuntimeError("Approved model pull failed: " + str(progress["error"])[:200])
                    if progress.get("digest") and progress.get("total"):
                        sizes[progress["digest"]] = progress["total"]
                        assert sum(sizes.values()) < MAX_BYTES - 550_000_000, "Model budget exceeded"
            tags = client.get("/api/tags").json()["models"]
            record = next(m for m in tags if m["name"] == tag)
            detail = client.post("/api/show", json={"model": tag}).json()
            report["models"].append(dict(name=tag, digest=record["digest"], bytes=record["size"], details=record["details"]))
            (STORE / (tag.split(":")[0] + "-LICENSE.txt")).write_text(detail.get("license", ""), encoding="utf-8")
            print("Verified local model manifest: " + tag, flush=True)
    report["whisper"] = whisper_download()
    report["total_artifact_bytes"] = sum(m["bytes"] for m in report["models"]) + sum(f["bytes"] for f in report["whisper"]["files"])
    assert report["total_artifact_bytes"] < MAX_BYTES
    (ROOT / "artifacts/phase7-model-assets.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Approved model artifacts ready; existing models and databases unchanged.", flush=True)


if __name__ == "__main__":
    main()
