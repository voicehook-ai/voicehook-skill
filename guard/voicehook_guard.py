"""voicehook-guard — local egress filter for agents joining voicehook.ai.

Design rules:
  - deny by default: unknown endpoints / unknown JSON shapes are blocked
  - deterministic: no ML models, no calls beyond voicehook.ai
  - auditable: every decision lands in audit.log, one line per event
  - reviewable: keep this file under ~200 lines

Pipeline per request:
  1. endpoint must be in ALLOWED_ENDPOINTS
  2. body must match the schema registered for that endpoint
  3. text fields are scanned for secrets (regex) and PII (scrubadub)
  4. if clean: forward to upstream voicehook.ai, return its response
  5. append audit entry either way

Run: `uvx --with fastapi --with scrubadub --with httpx voicehook-guard`
     or `pipx install voicehook-guard && voicehook-guard`
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import scrubadub
from fastapi import FastAPI, HTTPException, Request
from jsonschema import ValidationError, validate

UPSTREAM = os.environ.get("VOICEHOOK_UPSTREAM", "https://voicehook.ai")
AUDIT_PATH = Path(
    os.environ.get("VOICEHOOK_AUDIT_LOG", Path.home() / ".voicehook" / "guard-audit.log")
)
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- Allowed endpoints + schemas -----------------------------------------
# Every downstream call must match exactly one of these. New endpoints
# require an explicit commit to this file — that's the point.

ALLOWED_ENDPOINTS: dict[str, dict[str, Any]] = {
    "/api/control/sessions": {
        "method": "GET",
        "body_schema": None,
    },
    "/api/control/inject": {
        "method": "POST",
        "body_schema": {
            "type": "object",
            "required": ["session_id", "role", "text"],
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "maxLength": 64},
                "role": {"enum": ["system", "user"]},
                "text": {"type": "string", "maxLength": 8000},
                "mode": {"enum": ["silent", "trigger"]},
                "system_mode": {"enum": ["append", "replace"]},
            },
        },
    },
    "/api/control/peer/say": {
        "method": "POST",
        "body_schema": {
            "type": "object",
            "required": ["identity", "text"],
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "maxLength": 64},
                "identity": {"type": "string", "pattern": r"^[a-z0-9-]{1,32}$"},
                "text": {"type": "string", "maxLength": 2000},
                "speak": {"type": "boolean"},
            },
        },
    },
    "/api/control/claude-log": {
        "method": "POST",
        "body_schema": {
            "type": "object",
            "required": ["session_id", "event"],
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "maxLength": 64},
                "event": {"type": "string", "maxLength": 4000},
            },
        },
    },
}

# --- Secret patterns -----------------------------------------------------
# Stack these regexes over every text field; deny on any hit.

SECRET_PATTERNS = {
    "livekit_jwt": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\."),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{40,}"),
    "deepgram_key": re.compile(r"\b[0-9a-f]{40}\b"),
    "cartesia_key": re.compile(r"sk_car_[A-Za-z0-9_-]{20,}"),
    "bearer_header": re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.=]{20,}"),
}

PII_SCRUBBER = scrubadub.Scrubber()
BLOCKING_PII = {"email", "phone", "credit_card", "social_security_number", "iban"}


# --- Scan helpers --------------------------------------------------------

def _collect_text(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _collect_text(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _collect_text(v)]
    return []


def scan_secrets(texts: list[str]) -> list[str]:
    hits = []
    for label, pat in SECRET_PATTERNS.items():
        if any(pat.search(t) for t in texts):
            hits.append(label)
    return hits


def scan_pii(texts: list[str]) -> list[str]:
    hits: set[str] = set()
    for t in texts:
        for f in PII_SCRUBBER.iter_filth(t):
            if f.type in BLOCKING_PII:
                hits.add(f.type)
    return sorted(hits)


# --- Audit ---------------------------------------------------------------

def audit(endpoint: str, decision: str, reasons: list[str]) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": endpoint,
        "decision": decision,
        "reasons": reasons,
    }
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# --- App -----------------------------------------------------------------

app = FastAPI(title="voicehook-guard", version="0.1.0")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "upstream": UPSTREAM, "audit": str(AUDIT_PATH)}


@app.post("/forward")
async def forward(request: Request) -> Any:
    body = await request.json()
    endpoint = body.get("endpoint", "")
    payload = body.get("payload")

    spec = ALLOWED_ENDPOINTS.get(endpoint)
    if not spec:
        audit(endpoint, "deny", ["unknown_endpoint"])
        raise HTTPException(403, f"endpoint not in allowlist: {endpoint!r}")

    if spec["body_schema"] is not None:
        try:
            validate(instance=payload, schema=spec["body_schema"])
        except ValidationError as e:
            audit(endpoint, "deny", [f"schema:{e.message}"])
            raise HTTPException(403, f"schema violation: {e.message}") from e

    texts = _collect_text(payload)
    reasons: list[str] = []
    reasons += [f"secret:{x}" for x in scan_secrets(texts)]
    reasons += [f"pii:{x}" for x in scan_pii(texts)]
    if reasons:
        audit(endpoint, "deny", reasons)
        raise HTTPException(403, {"blocked": reasons})

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.request(
            spec["method"],
            UPSTREAM + endpoint,
            json=payload if spec["method"] != "GET" else None,
        )
    audit(endpoint, "allow", [f"status:{resp.status_code}"])
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text


@app.get("/audit")
def audit_tail(n: int = 50) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text().splitlines()[-n:]
    return [json.loads(l) for l in lines if l.strip()]


def main() -> None:
    import uvicorn
    port = int(os.environ.get("VOICEHOOK_GUARD_PORT", "7301"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
