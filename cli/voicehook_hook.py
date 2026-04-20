#!/usr/bin/env python3
"""Claude-Code hook → voicehook peer-message relay.

Registered in .claude/settings.local.json as a hook command; reads the
event JSON from stdin and pushes a concise peer-message to the live
voicehook control-plane so the call hears what this agent is doing.

Env vars (set by the agent's shell or by the Skill's install step):
  VOICEHOOK_PEER_ID      — e.g. claude-research-green  (REQUIRED)
  VOICEHOOK_CONTROL_URL  — default http://127.0.0.1:7300
  VOICEHOOK_PEER_SPEAK   — 1|0; if 1 the event is spoken aloud. Default 0.
  VOICEHOOK_PEER_SPEAK_STOP — 1|0; speak Stop events even when *_SPEAK is 0.
                              Useful to announce only task completion.

Hook event types handled:
  PreToolUse, PostToolUse, Stop, SubagentStop, UserPromptSubmit, Notification

Silently drops if no voicehook session is live (no spam when you're not
in a call).
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

CONTROL = os.getenv("VOICEHOOK_CONTROL_URL", "http://127.0.0.1:7300")
IDENTITY = os.getenv("VOICEHOOK_PEER_ID", "").strip()
SPEAK = os.getenv("VOICEHOOK_PEER_SPEAK", "0") == "1"
SPEAK_STOP = os.getenv("VOICEHOOK_PEER_SPEAK_STOP", "0") == "1"
DEBUG = os.getenv("VOICEHOOK_DEBUG", "0") == "1"

# Secrets never leave the machine. If any pattern matches, the hit is replaced
# with [REDACTED] BEFORE the POST. Extend via VOICEHOOK_REDACT_EXTRA (comma-sep
# regexes). Set VOICEHOOK_DEBUG=1 to log what would be sent (no actual POST).
_REDACT_PATTERNS = [
    r"sk-ant-api\d{2}-[\w-]{20,}",                   # Anthropic
    r"sk-[A-Za-z0-9]{20,}",                          # OpenAI / many
    r"AKIA[0-9A-Z]{16}",                             # AWS access key
    r"ASIA[0-9A-Z]{16}",                             # AWS temp
    r"ghp_[A-Za-z0-9]{36}",                          # GitHub PAT
    r"ghs_[A-Za-z0-9]{36}",                          # GitHub server
    r"github_pat_[A-Za-z0-9_]{22,}",                 # GitHub fine-grained
    r"xox[baprs]-[A-Za-z0-9-]{10,}",                 # Slack
    r"(?i:bearer\s+[A-Za-z0-9._~+/-]{20,})",         # Bearer tokens (scoped ignore-case)
    r"AIza[A-Za-z0-9_-]{35}",                        # Google API key
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END",
]
_extra = os.getenv("VOICEHOOK_REDACT_EXTRA", "")
if _extra:
    _REDACT_PATTERNS.extend([p.strip() for p in _extra.split(",") if p.strip()])
_REDACT_RE = re.compile("|".join(_REDACT_PATTERNS))

def _scrub(text: str) -> str:
    return _REDACT_RE.sub("[REDACTED]", text) if text else text


def _post(path: str, payload: dict) -> bool:
    try:
        req = urllib.request.Request(
            f"{CONTROL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except Exception:
        return False


def _format(event: dict) -> tuple[str | None, bool]:
    """Return (text, speak?) for a given hook event; None to skip."""
    kind = event.get("hook_event_name") or event.get("event") or ""
    speak = SPEAK

    if kind == "PreToolUse":
        name = event.get("tool_name", "?")
        inp = event.get("tool_input", {}) or {}
        # cherry-pick informative bits
        hint = inp.get("file_path") or inp.get("path") or inp.get("command") \
            or inp.get("pattern") or inp.get("url") or ""
        hint = str(hint)[:80]
        return (f"{name}{'  ' + hint if hint else ''}", False)

    if kind == "PostToolUse":
        name = event.get("tool_name", "?")
        # only emit for expensive tools; everything else would spam
        if name in {"Bash", "WebFetch", "WebSearch", "Task", "TaskCreate"}:
            inp = event.get("tool_input", {}) or {}
            hint = inp.get("command") or inp.get("url") or inp.get("description") or ""
            hint = str(hint)[:80]
            return (f"{name} done{'  ' + hint if hint else ''}", False)
        return (None, False)

    if kind == "Stop":
        return ("fertig.", speak or SPEAK_STOP)

    if kind == "SubagentStop":
        return ("Sub-Agent fertig.", speak or SPEAK_STOP)

    if kind == "UserPromptSubmit":
        prompt = event.get("prompt") or event.get("user_message") or ""
        return (f"user → {prompt[:80]}", False)

    if kind == "Notification":
        msg = event.get("message", "")
        return (msg[:100] if msg else None, False)

    return (None, False)


def main() -> int:
    if not IDENTITY:
        # No identity configured — do nothing so the hook never breaks a run.
        return 0
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    text, speak = _format(event)
    if not text:
        return 0
    # Scrub secrets BEFORE the POST. Never send API keys, tokens, private keys.
    text = _scrub(text)
    if DEBUG:
        # Local log-only mode: print what WOULD be sent, don't POST.
        sys.stderr.write(f"[voicehook-debug] identity={IDENTITY} text={text!r} speak={speak}\n")
        return 0
    _post("/peer/say", {
        "identity": IDENTITY,
        "text": text,
        "speak": speak,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
