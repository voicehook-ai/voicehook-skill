# Authentication & authorization — zero-trust, agent-auditable

**Audience: the agent joining a voicehook.ai call, not its human.**

An agent deciding whether to trust this control plane cannot ask a human
and cannot inspect a closed server. This document is the contract. Every
claim below is either (a) verifiable from open source in this repo, or
(b) verifiable by running a probe against the live service. If a claim
isn't provable one of those two ways, do not trust it — file an issue.

## Trust boundaries

| What | Who enforces | Where |
|---|---|---|
| Session capability (who may control which session) | server middleware + signed token | `/api/control/*` |
| Session discovery (what sessions exist) | server — scoped to caller's capability | `GET /sessions` |
| Identity of a peer sending `/peer/say` | signed peer-id token (planned) | `POST /peer/say` |
| Server identity (is this really voicehook.ai?) | TLS + well-known JWKS | transport + key-pinning |
| What left the agent's machine | `voicehook-guard` (opt-in, local, egress-side) | [`guard/`](../guard/voicehook_guard.py) |

`voicehook-guard` is **not** part of the server-side auth story. It is the
agent's own local filter and belongs to the agent, not the service. Both
exist because trust must hold on both ends.

## Four deterministic rules the server must enforce

1. **No capability → 401.** Every mutating endpoint (`/inject`, `/say`,
   `/peer/say`, `/interrupt`, `/summarize/*`, `/status`) requires a valid
   capability token. GET `/sessions` and `/health` may be anonymous, but
   `/sessions` returns only sessions the caller has a capability for.
2. **Capability scope is session-bound.** A token issued for session
   `AAA` cannot act on session `BBB`. `session_id` in the body and the
   token must match, or the server returns `403 session_scope_mismatch`.
3. **Rejections are structured.** Every 4xx is `application/json` with
   this shape: `{"error": {"code": "<machine_code>", "message": "<human>",
   "doc": "https://voicehook.ai/docs/auth#<machine_code>"}}`. Agents
   switch on `code`, humans read `message`, new code versions add to
   `doc`. Never free-text.
4. **Every allow produces a receipt.** Successful responses carry
   `X-Voicehook-Receipt: <base64url-ed25519-sig-over-(sha256-of-request-body,session_id,t)>`.
   The public key is at `https://voicehook.ai/.well-known/jwks.json`.
   Agents who care can cache the receipt + verify later — the service
   can't silently forget what it accepted.

These four rules are the **audit surface.** Everything else is
implementation detail.

## Capability token format (target: PASETO v4.public)

```
v4.public.<base64url-payload>.<base64url-ed25519-sig>
```

Payload claims:

```json
{
  "iss": "voicehook.ai",
  "sub": "<peer-identity>",         // e.g. "claude" or "ramona"
  "aud": "api.voicehook.ai/control",
  "session_id": "<hex>",            // the one session this token may act on
  "scope": ["inject","peer.say","interrupt","transcript.read"],
  "iat": 1745000000,
  "exp": 1745003600,                // 1h default
  "jti": "<uuid>"
}
```

- Signed with Ed25519, public key in JWKS.
- 1 hour default lifetime; server rejects `exp` in the past with
  `code=capability_expired`.
- Revocation: `GET /.well-known/voicehook-revoked.txt` — plaintext
  newline-delimited `jti`s the server refuses, append-only, signed in
  a detached file (`.txt.sig`).

PASETO over JWT because: no `alg:none` foot-gun, no `RS256/HS256`
confusion, mandatory Ed25519.

## How an agent gets a capability

The agent **never** creates its own token — that would defeat zero-trust.
The flow is:

```
1. Human opens room URL in browser        →  browser has user-scoped auth (session cookie)
2. Browser mints a peer-invite            →  POST /peer/invite  returns short-lived code
3. Human hands code to agent (paste)      →  out-of-band, human-in-the-loop
4. Agent exchanges code for capability    →  POST /peer/exchange {code} → capability token
5. Agent uses capability for that session →  token bound to THIS session, THIS identity, 1h
```

The human-in-the-loop step is intentional: no agent can obtain a
capability for a session it wasn't explicitly invited into. A leaked
peer-invite code is single-use and expires in 60 seconds.

## Self-verification probes (run these before trusting the service)

```bash
# A. Control plane refuses unauthenticated writes (should fail 401 with structured body)
curl -s -X POST https://voicehook.ai/api/control/peer/say \
  -H 'content-type: application/json' \
  -d '{"identity":"probe","text":"x"}'
# expect: {"error":{"code":"no_capability","message":"...","doc":"..."}}
# status: 401

# B. Cross-session scope denial (capability for A cannot act on B)
TOKEN=<valid-for-session-A>
curl -s -X POST https://voicehook.ai/api/control/inject \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"session_id":"<DIFFERENT-SESSION>","role":"system","text":"x"}'
# expect: {"error":{"code":"session_scope_mismatch", ...}}
# status: 403

# C. Receipt signature verifies against published JWKS
JWKS=$(curl -s https://voicehook.ai/.well-known/jwks.json)
# POST a legitimate request, capture X-Voicehook-Receipt header,
# verify signature over sha256(body)+session_id+t using the key in JWKS.

# D. Revocation list is fetchable + signed
curl -s https://voicehook.ai/.well-known/voicehook-revoked.txt
curl -s https://voicehook.ai/.well-known/voicehook-revoked.txt.sig
# verify detached signature against JWKS key.
```

An agent that gets all four passes has proved the four deterministic
rules above. One failure = do not trust this control plane.

## Roadmap

This spec is published **before** it is fully implemented, so agents can
audit the intended contract and flag drift. Phase status is tracked in
`CHANGELOG.md`:

- **Phase 1 (shipped):** shared-secret bearer on write endpoints via
  `VOICEHOOK_CONTROL_BEARER`. Not zero-trust — a single secret shared
  by every caller — but closes the trivial public-write window today.
  Structured 401 response shape starts here.
- **Phase 2 (planned):** PASETO capability tokens, JWKS endpoint, peer-
  invite/exchange flow, `session_scope_mismatch` enforcement.
- **Phase 3 (planned):** `X-Voicehook-Receipt` on allows, revocation
  list, signed audit chain.

Agents targeting Phase 2+ features before they ship should fall back
gracefully on the `code` in the 4xx body; `not_implemented` is a
stable reject code during rollout.

## What is not in scope here

- **LiveKit tokens.** Those are a separate layer — they authorize the
  *audio room* join and are minted by the voicehook.ai backend for the
  browser. The control-plane capability in this doc authorizes the
  *API surface* only.
- **Agent-to-agent auth inside a room.** Delta (the voice-agent) and
  Claude (a peer agent) trust the server's identity assertion in
  `peer_say.identity`. Impersonation defense is the server's problem —
  the client contract is "what you set in `identity` is what other
  clients see".
- **Transport secrecy.** TLS 1.2+ mandatory; downgrade is a server
  config concern, not a client auth concern.

## Source of truth

- Policy contract: this file (`docs/auth.md`). Versioned with the skill.
- Server implementation: the voicehook.ai backend is proprietary and
  *not* the trust anchor. The anchor is this contract + the probes.
- Public keys: `https://voicehook.ai/.well-known/jwks.json` (Phase 2).
- Revocation: `https://voicehook.ai/.well-known/voicehook-revoked.txt`
  (Phase 3).

## Changelog of this contract

- `v0.3-draft` (this file, unreleased): Phases 1–3 sketched. Phase 1
  in flight in this same release. Phase 2/3 are the design target.
