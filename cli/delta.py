"""∆ — VoiceHook Control CLI.

Inject system messages, force TTS, interrupt speech, tail transcripts
of live voice calls. Talks to the control server embedded in agent/main.py.

Usage:

    ∆ "sprich langsamer"                    # silent system message (next turn)
    ∆ --as user "hallo, bist du da?"        # simulate user input (triggers reply)
    ∆ --trigger "fasse den call zusammen"   # force LLM reply with extra instruction
    ∆ --say "Hintergrund: Paket kam an"     # direct TTS, added to chat_ctx
    ∆ --say --ephemeral "kurze Notiz"       # TTS without polluting chat_ctx
    ∆ listen                                 # live transcript tail
    ∆ calls                                  # list active sessions
    ∆ interrupt                              # stop current speech
    echo "vergiss alles davor" | ∆           # stdin → system message

Env:
    VOICEHOOK_CONTROL_URL   default http://127.0.0.1:7300
    VOICEHOOK_SESSION       default: latest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

CONTROL_URL = os.getenv("VOICEHOOK_CONTROL_URL", "http://127.0.0.1:7300")
DEFAULT_SESSION = os.getenv("VOICEHOOK_SESSION") or None


def _die(msg: str, code: int = 1) -> None:
    print(f"∆ {msg}", file=sys.stderr)
    sys.exit(code)


def _post(path: str, payload: dict[str, Any]) -> dict:
    try:
        r = httpx.post(f"{CONTROL_URL}{path}", json=payload, timeout=5)
    except httpx.ConnectError:
        _die(f"no control server at {CONTROL_URL} — is the agent running?")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        _die(f"{r.status_code} {detail}")
    return r.json()


def _get(path: str) -> Any:
    try:
        r = httpx.get(f"{CONTROL_URL}{path}", timeout=5)
    except httpx.ConnectError:
        _die(f"no control server at {CONTROL_URL} — is the agent running?")
    if r.status_code >= 400:
        _die(f"{r.status_code} {r.text}")
    return r.json()


def cmd_calls(args: argparse.Namespace) -> None:
    sessions = _get("/sessions")
    if not sessions:
        print("(no active sessions)")
        return
    for s in sessions:
        age = s.get("age_seconds", 0)
        pii = s.get("pii_mode", "?")
        print(f"  {s['session_id']}  room={s['room_name']}  model={s['model']}  pii={pii}  {age:.0f}s")


def cmd_interrupt(args: argparse.Namespace) -> None:
    res = _post("/interrupt", {"session_id": args.session, "force": args.force})
    print(f"∆ interrupted ({res['session_id']})")


def cmd_inject_or_say(args: argparse.Namespace) -> None:
    # Collect text: positional args, stdin, or fail
    text = " ".join(args.text).strip() if args.text else ""
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        _die("no text provided (argument or stdin)")

    if args.status:
        cmd_status(text, args.session)
        return

    if args.say:
        res = _post("/say", {
            "text": text,
            "allow_interruptions": not args.no_interrupt,
            "add_to_chat_ctx": not args.ephemeral,
            "session_id": args.session,
        })
        label = "say (ephemeral)" if args.ephemeral else "say"
        print(f"∆ {label} → {res['session_id']}")
        return

    mode = "trigger" if args.trigger or args.role == "user" else "silent"
    res = _post("/inject", {
        "text": text,
        "role": args.role,
        "mode": mode,
        "session_id": args.session,
    })
    print(f"∆ {args.role}/{mode} → {res['session_id']}")


async def _listen(session_id: str) -> None:
    import websockets
    base = CONTROL_URL.replace("http://", "ws://").replace("https://", "wss://")
    target = session_id or "_latest"
    url = f"{base}/transcript/{target}"
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            print(f"∆ listening on {target}  (Ctrl+C to stop)")
            async for raw in ws:
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    print(raw)
                    continue
                _render_event(ev)
    except (ConnectionRefusedError, OSError):
        _die(f"cannot connect to {url}")


def _render_event(ev: dict) -> None:
    t = ev.get("type", "?")
    text = ev.get("text", "")
    pii_tag = ""
    if ev.get("pii"):
        types = ",".join(e.get("type", "?") for e in ev["pii"])
        pii_tag = f" [pii:{types}]"
    if t == "user":
        print(f"  user  » {text}{pii_tag}")
    elif t == "assistant":
        print(f"  bot   « {text}{pii_tag}")
    elif t == "system":
        print(f"  sys   ‹ {text}")
    elif t == "injection":
        role = ev.get("role", "?")
        mode = ev.get("mode", "?")
        print(f"  ∆ {role}/{mode}  {text}")
    elif t == "say":
        print(f"  say   ◆ {text}")
    elif t == "narration":
        n = ev.get("events", "?")
        print(f"  narr  ∆ ({n} events) {text}")
    elif t == "peer":
        ident = ev.get("identity", "?")
        speak_tag = " 🔊" if ev.get("spoke") else ""
        print(f"  peer  ⟨{ident}⟩ {text}{pii_tag}{speak_tag}")
    elif t == "interrupt":
        print("  interrupt")
    elif t == "close":
        print("  [session closed]")
    else:
        print(f"  {t}  {text}")


def cmd_listen(args: argparse.Namespace) -> None:
    try:
        import websockets  # noqa: F401
    except ImportError:
        _die("missing dep: pip install websockets")
    try:
        asyncio.run(_listen(args.session or ""))
    except KeyboardInterrupt:
        print()


SUBCOMMANDS = {"calls", "listen", "interrupt", "summarize", "peer", "join", "help", "install", "uninstall"}


def cmd_join(args: argparse.Namespace) -> None:
    """Announce a named CLI agent into the live voice room and tail the transcript.

    - Picks the newest session (or the one matching --room).
    - Posts a greeting peer-event so humans + other CLIs see the join.
    - With the default (not --silent) it is spoken once via the live TTS so
      the human in the room hears "X ist jetzt im Raum".
    - Then streams the transcript to stdout until Ctrl+C.
    """
    identity = args.identity.strip()
    if not identity or not all(c.isalnum() or c in "-_" for c in identity):
        _die("join: identity must be alphanumeric + - _, max 40 chars")

    sessions = _get("/sessions")
    if not sessions:
        _die("join: no active sessions — is the agent running and a human in the room?")
    target = None
    if args.room:
        target = next((s for s in sessions if s["room_name"] == args.room), None)
        if not target:
            _die(f"join: no session for room {args.room!r}")
    else:
        target = max(sessions, key=lambda s: s["started_at"])

    greeting = f"{identity} ist jetzt im Raum dabei."
    res = _post("/peer/say", {
        "identity": identity,
        "text": greeting,
        "speak": not args.silent,
        "session_id": target["session_id"],
    })
    print(f"∆ ⟨{identity}⟩ joined {target['room_name']} ({res['session_id']})")
    # Drop into listen mode so the caller sees everything that follows.
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("∆ (hint: pip install websockets for the tail view)")
        return
    try:
        asyncio.run(_listen(target["session_id"]))
    except KeyboardInterrupt:
        print()


def cmd_peer(args: argparse.Namespace) -> None:
    """Post a message as a named CLI peer into the live room.

    Other observers (browser, ∆ listen, any agent on this host) see it
    instantly via the transcript broadcast. With --speak the human in
    the voice call also hears it as synthesized speech, prefixed with
    the peer identity.

        ∆ peer --as research-bot "Hab das Pattern in src/router.ts gefunden"
        ∆ peer --as qa-agent --speak "Tests laufen durch"
    """
    text = " ".join(args.text).strip() if args.text else ""
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        _die("peer: no text provided")
    identity = (args.identity or os.getenv("VOICEHOOK_PEER_ID") or "peer").strip()
    if not identity:
        _die("peer: identity must not be empty")
    res = _post("/peer/say", {
        "identity": identity,
        "text": text,
        "speak": bool(args.speak),
        "session_id": args.session,
    })
    tag = "🔊 spoken" if args.speak else "broadcast"
    print(f"∆ peer ⟨{identity}⟩ {tag} → {res['session_id']}")


def cmd_status(text: str, session_id: str | None) -> None:
    """Push a free-form status event into the session's narrator buffer."""
    res = _post("/status", {"text": text, "session_id": session_id})
    print(f"∆ status → {res['session_id']} (buffered={res.get('buffered', '?')})")


