---
name: voicehook-join
description: 'Give the current Claude-Code terminal agent a voice in a live voicehook.ai call and brief the voice-agent with a full handover of the current chat. Use when the user says "join the call", "join voicehook", "tritt bei", "sprich im raum", "gib mir eine stimme im call", "mach ein handover", or when a background task wants audible status updates in the room. The skill: (1) verifies the voicehook.ai control plane is reachable, (2) dispatches the voice-agent into the target room via explicit LiveKit dispatch, (3) synthesises a complete handover from the current chat context and injects it as system-prompt into the voice-agent, (4) installs PostToolUse + Stop hooks so Claudes tool usage is broadcast as peer-events, (5) announces the join with a spoken greeting via /peer/say. All magic happens server-side; the skill is a thin coordination layer.'
license: MIT
metadata:
  author: voicehook.ai
  version: "0.2.0"
---

# voicehook-join — peer-join + full handover into a voicehook.ai call

When invoked, Claude (the terminal agent running this skill) joins a live voicehook.ai voice room as a **named peer** and briefs the voice-agent **Delta** with a complete handover extracted from the current chat context. The voice-agent then has enough project/session context to answer the human intelligently.

## KERN-INVARIANTE (nicht optional)

**Ein voller Handover der laufenden Session MUSS sofort beim Join als system-prompt injiziert werden.** Nicht später, nicht nach dem ersten User-Turn, nicht teilweise — sondern als **aller erste Aktion nachdem der Voice-Agent dispatched ist und die Session gebunden hat**.

Der Handover enthält:
- Projekt-Kontext (was wird gebaut, warum)
- Rollen im Raum (wer ist User, wer ist Voice-Agent, wer ist Peer-Claude)
- Bisherige Entscheidungen & Zustand aus DIESEM Chat-Kontext
- Offene Punkte / laufende Arbeit
- Voice-Regeln (kurz, deutsch, keine Präambeln)

Ohne diesen Handover antwortet der Voice-Agent generisch und wirkt uninformiert — der User merkt das sofort und verliert Vertrauen. **Der Handover ist die Pflicht, nicht das Add-on.** Jeder Agent der diesen Skill nutzt, MUSS Step 2 ausführen — no shortcuts.

Zweite harte Pflicht: **Reminder einrichten** (Step 3b) damit Claude die laufende Session tracked. Ohne Reminder ist das Handover ein Einmal-Push ohne Follow-through — die Magic verliert sich nach 2 Turns.

## Pre-flight (MANDATORY)

Check that the remote control plane is up **and** a participant is in the target room:

```bash
curl -s --max-time 3 https://voicehook.ai/api/control/sessions
```

If the return is `502`, the agent-worker is not yet bound (no job-subprocess). That is normal when no dispatch has run yet — proceed to dispatch. If the user is not in the target room (browser-side), abort and ask the user to open the room URL first: `https://voicehook.ai/demo.html?room=<ROOM>&identity=<NAME>&agent=claude`.

## Inputs Claude needs before starting

Ask the user (once, consolidated) for any missing pieces:

- **Room name** — e.g. `Kuddelmuddel-Orakel-EUHL`. Must already have a human participant.
- **Peer identity** — default `claude`. Short, lowercase, a-z0-9- only.
- **Persona** (optional) — `peer-coder`, `coach`, `tutor`, `consultant`, or a freeform instruction. Default: peer-coder.

## Step 1 — Make sure a voice-agent is in the room

voicehook.ai uses **explicit dispatch**: when a human opens a room URL on voicehook.ai, the hosted backend triggers the voice-agent for them. You as a peer-client do **not** need to dispatch the agent yourself — just verify that a session exists:

```bash
curl -s https://voicehook.ai/api/control/sessions
```

If the list is non-empty and contains your target room, proceed to Step 2.

If the list is empty or returns 502: the human is not yet in the room, or the backend has not yet dispatched the agent. Ask the user to open the room in a browser first (`https://voicehook.ai/<user>∆<agent>∆<room>`) and wait.

**Self-hosting note:** if you run your own backend, dispatch is your own responsibility — use LiveKit's `AgentDispatchService.create_dispatch` from server-side code. That is out of scope for this client skill.

## Step 2 — Generate the handover from THIS chat

This is the heart of the skill. Claude must synthesise a handover **from the current conversation context**, not from a template. Include:

