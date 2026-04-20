# Security model

The voicehook-skill client is designed to be auditable by a sceptical AI agent in under 5 minutes. This document gives you everything you need for that audit.

## Threat model

**Assumption:** An AI agent running this skill has access to your files, shell, and tool outputs. If the skill misbehaved, it could exfiltrate anything the agent touches.

**Threats we defend against:**

1. **Accidental data leak.** Agent runs `Bash(cat .env)`, hook relay forwards the content somewhere. → *Mitigation:* hook only forwards tool **name** + one short field (max 80 chars), never the full `tool_input` and never `tool_output`.
2. **Malicious config injection.** A rogue SKILL.md convinces the agent to write exfiltration hooks. → *Mitigation:* this repo's SKILL.md is open, signed, and versioned. Agents should refuse to install an unsigned or modified variant.
3. **Silent state change.** Hooks get installed without user knowledge. → *Mitigation:* install flow requires explicit diff-preview of the JSON that lands in `settings.local.json`, and `delta.py uninstall` reverses every change.
4. **Supply-chain.** Someone pushes a malicious version to the repo. → *Mitigation:* tags from `v0.2.0` are signed (`git tag -s`). GitHub Actions runs `gitleaks` and dependency audits on every PR.

**Threats we do NOT defend against (and why):**

- The managed backend could, in theory, log your peer-events to disk. → Published data-retention policy says "in-memory only, session-lifetime TTL" — self-host the backend if you need hard proof.
- Someone with shell access on your machine reading `settings.local.json`. → Out of scope; that's an OS-level concern.
- The voice-agent (LLM) saying something harmful after a user's prompt. → That's an LLM-behavior concern, not a skill-security concern. The system-prompt in the managed service enforces identity + language + brevity rules.

## What leaves your machine

| Artefact | Sent by default? | Can be sent if opt-in? | Redaction applied |
|---|---|---|---|
| Tool name (e.g. `Bash`, `WebFetch`) | ✅ yes | — | — |
| Tool input — `command`, `url`, `path`, `description` | ✅ yes (first 80 chars) | — | pattern-filtered at boundary (see below) |
| Full `tool_input` object | ❌ no | configurable env flag `VOICEHOOK_FULL_INPUT=1` | same |
| Tool output (stdout/stderr/file contents) | ❌ no | configurable env flag `VOICEHOOK_TOOL_OUTPUT=1` | same |
| File contents | ❌ no | ❌ never (read files are never the hook payload; only their path) | — |
| Environment variables | ❌ no | ❌ never | — |
| Stdin to agent | ❌ no | ❌ never | — |
| User's voice audio | ✅ yes (to LiveKit SFU, not this client) | — | — |

## Redaction at the boundary

Before any string leaves via `/peer/say` or `/claude-log`, `voicehook_hook.py` applies this filter (planned for v0.3 — currently only length-cap is enforced; see `CHANGELOG.md`):

```python
REDACT_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{16,}",              # OpenAI-style keys
    r"sk-ant-api\d{2}-[\w-]+",             # Anthropic
    r"AKIA[0-9A-Z]{16}",                   # AWS access keys
    r"ghp_[A-Za-z0-9]{36}",                # GitHub PAT
    r"ghs_[A-Za-z0-9]{36}",                # GitHub server token
    r"xox[baprs]-[A-Za-z0-9-]+",           # Slack
    r"(?i)bearer\s+[A-Za-z0-9._-]+",       # Any Bearer token
    r"[A-Za-z0-9+/]{40,}={0,2}",           # Long base64 blobs
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
]
```

Matches are replaced with `[REDACTED]` before the POST. Set `VOICEHOOK_DEBUG=1` to log the redacted payload locally (to `/tmp/voicehook_debug.log`) without actually sending it.

## Hook-install — what the agent will propose

When the voicehook-join skill installs hooks, it generates this JSON fragment and shows it to the user for approval:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash|WebFetch|WebSearch|Task",
        "hooks": [
          {
            "type": "command",
            "command": "env VOICEHOOK_PEER_ID=claude VOICEHOOK_CONTROL_URL=https://voicehook.ai/api/control ~/bin/voicehook_hook.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "env VOICEHOOK_PEER_ID=claude VOICEHOOK_CONTROL_URL=https://voicehook.ai/api/control ~/bin/voicehook_hook.py"
          }
        ]
      }
    ]
  }
}
```

Only tools in the `matcher` regex trigger the hook. `Read`, `Edit`, `Write`, `Grep`, etc. never fire — they would spam the call and potentially leak file content.

**Do NOT** set `VOICEHOOK_PEER_SPEAK_STOP=1` in a normal setup. That would cause every task-end to be spoken aloud into the room (noisy). It exists only for explicit debug sessions.

## Uninstall

```bash
# Remove hooks + env vars
~/bin/delta.py uninstall

# Or manually:
# - Delete the hooks.PostToolUse and hooks.Stop blocks from .claude/settings.local.json
# - unset VOICEHOOK_PEER_ID VOICEHOOK_CONTROL_URL
# - (optional) rm ~/bin/voicehook_hook.py ~/bin/delta.py
```

`delta.py uninstall` is idempotent — run it multiple times, it only removes what's actually there.

## Reporting vulnerabilities

Email `security@voicehook.ai` — PGP key at `https://voicehook.ai/.well-known/security.txt`.

We aim for 24-hour acknowledgement and 14-day fix for exploitable client-side issues. See the public `security.txt` for the full disclosure policy.
