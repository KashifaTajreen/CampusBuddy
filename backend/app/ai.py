"""Optional provider abstraction. Validated grounded output; never executable actions."""

import json
from typing import Protocol
import httpx
from pydantic import BaseModel, Field, ConfigDict
from backend.app.config import settings


class GroundedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=6000)
    source_ids: list[str] = Field(max_length=20)


class Provider(Protocol):
    def synthesize(self, question: str, hits: list[dict]) -> GroundedOutput | None: ...


class GeminiProvider:
    def synthesize(self, question, hits):
        config = settings()
        if not config.gemini_api_key:
            return None
        sources = []
        for h in hits:
            if isinstance(h, dict):
                if h.get("suspicious"):
                    continue
                sid = str(h.get("source_id") or "campus-data")
                txt = h.get("text")
                if not txt:
                    txt = ", ".join(
                        f"{k}: {v}"
                        for k, v in h.items()
                        if k not in ("id", "tenant_id", "owner_id", "source_id", "source_ids", "demo", "suspicious") and v is not None and v != ""
                    )
                if txt:
                    sources.append(dict(source_id=sid, text=str(txt)[:1800]))
        if not sources:
            return None
        instruction = (
            "You are CampusOS AI assistant. Answer the user's question clearly, accurately and conversationally using facts from the supplied passages. "
            "Format your answer cleanly using standard markdown (bullet points, bold highlights for dates and subjects). "
            "Return JSON with 'answer' and 'source_ids' (list of cited source_id strings)."
        )
        try:
            with httpx.Client(timeout=25) as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{config.gemini_model}:generateContent",
                    headers={"x-goog-api-key": config.gemini_api_key},
                    json={
                        "system_instruction": {"parts": [{"text": instruction}]},
                        "contents": [
                            {"parts": [{"text": json.dumps({"question": question, "passages": sources})}]}
                        ],
                        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                    },
                )
                response.raise_for_status()
                raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = GroundedOutput.model_validate_json(raw)
            allowed = {s["source_id"] for s in sources}
            if not result.source_ids:
                result.source_ids = [sources[0]["source_id"]]
            else:
                result.source_ids = [s for s in result.source_ids if s in allowed] or [sources[0]["source_id"]]
            return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return None
