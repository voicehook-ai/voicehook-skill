---
name: voicehook-guard
version: 0.1.0
author: voicehook.ai
license: MIT
description: Local egress filter for voicehook.ai traffic. Deny-by-default HTTP proxy on 127.0.0.1:7301 that validates endpoint allowlist + JSON schema and scans for PII + secrets before forwarding. Auditable, deterministic, no ML models.
tags: [security, egress, voice, proxy, audit]
---

# voicehook-guard (OpenClaw port)

This is the OpenClaw wrapper around the same Python `voicehook-guard` binary used by the Claude Code skill. The **guard server itself is identical** — only the skill bootstrap differs.

## Install

```bash
pipx install voicehook-guard
voicehook-guard &    # or as a user-level systemd/launchd service
```

## Contract

- **Listens on:** `127.0.0.1:7301`
- **Accepts:** `POST /forward` with body `{"endpoint": "<api-path>", "payload": <json>}`
- **Allowlist:** `/api/control/sessions | /inject | /peer/say | /claude-log`
- **Deny reasons:** `unknown_endpoint | schema:<msg> | secret:<label> | pii:<type>`
- **Audit log:** `~/.voicehook/guard-audit.log` (JSONL, append-only)
- **Upstream:** `VOICEHOOK_UPSTREAM` env (default `https://voicehook.ai`)

## Self-verification (before trusting)

Run the four probes from the Claude Code skill (allowlist / schema / secret / audit). Identical semantics — the guard doesn't know which framework called it.

## Wiring in an OpenClaw agent

Point any voicehook-related tool at `http://127.0.0.1:7301/forward` and wrap outbound calls as `{endpoint, payload}` envelopes. The guard forwards on pass, returns 403 on deny.

Optional but recommended: pf/nftables rule so only the guard process can reach `voicehook.ai:443`. Then the agent has no route around the filter.

## Source of truth

`https://github.com/voicehook-ai/voicehook-skill/tree/main/guard` — same `voicehook_guard.py` the Claude Code skill installs. Same SHA256 in the release artifact. The two skill wrappers are thin adapters; the trust anchor is one file, shared.