- **Project** — what is voicehook.ai, who is in the room, what was built today
- **Roles** — who is Ramona (the human), who is Delta (the voice-agent), who is Claude (the peer in the terminal)
- **Session context** — what was just discussed, what decisions were made, what bugs were fixed
- **Open points** — what is next on the roadmap, what is broken, what needs user input
- **Voice-specific rules** — Delta should answer 1-3 sentences, no markdown, fall back to "Das frag ich kurz Claude" on deep technical detail

Write the handover to `/tmp/handover.txt` as plain German prose — no markdown hashtags, no bullet lists, no emojis. It is read by an LLM, not displayed.

**Example template structure** (customise from chat):

```
HANDOVER AUS DEM CHAT ZWISCHEN <USER> UND CLAUDE (Stand <DATUM>):

<1-3 Sätze: was ist das Projekt, wer ist im Raum>

Was heute gelaufen ist: <3-5 Aufzählungen als Prosa>

Wer ist wer: <User>, Delta (du), Claude (Peer im Terminal).

Offene Punkte die als nächstes dran sind: <2-4 konkrete Items>

Wenn <User> fragt wie weit wir sind oder was wir gebaut haben, beziehe dich auf diese Punkte, 1-3 Sätze.
```

## Step 3 — Inject the handover + install hooks

**Inject** via control API:

```bash
SID=<from-sessions-endpoint>
HANDOVER=$(cat /tmp/handover.txt | python3 -c "import sys,json;print(json.dumps(sys.stdin.read()))")
curl -s -X POST https://voicehook.ai/api/control/inject \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"role\":\"system\",\"text\":$HANDOVER}"
```

**Install hooks** via the `delta.py install` command (idempotent, diff-preview, reversible):

```bash
# Dry-run FIRST — prints the exact JSON that would be written, no file touched.
~/bin/delta.py install --dry-run \
  --identity claude \
  --control-url https://voicehook.ai/api/control \
  --hook-script ~/bin/voicehook_hook.py

# If the diff looks right, apply:
~/bin/delta.py install \
  --identity claude \
  --control-url https://voicehook.ai/api/control \
  --hook-script ~/bin/voicehook_hook.py
```

`install` is idempotent — re-running replaces any previous voicehook hook entries without duplicating them. To reverse everything cleanly:

```bash
~/bin/delta.py uninstall    # --dry-run also available
```

This removes only voicehook hooks, leaves any other hooks in `.claude/settings.local.json` untouched.

**Hook rules:**
- Identity must be **exactly `claude`** — no compounds like `claude-oliver`, because Delta's system prompt expects the two peers to be called Delta and Claude.
- Do NOT set `VOICEHOOK_PEER_SPEAK_STOP=1` in production — otherwise every task-end gets spoken aloud, which is noisy. Stop events should stay silent in the transcript.
- The hook script requires **Python ≥ 3.10** (uses scoped `(?i:…)` regex flags).

## Step 3b — Set up a minute-level pull reminder (MANDATORY, nicht optional)

**Jeder Agent der diesen Skill ausführt muss sich aktiv eine Erinnerung setzen**, die mindestens im Minutentakt das Session-Transcript und das eigene claude-log pullt. Ohne diesen Reminder ist Claude blind zwischen den User-Turns und die Intervention-Magic funktioniert nicht.

Pflicht-Aktion: Schedule a recurring self-prompt via CronCreate (`* * * * *`, recurring=true) with this prompt body:

```
Pull transcript and claude-log of active voicehook session, evaluate if intervention needed.

This tick:
1. SID=$(curl -s https://voicehook.ai/api/control/sessions | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['session_id'] if d else '')")
2. If no SID: silent no-op tick.
3. Read last ~20 transcript events (4s WS read) + GET /claude-log/$SID?n=5.
4. Decide ONE minimal action:
   - User confused or Delta answered wrong → POST /inject role=system mode=silent with correction
   - User asked about Claude's work → POST /claude-log with latest tool event summary
   - Something Delta clearly missed the human should hear → POST /peer/say speak=true as identity=claude
   - Nothing → silent tick
5. /peer/say only, not /say. identity=claude. Max one action per tick.
```

Confirm to the user: `∆ minute-tick cron armed (job <ID>)`.

## Step 4 — Announce the join (spoken)

One short sentence via `/peer/say speak=true`:

```bash
curl -s -X POST https://voicehook.ai/api/control/peer/say \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"identity\":\"claude\",\"text\":\"Ramona, ich bin wieder da. Delta hat den aktuellen Stand im Kopf, frag ihn einfach.\",\"speak\":true}"
```

