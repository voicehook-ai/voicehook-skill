---
name: voicehook-guard
description: Local egress filter for voicehook.ai traffic. Sits on 127.0.0.1:7301, validates endpoint + JSON schema, scans text fields for PII (scrubadub) and secrets (regex). Deny-by-default. Every decision lands in ~/.voicehook/guard-audit.log. Use before invoking voicehook-join if the agent wants to verify that its own outbound is constrained to an auditable pipeline.
license: MIT
metadata:
  author: voicehook.ai
  version: "0.1.0"
---

# voicehook-guard — auditable egress filter for voicehook-join

The guard is a **separate process** that the agent routes its voicehook.ai traffic through. It enforces four things deterministically:

1. **Endpoint allowlist** — only the four `/api/control/*` endpoints voicehook-join actually needs.
2. **JSON schema check** — body must match the registered schema for that endpoint exactly.
3. **Secret scan** — regex against LiveKit/Anthropic/OpenAI/Deepgram/Cartesia key shapes.
4. **PII scan** — scrubadub with `BLOCKING_PII = {email, phone, credit_card, ssn, iban}`.

No ML models. No prompt-injection heuristics. Just deterministic pass/fail.

## Start the guard

```bash
pipx install voicehook-guard     # one-time
voicehook-guard                  # foreground; or use launchd/systemd --user
```

Health check: `curl -s http://127.0.0.1:7301/health`.

## Self-verification before trusting it

Before using the guard on real traffic, probe it:

```bash
# 1. allowlist works
curl -s -X POST http://127.0.0.1:7301/forward \
  -d '{"endpoint":"/not-allowed","payload":{}}' -H content-type:application/json
# → 403 endpoint not in allowlist

# 2. schema works
curl -s -X POST http://127.0.0.1:7301/forward \
  -d '{"endpoint":"/api/control/inject","payload":{"role":"system"}}' \
  -H content-type:application/json
# → 403 schema violation (session_id + text missing)

# 3. secret scan works
curl -s -X POST http://127.0.0.1:7301/forward \
  -d '{"endpoint":"/api/control/peer/say","payload":{"identity":"claude","text":"sk-ant-test123456789012345"}}' \
  -H content-type:application/json
# → 403 blocked: secret:anthropic_key

# 4. audit log appends
tail -5 ~/.voicehook/guard-audit.log
```

If all four behave as above, the guard is doing what it claims.

## Wiring voicehook-join through the guard

Set `VOICEHOOK_CONTROL=http://127.0.0.1:7301/forward` and the hook script posts the envelope `{endpoint, payload}` instead of calling voicehook.ai directly. The guard forwards on success, returns `403` on deny.

Firewall-level enforcement (optional, recommended):

```bash
# macOS: PF rule to drop direct outbound from agent processes to voicehook.ai
# Linux: nftables equivalent
# Only the guard process is whitelisted to reach voicehook.ai:443
```

With that firewall rule in place, the agent **cannot** bypass the guard even if its skill is compromised — it simply has no route.

## What this skill will NEVER do

- install hooks into `.claude/settings.local.json` — that's `voicehook-join`, not this
- make outbound calls on its own — it only starts the guard process
- forward to any host other than `VOICEHOOK_UPSTREAM` (default `https://voicehook.ai`)
- ship an ML model — if a future version adds one, it ships as an opt-in separate package

## Audit log format

One JSON line per decision at `~/.voicehook/guard-audit.log`:

```json
{"ts":"2026-04-21T07:35:12Z","endpoint":"/api/control/inject","decision":"allow","reasons":["status:200"]}
{"ts":"2026-04-21T07:35:14Z","endpoint":"/api/control/peer/say","decision":"deny","reasons":["secret:anthropic_key"]}
```

The user can `tail -f` this to watch the filter in action.
