"""Download only the three approved Llama variants into an isolated local store.

No database connection, inference, app configuration or existing Ollama service is
used. Run only after explicit approval of the download budget.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / ".scratch/shared-llama-models/ollama"
REPORT = ROOT / "artifacts/shared_llama_downloads.json"
TAGS = ("llama3.2:1b", "llama3:8b", "llama3.1:8b")


def request_json(url, payload=None, timeout=15):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--small-models-approved',action='store_true',
        help='Explicitly approved Gemma 2 2B / Qwen 3 4B downloads, 4.5 GB budget')
    args=parser.parse_args()
    tags=('gemma2:2b','qwen3:4b') if args.small_models_approved else TAGS
    report_path=ROOT/'artifacts/small_model_downloads.json' if args.small_models_approved else REPORT
    budget=4_500_000_000 if args.small_models_approved else 12_000_000_000
    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama executable not installed")
    sizes = {}
    for tag in tags:
        model, version = tag.split(":")
        manifest = request_json(f"https://registry.ollama.ai/v2/library/{model}/manifests/{version}")
        sizes[tag] = manifest["config"]["size"] + sum(layer["size"] for layer in manifest["layers"])
    free = shutil.disk_usage(ROOT).free
    if sum(sizes.values()) > budget or free < 16 * 2**30:
        raise RuntimeError("Approved download budget or disk safety check failed")
    if not STORE.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Model store outside workspace")
    STORE.mkdir(parents=True, exist_ok=True)
    report = dict(status="starting", model_store=str(STORE.relative_to(ROOT)),
        expected_bytes=sizes, free_disk_bytes_before=free, models=[],
        database_accessed=False, inference_run=False, existing_ollama_modified=False)

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    host = f"127.0.0.1:{port}"
    base = f"http://{host}"
    env = os.environ.copy()
    env.update(OLLAMA_HOST=host, OLLAMA_MODELS=str(STORE), OLLAMA_NO_CLOUD="1",
        OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NUM_PARALLEL="1", OLLAMA_NOPRUNE="1")
    report["loopback_host"] = host
    process = None
    try:
        with (STORE.parent / "download-server.log").open("ab") as log:
            process = subprocess.Popen([executable, "serve"], env=env, stdout=log,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            report["owned_server_pid"] = process.pid
            for attempt in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Isolated download server exited")
                try:
                    request_json(base + "/api/tags", timeout=1)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Isolated download server startup timed out")
            for tag in tags:
                print(f"Downloading {tag} sequentially", flush=True)
                report.update(status="downloading", current_model=tag)
                save()
                payload = json.dumps(dict(model=tag, stream=True)).encode()
                request = urllib.request.Request(base + "/api/pull", data=payload,
                    headers={"Content-Type": "application/json"})
                last_output = 0.0
                succeeded = False
                with urllib.request.urlopen(request, timeout=60) as response:
                    for line in response:
                        event = json.loads(line)
                        if event.get("error"):
                            raise RuntimeError("Model registry pull failed; see isolated download log")
                        succeeded = event.get("status") == "success"
                        if time.monotonic() - last_output >= 15 or succeeded:
                            print(json.dumps(dict(model=tag, status=event.get("status"),
                                completed=event.get("completed"), total=event.get("total"))), flush=True)
                            last_output = time.monotonic()
                if not succeeded:
                    raise RuntimeError("Model pull ended without success")
                inventory = request_json(base + "/api/tags")["models"]
                entry = next(model for model in inventory if model["name"] == tag)
                report["models"].append(entry)
                save()
            report.update(status="completed", free_disk_bytes_after=shutil.disk_usage(ROOT).free)
    except BaseException:
        report["status"] = "incomplete"
        raise
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            report["owned_server_stopped"] = process.poll() is not None
        save()
    print("Approved downloads completed; isolated server stopped; no database access or inference.", flush=True)


if __name__ == "__main__":
    main()
