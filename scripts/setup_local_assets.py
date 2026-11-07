"""Explicit opt-in public model downloads; never called by application startup."""
import argparse
import hashlib
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--verify-local", action="store_true", help="Check the recorded asset hashes without network access.")
    args = parser.parse_args()
    manifest_path = ROOT / "artifacts/local-assets.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"assets": []}
    if args.verify_local:
        assert previous["assets"], "No asset manifest is available."
        for asset in previous["assets"]:
            path = (ROOT / asset["path"]).resolve(strict=True)
            assert path.is_relative_to((ROOT / ".scratch").resolve()), "Unexpected asset path."
            assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"], "Local asset checksum mismatch."
        print(f"Verified {len(previous['assets'])} recorded local hashes without network access.")
        return
    assert args.ocr or args.speech, "Select an explicitly approved asset group."
    target = ROOT / ".scratch"
    manifest = []
    total = 0
    with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
        def download(url, path, expected=None, git_blob=None):
            nonlocal total
            if path.exists():
                content = path.read_bytes()
            else:
                response = client.get(url)
                response.raise_for_status()
                content = response.content
                total += len(content)
            assert total < 100 * 1024 * 1024, "Model downloads exceed the approved asset budget."
            sha = hashlib.sha256(content).hexdigest()
            if expected:
                assert sha == expected, "Model checksum mismatch."
            if git_blob:
                assert hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == git_blob
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            manifest.append(dict(path=str(path.relative_to(ROOT)), bytes=len(content), sha256=sha,
                                 source=url, upstream_sha256=expected, upstream_git_blob=git_blob))
        if args.ocr:
            for language in ("eng", "hin", "kan"):
                response = client.get(f"https://api.github.com/repos/tesseract-ocr/tessdata_fast/contents/{language}.traineddata?ref=4.1.0")
                response.raise_for_status()
                info = response.json()
                download(info["download_url"], target / "tessdata" / (language + ".traineddata"), git_blob=info["sha"])
            # Small OFL fonts are evaluation fixtures, not a system font installation.
            for family, output in (("notosanskannada", "Kannada.ttf"), ("notosansdevanagari", "Hindi.ttf")):
                response = client.get(f"https://api.github.com/repos/google/fonts/contents/ofl/{family}")
                response.raise_for_status()
                entries = response.json()
                entry = next(e for e in entries if e["name"].endswith(".ttf"))
                download(entry["download_url"], target / "fonts" / output, git_blob=entry["sha"])
                license_entry = next(e for e in entries if e["name"] == "OFL.txt")
                download(license_entry["download_url"], target / "fonts" / (family + "-OFL.txt"), git_blob=license_entry["sha"])
        if args.speech:
            response = client.get("https://huggingface.co/api/models/Systran/faster-whisper-tiny?blobs=true")
            response.raise_for_status()
            info = response.json()
            revision = info["sha"]
            for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                entry = next(e for e in info["siblings"] if e["rfilename"] == name)
                download(f"https://huggingface.co/Systran/faster-whisper-tiny/resolve/{revision}/{name}",
                         target / "whisper-tiny" / name, expected=entry.get("lfs", {}).get("sha256"),
                         git_blob=None if entry.get("lfs") else entry.get("blobId"))
        combined = {asset["path"]: asset for asset in previous["assets"]}
        combined.update({asset["path"]: asset for asset in manifest})
        report = dict(assets=list(combined.values()), total_download_bytes=total, credentials_used=False,
                      campus_content_uploaded=False)
        manifest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Verified {len(manifest)} workspace-local assets; {total / 1024**2:.1f} MiB downloaded.")


if __name__ == "__main__":
    main()