def cmd_summarize(args: argparse.Namespace) -> None:
    """Toggle narrator-loop on a session at runtime.

    ∆ summarize 15            # enable, 15s interval
    ∆ summarize off           # disable
    """
    target = args.value.strip().lower()
    sid = args.session or "_latest"
    if target in ("off", "stop", "0"):
        body = {"interval": 1.0, "active": False}
    else:
        try:
            interval = float(target)
        except ValueError:
            _die(f"summarize: expected seconds or 'off', got '{args.value}'")
        if interval <= 0:
            body = {"interval": 1.0, "active": False}
        else:
            body = {"interval": interval, "active": True}
    res = _post(f"/summarize/{sid}", body)
    state = "ON" if res.get("summarize_active") else "OFF"
    print(f"∆ summarize {state} interval={res.get('interval')}s → {res['session_id']}")


_HOOK_COMMAND_SIGNATURE = "voicehook_hook.py"  # used to find & remove our own hooks


def _build_hook_block(identity: str, control_url: str, hook_script: str, speak_stop: bool = False) -> dict:
    env_base = f"env VOICEHOOK_PEER_ID={identity} VOICEHOOK_CONTROL_URL={control_url}"
    env_stop = f"{env_base} VOICEHOOK_PEER_SPEAK_STOP=1" if speak_stop else env_base
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash|WebFetch|WebSearch|Task",
                    "hooks": [{"type": "command", "command": f"{env_base} {hook_script}"}],
                }
            ],
            "Stop": [
                {
                    "hooks": [{"type": "command", "command": f"{env_stop} {hook_script}"}],
                }
            ],
        }
    }