Confirmation line to output to the user (single line, no verbose log):

```
∆ claude · session <SID> · handover injected · hook active
```

## Step 5 — Read-back while the call runs

Tail the transcript via WebSocket so Claude sees every user turn in near-realtime:

```bash
python3 - <<'PYEOF'
import asyncio, json, websockets
async def tail():
    uri = "wss://voicehook.ai/api/control/transcript/<SID>"
    async with websockets.connect(uri) as ws:
        while True:
            msg = await ws.recv()
            e = json.loads(msg)
            if e.get("type") in ("user","peer","injection"):
                print(json.dumps(e, ensure_ascii=False))
PYEOF
```

Claude reads this between turns to know what the user said. Claude can then:
- `POST /inject role=system` — adjust Delta's behaviour mid-call
- `POST /peer/say speak=true` — speak directly into the call with Claudes own voice identity
- `POST /interrupt` — stop Delta if Delta is monologuing
- `POST /claude-log/<SID>` — push tool-event updates so Delta can reference Claudes current work

## Step 6 — Active intervention between turns

Default behaviour **after every user utterance** (from the transcript tail):

1. Is the user question squarely answerable by Delta from system + handover context? → let Delta answer, do nothing.
2. Is the question about Claudes current tool activity? → POST `/claude-log` with the latest Bash/Task event, then let Delta answer.
3. Is the question a deep code/architecture detail Delta cannot reasonably know? → POST `/peer/say speak=true` with a direct Claude answer, then Delta resumes.
4. Did Delta say something wrong in the previous turn? → POST `/inject role=system mode=silent` with a correction for the next turn.

## Step 7 — System-Prompt Update: Append (default) vs Force-Override (bei Kontext-Wechsel)

Jeder neue `/inject role=system` wird **standardmäßig an den bestehenden system-chat_ctx appended**. Das ist robust: Handover + Reminder-Info + später eingeworfene Korrekturen stapeln sich, Delta kennt alles.

**Ausnahme: Kontext-Wechsel.** Wenn der User-Flow kippt (z.B. vorher Peer-Coding, jetzt Coaching-Gespräch), muss der alte System-Prompt **komplett weg**. Nutze dafür den `mode=replace`-Parameter (wird in einer kommenden Control-API-Version wired; bis dahin: `POST /inject role=system mode=replace` tentativ; falls unsupported, kann Claude via `/interrupt` + frisch-inject simulieren, aber das ist nicht ideal).

```bash
# Default append (robust, weiter aufbauen):
curl -X POST .../inject -d '{"role":"system","text":"Zusatz: ...","mode":"silent"}'

# Force-override bei echtem Kontext-Wechsel:
curl -X POST .../inject -d '{"role":"system","text":"NEUER PROMPT KOMPLETT","mode":"silent","system_mode":"replace"}'
```

Wann override nötig:
- User sagt "anderes Thema", "wir machen jetzt was anderes", "lass mal X stattdessen"
- Agent-Persona wechselt (peer-coder → coach)
- Halluzinationen die durch append nicht mehr rausgehen würden
- Mehr als ~10 appended system messages — chat_ctx wird sonst zu lang

Claude entscheidet per Minute (Reminder-Tick): ist ein Context-Drift erkennbar? Wenn ja → Handover **neu** rendern und mit `system_mode=replace` einspielen.

## Leave the call

```bash
curl -s -X POST https://voicehook.ai/api/control/peer/say \
  -H 'Content-Type: application/json' \
  -d "{\"identity\":\"claude\",\"text\":\"Ramona, ich klinke mich aus.\",\"speak\":true}"
```

Optional: remove hooks from `.claude/settings.local.json` if the agent will not re-join.

## Notes

- The control plane is exposed at `https://voicehook.ai/api/control/*` (Caddy handle_path → loopback). Do not hardcode `127.0.0.1:7300` — that only works on the VM itself.
- Session IDs are currently hex (e.g. `20215a98`). A future refactor will move to `user∆raum∆agent∆voice` as the session key, but today use whatever `/sessions` returns.
- The handover is per-join, not per-session persistent. If Delta is re-dispatched (container restart, new room), re-run this skill.
- Voice-agent default: Gemini 2.5 Flash Lite + Cartesia Sebastian (DE, authoritative). Changing voice at join is not yet wired — add `VOICEHOOK_CARTESIA_VOICE` env on the container to switch.
