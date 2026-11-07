"""Explicit approved download of a pinned, local-only 384-dimensional model."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / ".scratch/semantic-e5"
    manifest = []
    downloaded = 0
    with httpx.Client(timeout=120, follow_redirects=True, trust_env=False) as client:
        info = client.get("https://huggingface.co/api/models/Xenova/multilingual-e5-small?blobs=true")
        info.raise_for_status()
        info = info.json()
        for name in ("onnx/model_quantized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json"):
            item = next(e for e in info["siblings"] if e["rfilename"] == name)
            assert item["size"] < 180*1024**2
            url = f"https://huggingface.co/Xenova/multilingual-e5-small/resolve/{info['sha']}/{name}"
            path = target / Path(name).name
            if path.exists():
                content = path.read_bytes()
            else:
                response = client.get(url)
                response.raise_for_status()
                content = response.content
                downloaded += len(content)
            assert downloaded <= 180*1024**2, "Approved download budget exceeded"
            sha = hashlib.sha256(content).hexdigest()
            if item.get("lfs"):
                assert sha == item["lfs"]["sha256"], "Model checksum mismatch"
            else:
                assert hashlib.sha1(b"blob "+str(len(content)).encode()+b"\0"+content).hexdigest() == item["blobId"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            manifest.append(dict(file=path.name, bytes=len(content), sha256=sha, source=url))
    report = dict(repo="Xenova/multilingual-e5-small", revision=info["sha"], files=manifest,
                  downloaded_bytes=downloaded, local_inference_only=True, credentials_used=False)
    (target / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "artifacts/semantic-assets.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Verified {len(manifest)} model assets; {downloaded/1024**2:.1f} MiB downloaded.")


if __name__ == "__main__":
    main()
