# Changelog

All notable changes to voicehook-skill. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `guard/` — voicehook-guard local egress filter (v0.1.0). Deny-by-default FastAPI proxy on `127.0.0.1:7301` with endpoint allowlist, JSON-schema validation, PII scan (scrubadub), and secret-regex scan. Ships with Claude Code + OpenClaw skill wrappers. Same `voicehook_guard.py` is the trust anchor for both frameworks — SHA256 in `SHA256SUMS.txt`.
- `docs/auth.md` — zero-trust auth contract written for agents, not humans. Specifies four deterministic server rules (no capability → structured 401; capability is session-scoped; rejects are machine-readable JSON; allows produce signed receipts) plus self-verification probes. Phase-1 (shared-secret bearer) is the live baseline; Phase-2 (PASETO capability tokens + JWKS) and Phase-3 (signed receipts + revocation list) are the zero-trust target.

### Planned

- v0.3: `delta.py uninstall` command for one-shot cleanup
- v0.3: `delta.py install --dry-run` to preview hook diff before write
- v0.3: auth Phase-1 wiring — `VOICEHOOK_CONTROL_BEARER` env on client, structured 401 on server, Caddy snippet published
- v0.4: explicit `AskUserQuestion` step inside the skill before hook write
- v0.4: `voicehook-guard verify` subcommand bundling the four self-verification probes
- v0.4: `pyproject.toml` for `pipx install voicehook-guard`
- v0.5: auth Phase-2 — PASETO capability tokens, JWKS endpoint, peer-invite/exchange flow
- v0.6: auth Phase-3 — signed receipts (`X-Voicehook-Receipt`), revocation list

## [0.2.0] — 2026-04-20

### Added
- KERN-INVARIANTE block in `SKILL.md`: full handover is mandatory on every join
- Minute-level reminder cron as mandatory Step 3b — Claude tracks the session between turns
- Append vs `system_mode: replace` documentation for context-switch handling
- Room URL pattern: `https://voicehook.ai/<user>∆<agent>∆<room>` (pretty URLs + Caddy rewrite)

### Changed
- Peer-identity convention: `claude` (not `claude-<username>` compounds) — matches Delta's system-prompt expectations
- Handover is generated PER-JOIN from current chat context (no generic templates)

## [0.1.0] — 2026-04-18

### Added
- Initial public release
- `SKILL.md` with join/greet/hooks/announce flow
- `cli/voicehook_hook.py` hook relay (PostToolUse, Stop)
- `cli/delta.py` CLI for transcript-tail / inject / say / interrupt
- Default control URL: `https://voicehook.ai/api/control`
- Loopback support via `VOICEHOOK_CONTROL_URL` env var