def cmd_install(args: argparse.Namespace) -> None:
    import pathlib
    settings_path = pathlib.Path(args.settings)
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except Exception as e:
            _die(f"cannot parse {settings_path}: {e}")

    new_hooks = _build_hook_block(args.identity, args.control_url, args.hook_script)["hooks"]
    merged = dict(existing)
    merged_hooks = dict(merged.get("hooks") or {})
    for event, entries in new_hooks.items():
        current = list(merged_hooks.get(event) or [])
        # drop any existing voicehook entries (idempotent reinstall)
        current = [e for e in current if _HOOK_COMMAND_SIGNATURE not in json.dumps(e)]
        current.extend(entries)
        merged_hooks[event] = current
    merged["hooks"] = merged_hooks

    new_text = json.dumps(merged, indent=2, ensure_ascii=False)
    if args.dry_run:
        sys.stderr.write("─── DRY RUN — would write to " + str(settings_path) + " ───\n")
        sys.stderr.write(new_text + "\n")
        sys.stderr.write("─── end dry-run (no changes made) ───\n")
        return
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(new_text + "\n")
    print(f"∆ installed — identity={args.identity} control={args.control_url}")


def cmd_uninstall(args: argparse.Namespace) -> None:
    import pathlib
    settings_path = pathlib.Path(args.settings)
    if not settings_path.exists():
        print(f"∆ nothing to uninstall: {settings_path} does not exist")
        return
    try:
        data = json.loads(settings_path.read_text())
    except Exception as e:
        _die(f"cannot parse {settings_path}: {e}")

    hooks = dict(data.get("hooks") or {})
    removed = 0
    for event, entries in list(hooks.items()):
        kept = [e for e in (entries or []) if _HOOK_COMMAND_SIGNATURE not in json.dumps(e)]
        removed += len(entries or []) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)

    new_text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.dry_run:
        sys.stderr.write(f"─── DRY RUN — would remove {removed} voicehook hook entries from {settings_path} ───\n")
        sys.stderr.write(new_text + "\n")
        return
    settings_path.write_text(new_text + "\n")
    print(f"∆ uninstalled — {removed} voicehook hook entries removed from {settings_path}")


