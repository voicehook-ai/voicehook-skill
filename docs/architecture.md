# Architecture

How the voicehook-skill client interacts with the voicehook.ai backend — and what you can self-host if you want to.

## Data flow

```
┌───────────────┐        audio           ┌──────────────────┐
│  Human        │ ─────────────────────→ │  LiveKit SFU     │
│  (Browser)    │ ←───────────────────── │  voicehook.ai    │
│  Mic+Speaker  │                        │  (DACH, EU)      │
└───────────────┘                        └─────────┬────────┘
                                                   │ audio
                                                   ↓
                                         ┌──────────────────┐
                                         │  Voice-Agent     │
                                         │  STT → LLM → TTS │
                                         │  (server-side)   │
                                         └─────────┬────────┘
                                                   │ session events
                                                   ↓
                                         ┌──────────────────┐
                                         │  Control Plane   │
                                         │  /api/control/*  │
                                         └─────────┬────────┘
                                                   │ HTTPS
                                                   ↓
                                         ┌──────────────────┐
                                         │  YOUR AGENT      │
                                         │  (this skill)    │
                                         │  Claude/ChatGPT  │
                                         └──────────────────┘
```

The skill runs inside your local agent context (Claude Code terminal, ChatGPT Action, LangChain worker — anywhere HTTPS is available). It never touches LiveKit directly; everything goes through the control plane.

## What this client sends

All requests go to exactly these endpoints. See [`payload-spec.yaml`](payload-spec.yaml) for exact schemas.

| Endpoint | When | Payload |
|---|---|---|
| `GET /api/control/sessions` | Pre-flight check; every cron tick | none |
| `POST /api/control/inject` | Once at join (handover) + rare corrections | `{session_id, role, text, mode, system_mode}` |
| `POST /api/control/peer/say` | Greeting + active interventions | `{session_id, identity, text, speak}` |
| `POST /api/control/claude-log/{sid}` | Optional: push tool events | `{tool, detail, text, identity}` |
| `GET /api/control/claude-log/{sid}` | Read own log | query: `?n=N` |
| `GET /api/control/transcript/{sid}` (WebSocket) | Tail user/assistant events | streams JSON events |

**Nothing else.** No telemetry, no analytics, no silent pings.

## What the hook relay sends per tool call

`cli/voicehook_hook.py` fires on Claude Code's `PostToolUse` and `Stop` events. For each:

1. Reads event JSON from stdin
2. Extracts `tool_name` + **one short informative field** (max 80 chars):
   - `command` for Bash
   - `url` for WebFetch / WebSearch
   - `description` for Task
3. POSTs `{identity: <VOICEHOOK_PEER_ID>, text: "<tool> done  <hint>", speak: false}` to `/peer/say`

File contents, full tool outputs, complete commands — none of this leaves the machine via the default hook. See [`security.md`](security.md) for the full field-by-field breakdown.

## What the control plane does with that

The server (proprietary, not in this repo):

1. Receives peer-events, stores nothing on disk — pushed to an in-memory ring buffer per session (max 50 events, session-lifetime TTL).
2. Broadcasts to the transcript WebSocket so the browser shows the event live.
3. Optionally announces via TTS into the room audio track if `speak: true` was set.

When the LiveKit room closes, the buffer is garbage-collected. There is no persistent log unless the user explicitly enables session recording in the dashboard (which is not something this skill can trigger).

## Self-hosting the backend

You can run your own backend by pointing `VOICEHOOK_CONTROL_URL` at it:

```bash
export VOICEHOOK_CONTROL_URL=https://your-voicehook-backend.example.com/api/control
```

Requirements for a compatible backend:

- LiveKit SFU (self-host via [livekit.io](https://livekit.io) or managed LiveKit Cloud)
- A voice-agent job (LiveKit Agents SDK v1.x with STT/LLM/TTS plugins of your choice)
- An HTTP service exposing the endpoints in [`payload-spec.yaml`](payload-spec.yaml)
- TLS termination in front of the control plane

The open-source client works against any backend that implements the spec — but **most of the value** (tuned personas, DACH-DSGVO hosting, DPA, multi-region failover, persona marketplace) is in the managed service at `voicehook.ai`. Self-hosting is viable for tinkerers and compliance-restricted deploys; it is not the happy path.

## Moat (why this open repo doesn't hurt voicehook.ai)

| Layer | Hosted at voicehook.ai | In this repo |
|---|---|---|
| Client skill + hook | — | ✅ open |
| OpenAPI spec | — | ✅ open |
| Provider accounts (Deepgram, Cartesia, Gemini) | proprietary | — |
| Persona-templates + prompt-craft | proprietary | — |
| Voice-agent orchestration code | proprietary | — |
| Billing + auth + dashboard | proprietary | — |
| Infrastructure (Terraform, Caddy, SFU) | proprietary | — |
| DSGVO + DPA + SLA | proprietary | — |
| Brand, domain, `∆` mark | trademarked | — |

Cloning this repo gives you a client. Running it against your own infra means reproducing the proprietary 80% first. That's the moat.
