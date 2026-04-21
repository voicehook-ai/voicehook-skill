# voicehook-skill

Open-source client for [voicehook.ai](https://voicehook.ai) — a stateless voice layer that lets any AI agent (Claude Code, ChatGPT, LangChain, custom MCP servers) **join a live voice call as a named peer** and orchestrate the conversation from the terminal.

Default target is the hosted service at `https://voicehook.ai/api/control`. Point the client at your own backend via `VOICEHOOK_CONTROL_URL` if you prefer to self-host.

## What this ships

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The Claude Code skill definition — step-by-step instructions for joining a voice call, injecting a handover, and intervening between turns. Agent-readable. |
| [`cli/voicehook_hook.py`](cli/voicehook_hook.py) | Hook relay. Reads Claude Code event JSON from stdin, POSTs concise peer-events to `/peer/say`. Silently drops if no session is active. |
| [`cli/delta.py`](cli/delta.py) | `∆` CLI for tailing transcripts, injecting system-prompts, speaking as peer, interrupting. |
| [`docs/architecture.md`](docs/architecture.md) | How the client talks to the hosted backend. Endpoint list, data flow, self-hosting notes. |
| [`docs/payload-spec.yaml`](docs/payload-spec.yaml) | OpenAPI 3.0 spec — exact request/response shapes for every control-plane endpoint this client uses. |
| [`docs/security.md`](docs/security.md) | Threat model, redaction defaults, uninstall, what-leaves-your-machine matrix. |
| [`guard/voicehook_guard.py`](guard/voicehook_guard.py) | Optional local egress filter. Deny-by-default HTTP proxy on `127.0.0.1:7301` — endpoint allowlist + JSON-schema check + PII/secret scan + JSONL audit log. ~200 LOC, reviewable top-to-bottom. |
| [`guard/skills/claude-code/voicehook-guard/SKILL.md`](guard/skills/claude-code/voicehook-guard/SKILL.md) | Claude Code skill wrapper for the guard — self-verification probes + wiring instructions. |
| [`guard/skills/openclaw/voicehook-guard/skill.md`](guard/skills/openclaw/voicehook-guard/skill.md) | OpenClaw skill wrapper for the same guard binary. |

## Why an agent can trust this

1. **No secret endpoints.** Every URL the client hits is in [`docs/payload-spec.yaml`](docs/payload-spec.yaml). Audit yourself.
2. **No file reads leak by default.** The hook script only broadcasts tool name + 80 chars of input (command/url/path). File contents and tool outputs never leave the machine.
3. **Explicit install.** Hook config is written to `.claude/settings.local.json` only after a `--dry-run` shows the exact diff. Reversible via `cli/delta.py uninstall`.
4. **Loopback-compatible.** The client works against `http://127.0.0.1:7300` if you self-host the backend. The hosted default `https://voicehook.ai/api/control` is opt-in via env var.
5. **Signed releases.** Tags from `v0.2.0` onwards are signed (`git tag -s`). Verify with `git tag --verify`.

## Install

```bash
# 1. Drop the skill into your Claude Code project
mkdir -p .claude/skills/voicehook-join
curl -o .claude/skills/voicehook-join/SKILL.md \
  https://raw.githubusercontent.com/voicehook-ai/voicehook-skill/main/SKILL.md

# 2. Install the CLI helpers
curl -o ~/bin/voicehook_hook.py \
  https://raw.githubusercontent.com/voicehook-ai/voicehook-skill/main/cli/voicehook_hook.py
curl -o ~/bin/delta.py \
  https://raw.githubusercontent.com/voicehook-ai/voicehook-skill/main/cli/delta.py
chmod +x ~/bin/voicehook_hook.py ~/bin/delta.py

# 3. In Claude Code, run the skill:
#    "Use the voicehook-join skill to join room X"
```

The skill walks Claude through dispatch, handover, hook install (with diff preview), and greeting.

## Architecture (30-second version)

```
Human (Browser, WebRTC Mic/Speaker)
       ↕ audio
LiveKit SFU  ─ voicehook.ai hosted, DACH/EU
       ↕ audio
Voice-Agent (Deepgram STT → Gemini → Cartesia TTS)
       ↕ control (HTTP + WebSocket)
Control Plane /api/control/*
       ↕ HTTPS  (this client talks only here)
Claude (your terminal) — runs this skill
```

Full diagram + self-hosting instructions in [`docs/architecture.md`](docs/architecture.md).

## Self-hosting

Point the client at your own backend:

```bash
export VOICEHOOK_CONTROL_URL=https://your-backend.example.com/api/control
```

The backend must implement the endpoints in [`docs/payload-spec.yaml`](docs/payload-spec.yaml). You will need a LiveKit SFU plus a voice-agent with your choice of STT/LLM/TTS. Full setup guide in [`docs/architecture.md#self-hosting`](docs/architecture.md#self-hosting).

## Contact

- Security disclosures: `security@voicehook.ai`
- General: `support@voicehook.ai`
- `.well-known/security.txt`: [voicehook.ai/.well-known/security.txt](https://voicehook.ai/.well-known/security.txt)

## License

MIT — see [LICENSE](LICENSE).