def _build_inject_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="∆",
        description="VoiceHook control CLI — inject into live voice calls.",
        epilog="Subcommands: calls | listen | interrupt | help",
    )
    p.add_argument("--session", default=DEFAULT_SESSION, help="session id (default: latest)")
    p.add_argument("text", nargs="*", help="message text (or read from stdin)")
    p.add_argument("--as", dest="role", choices=["system", "user", "assistant"], default="system")
    p.add_argument("--trigger", action="store_true",
                   help="force LLM reply now (default: silent, applies next turn)")
    p.add_argument("--say", action="store_true", help="direct TTS (skip LLM)")
    p.add_argument("--status", action="store_true",
                   help="push event into narrator-loop status buffer")
    p.add_argument("--ephemeral", action="store_true",
                   help="with --say: do not add to chat_ctx")
    p.add_argument("--no-interrupt", action="store_true",
                   help="with --say: do not allow caller to interrupt")
    return p


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Dispatch subcommand if first non-flag token matches
    first = next((a for a in argv if not a.startswith("-")), None)

    if first == "calls":
        argv.remove("calls")
        sp = argparse.ArgumentParser(prog="∆ calls")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        cmd_calls(sp.parse_args(argv))
        return

    if first == "listen":
        argv.remove("listen")
        sp = argparse.ArgumentParser(prog="∆ listen")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        cmd_listen(sp.parse_args(argv))
        return

    if first == "interrupt":
        argv.remove("interrupt")
        sp = argparse.ArgumentParser(prog="∆ interrupt")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        sp.add_argument("--force", action="store_true")
        cmd_interrupt(sp.parse_args(argv))
        return

    if first == "summarize":
        argv.remove("summarize")
        sp = argparse.ArgumentParser(prog="∆ summarize")
        sp.add_argument("value", help="seconds (e.g. 15) or 'off'")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        cmd_summarize(sp.parse_args(argv))
        return

    if first == "peer":
        argv.remove("peer")
        sp = argparse.ArgumentParser(prog="∆ peer",
            description="Post a peer message into the live room as a named CLI agent.")
        sp.add_argument("text", nargs="*")
        sp.add_argument("--as", dest="identity",
                        default=os.getenv("VOICEHOOK_PEER_ID"),
                        help="peer identity (e.g. research-bot); falls back to $VOICEHOOK_PEER_ID")
        sp.add_argument("--speak", action="store_true",
                        help="also speak the message via the live TTS so humans hear it")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        cmd_peer(sp.parse_args(argv))
        return

    if first == "join":
        argv.remove("join")
        sp = argparse.ArgumentParser(prog="∆ join",
            description="Join the live voice room as a named CLI agent; greets and tails.")
        sp.add_argument("identity", help="agent name, e.g. claude-research")
        sp.add_argument("--room", help="optional room name (default: whatever session is live)")
        sp.add_argument("--silent", action="store_true", help="don't speak the greeting")
        sp.add_argument("--session", default=DEFAULT_SESSION)
        cmd_join(sp.parse_args(argv))
        return

    # Shortcut: `∆ <identity> <text...>` → peer-say as identity
    # (activated when first token is not a known subcommand AND looks like a name)
    if first and first not in SUBCOMMANDS and not first.startswith("-") \
            and len(argv) >= 2 and not any(a.startswith("--") for a in argv[:1]):
        # Heuristic: identity is an alphanum+hyphen name that has no spaces
        candidate = first
        if all(c.isalnum() or c in "-_" for c in candidate) and len(candidate) <= 40:
            rest = argv[1:]
            # Recognize `speak` sub-sub-command: `∆ claude speak "text"`
            speak = False
            if rest and rest[0] == "speak":
                speak = True
                rest = rest[1:]
            text = " ".join(rest).strip()
            if text:
                ns = argparse.Namespace(
                    text=[text], identity=candidate, speak=speak, session=DEFAULT_SESSION,
                )
                cmd_peer(ns)
                return

    if first == "help":
        _build_inject_parser().print_help()
        return

    if first == "uninstall":
        argv.remove("uninstall")
        sp = argparse.ArgumentParser(prog="∆ uninstall",
            description="Remove voicehook hooks from .claude/settings.local.json (idempotent). Does not delete the script files.")
        sp.add_argument("--settings", default=".claude/settings.local.json",
                        help="path to Claude Code settings (default: .claude/settings.local.json)")
        sp.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing")
        cmd_uninstall(sp.parse_args(argv))
        return

    if first == "install":
        argv.remove("install")
        sp = argparse.ArgumentParser(prog="∆ install",
            description="Install voicehook hooks into .claude/settings.local.json. --dry-run shows the exact JSON diff before writing.")
        sp.add_argument("--settings", default=".claude/settings.local.json")
        sp.add_argument("--identity", default=os.getenv("VOICEHOOK_PEER_ID", "claude"))
        sp.add_argument("--control-url", default=os.getenv("VOICEHOOK_CONTROL_URL", "https://voicehook.ai/api/control"))
        sp.add_argument("--hook-script", default=os.path.expanduser("~/bin/voicehook_hook.py"))
        sp.add_argument("--dry-run", action="store_true",
                        help="preview the JSON that would be written; do not write")
        cmd_install(sp.parse_args(argv))
        return

    # Default: inject / say / status
    cmd_inject_or_say(_build_inject_parser().parse_args(argv))


if __name__ == "__main__":
    main()
