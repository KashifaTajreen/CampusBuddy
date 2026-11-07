"""Original files are private. Clients retrieve through permission-checked API only."""

from urllib.parse import quote
import httpx
from backend.app.config import settings, ROOT


class ObjectStore:
    def __init__(self, config=None):
        self.config = config or settings()

    def local_path(self, key):
        base = (ROOT / "data" / "objects").resolve()
        path = (base / key).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Invalid storage key.")
        return path

    def headers(self):
        key = self.config.supabase_service_role_key
        if not key:
            raise ValueError("Private storage is not configured.")
        return {"Authorization": f"Bearer {key}", "apikey": key}

    def url(self, key):
        return (
            self.config.supabase_url.rstrip("/")
            + "/storage/v1/object/"
            + quote(self.config.supabase_storage_bucket, safe="")
            + "/"
            + quote(key, safe="/")
        )

    def put(self, key, content):
        if self.config.storage_backend == "supabase":
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    self.url(key),
                    headers={
                        **self.headers(),
                        "Content-Type": "application/octet-stream",
                        "x-upsert": "false",
                    },
                    content=content,
                )
                response.raise_for_status()
        else:
            path = self.local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def get(self, key):
        if self.config.storage_backend == "supabase":
            with httpx.Client(timeout=30) as client:
                response = client.get(self.url(key), headers=self.headers())
                response.raise_for_status()
                return response.content
        return self.local_path(key).read_bytes()

    def download(self, service, source_id):
        """User-facing reads authorize before contacting disk or the object provider."""
        source = service.original_source(source_id)
        return source, self.get(source.storage_key)

    def delete(self, key):
        if not key:
            return
        if self.config.storage_backend == "supabase":
            endpoint = self.url("").rstrip("/")
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.request("DELETE", endpoint, headers=self.headers(), json={"prefixes": [key]})
                response.raise_for_status()
        else:
            self.local_path(key).unlink(missing_ok=True)
