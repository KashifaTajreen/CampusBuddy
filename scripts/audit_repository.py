"""Audit publishable Git content without printing matched credential values.

This is a bounded local check, not a replacement for GitHub secret scanning.
Ignored environment files are read only to detect accidental copies of local
credentials. They are never included in the output.
"""

import argparse
import base64
import io
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "google-api-key": rb"AIza[0-9A-Za-z_-]{35}",
    "github-token": rb"(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{40,})",
    "aws-access-key": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "supabase-secret-key": rb"sb_secret_[A-Za-z0-9_-]{20,}",
    "provider-key": rb"sk-(?:proj-|or-v1-)[A-Za-z0-9_-]{32,}",
    "google-oauth-secret": rb"GOCSPX-[A-Za-z0-9_-]{20,}",
}
FORBIDDEN = re.compile(
    r"(^|/)(?:\.scratch|\.venv|venv|node_modules|\.next|data|uploads|models|"
    r"playwright-report|test-results)(/|$)|"
    r"(^|/)\.env(?:\.[^/]*)?$|\.(?:db|sqlite|sqlite3|dump|gguf|onnx|safetensors|log)$",
    re.I,
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def local_secrets():
    values = set()
    for path in [*ROOT.glob(".env*"), *ROOT.joinpath("frontend").glob(".env*")]:
        if not path.is_file() or path.name.endswith(".example"):
            continue
        for name, value in dotenv_values(path, interpolate=False).items():
            if not value or name.startswith("NEXT_PUBLIC_") or "PUBLISHABLE" in name or "ANON_KEY" in name:
                continue
            if re.search(r"PASSWORD|SECRET|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|TOKEN$|DATABASE_URL", name):
                if len(value) >= 16 and value not in {"change-me", "replace-me"}:
                    values.add(value.encode())
                if "DATABASE_URL" in name:
                    try:
                        password = urlsplit(value).password
                    except ValueError:
                        password = None
                    if password and len(password) >= 8:
                        for form in {password, unquote(password), quote(unquote(password), safe="")}:
                            values.add(form.encode())
    return values


def inspect_blob(path, blob, secrets, *, historical=False):
    findings = []
    if blob.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for member in archive.infolist():
                    if member.filename in {"word/document.xml", "xl/sharedStrings.xml"} and member.file_size < 20 * 1024 * 1024:
                        text = "".join(ET.fromstring(archive.read(member)).itertext()).encode()
                        findings.extend(inspect_blob(path + "::" + member.filename, text, secrets, historical=True))
        except (zipfile.BadZipFile, ET.ParseError, ValueError):
            findings.append({"path": path, "rule": "archive-inspection-failed"})
    for rule, pattern in RULES.items():
        for match in re.finditer(pattern, blob):
            # Literal regexes in this scanner are not credential material.
            findings.append({"path": path, "rule": rule, "line": blob[:match.start()].count(b"\n") + 1})
    if any(value in blob for value in secrets):
        findings.append({"path": path, "rule": "local-credential-copy"})
    for match in re.finditer(rb"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", blob):
        try:
            part = match[0].split(b".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + b"=" * (-len(part) % 4)))
        except (ValueError, UnicodeError):
            continue
        if claims.get("role") == "service_role":
            findings.append({"path": path, "rule": "embedded-service-role-jwt"})
    for match in re.finditer(rb"postgres(?:ql)?(?:\+psycopg)?://[^\s\"'<>]+", blob):
        try:
            value = urlsplit(match[0].decode())
            password = unquote(value.password or "")
        except (ValueError, UnicodeError):
            continue
        local = value.hostname in {"localhost", "127.0.0.1", "postgres", "db"}
        placeholder = (not password or password.isupper() or
                       any(term in password.casefold() for term in ("placeholder", "encoded_password", "redacted")))
        # Explicit fake inputs in offline scope/error-redaction unit tests.
        synthetic_fixture = path in {"benchmarks/test_campusos_kt_setup.py", "benchmarks/test_kt_migration_gate.py"} and password in {"fake", "synthetic", "private-password"}
        if not local and not placeholder and not synthetic_fixture:
            findings.append({"path": path, "rule": "remote-database-credential-url",
                             "line": blob[:match.start()].count(b"\n") + 1})
    if not historical:
        example = Path(path).name.startswith(".env") and path.endswith(".example")
        if FORBIDDEN.search(path) and not example and path != "uploads/.gitkeep":
            findings.append({"path": path, "rule": "private-or-generated-path"})
        if len(blob) > 10 * 1024 * 1024:
            findings.append({"path": path, "rule": "file-larger-than-10MiB", "bytes": len(blob)})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect the entire Git index instead of candidate files")
    parser.add_argument("--history", action="store_true", help="Also inspect all reachable historical blobs")
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON report (use an ignored path)")
    args = parser.parse_args()
    secrets = local_secrets()
    paths = git("ls-files", "-z", *([] if args.staged else ["--cached", "--others", "--exclude-standard"]))
    findings, sizes = [], []
    for raw in sorted(set(paths.split(b"\0")) - {b""}):
        path = raw.decode("utf-8")
        if args.staged:
            blob = git("show", ":" + path)
        else:
            file = ROOT / path
            if not file.is_file():
                continue
            blob = file.read_bytes()
        findings.extend(inspect_blob(path, blob, secrets))
        sizes.append({"path": path, "bytes": len(blob)})
    history_findings, historical_blobs = [], 0
    if args.history:
        for entry in git("rev-list", "--objects", "--all").decode().splitlines():
            sha, _, path = entry.partition(" ")
            if git("cat-file", "-t", sha).strip() != b"blob":
                continue
            historical_blobs += 1
            for finding in inspect_blob(path, git("cat-file", "blob", sha), secrets, historical=True):
                history_findings.append({**finding, "object": sha})
    report = {
        "mode": "index" if args.staged else "working-tree candidates",
        "files": len(sizes), "total_bytes": sum(item["bytes"] for item in sizes),
        "largest_files": sorted(sizes, key=lambda item: item["bytes"], reverse=True)[:10],
        "findings": findings, "historical_blobs": historical_blobs,
        "history_findings": history_findings,
        "limitations": "High-signal patterns and copies of current local credentials only; not a proof that all secrets are absent.",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return int(bool(findings or history_findings))


if __name__ == "__main__":
    raise SystemExit(main())
