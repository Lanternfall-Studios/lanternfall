# Lanternfall integration gateway — v1

Steve's lane: the one strict door between the game side and any
backend/vendor, implementing the backend-integration & privacy brief
(`lanternfall-backend-integration-privacy-brief.md`, Oct 30, 2026).
Standard library only, no network, no database. Everything here is
additive: no existing game file is touched.

## Reality this was built against

At main `670afe6` the game in this repo is a standalone stdlib prototype:
no database, no network calls, and **no connection to the core verifier
at all** — `lanternfall/world.py` is the in-process authority and agents
act via `decide(view) -> action`. The signed verifier (enrolled actors,
Ed25519-signed actions, append-only ledger) lives in the sibling repo
**Lanternfall-Studios/lanternfall-core**. The gateway therefore forwards
through an interface (`verifier.VerifierClient`) shaped like that core's
signed-action flow, with a local `FakeVerifier` for tests. Wiring a real
core client behind that interface is a deliberate follow-up, not a claim
made here.

## What's in v1

| File | Piece |
|---|---|
| `tokens.py` | Per-party scoped tokens: HMAC-signed claims, party inside the token (never a caller parameter), default 1-hour expiry, single-token revoke + per-party kill switch. Every verification failure is the same outward `not_allowed`. |
| `pseudonyms.py` | Per-counterparty pseudonyms: HMAC of (party, counterparty). Stable per pair, different per counterparty (unlinkable), no real id inside. `resolve()` is gateway-internal and counterparty-scoped. |
| `filters.py` | Per-operation field allowlists (mirroring brief §4.1) enforced by rejection, plus the never-receive list (§4.2: real names/emails/phones, payment details, real UUIDs, prompts/memory, margins/stock depth…) scanned at any nesting depth, in both directions. Responses are minimised to their allowlist. |
| `audit.py` | Audit entries with exactly: timestamp, actor pseudonym, counterparty pseudonym, action, result code, field **names** disclosed. The API cannot accept values or prompts, so they cannot be logged by accident. Optional JSONL file append. |
| `verifier.py` | The forwarding seam: `VerifierClient` protocol, `FakeVerifier` (records what reached it, returns allowlist-shaped canned responses), `UnavailableVerifier` (fails closed — the default). |
| `gateway.py` | `Gateway.handle()`, the only entry point: freeze check → token/scope verify → pseudonym resolution with **uniform `not_found`** for unknown and not-yours alike → request filter → forward under pseudonym → response minimisation → audit. Plus `freeze()`/`unfreeze()` and `revoke_party()` kill switches. |
| `errors.py` | Outward errors are plain codes only: `not_found`, `not_allowed`. Internal reasons never cross the boundary. |

## Usage sketch

```python
from gateway import (AuditLog, FakeVerifier, Gateway,
                     PseudonymService, TokenService)

gw = Gateway(TokenService(signing_key), PseudonymService(pseudo_secret),
             verifier=FakeVerifier(), audit_log=AuditLog())
gw.register_party("vendor-1"); gw.register_party("client-1")
token = gw.issue_token("client-1", ["catalogue:read"], ttl_seconds=3600)
vendor_seen_by_client = gw.pseudonym_for("vendor-1", "client-1")
gw.handle("catalogue_read", token, {"vendor": vendor_seen_by_client})
# -> {"item": ..., "price": ..., "in_stock": ...}
```

## Tests

From the repo root:

```bash
python3 -B -m unittest discover -s gateway/tests -v   # 21 tests
python3 -B tests/smoke_test.py                        # existing suite
python3 -B tests/model_agent_test.py                  # existing suite
```

Coverage maps to brief §7: token scope, expiry, single + party
revocation, freeze drill, pseudonym unlinkability/stability,
never-receive rejection (top-level and nested), unknown-field rejection,
audit-log hygiene (canary values and real ids asserted absent), uniform
unknown/not-yours errors, and verifier-seam hygiene (only pseudonym +
filtered payload cross).

## Deliberately deferred (and why)

- **Live core client** — needs the core's actual transport/API contract
  agreed with lanternfall-core; the seam and fake come first so the game
  side never grows a second, unfiltered path in the meantime.
- **HTTP transport + rate limiting / enumeration throttling** (§7 test 6)
  — v1 is a library boundary; the uniform-error half is implemented and
  tested, the throttle belongs with the transport.
- **Postgres row-level security** (§3.2 rule 3) — defence-in-depth in the
  backend this gateway eventually fronts; there is no backend in this
  repo yet.
- **Real Ed25519 signature verification** — the gateway forwards the
  caller's signature opaquely; verification belongs to the core verifier,
  which is its sole-authority design.
- **Agent memory isolation** (§3.2 rule 6, §7 test 4) — an agent-harness
  property, not gateway code; the gateway's contribution (no prompts or
  memory in payloads/logs) is enforced by the never-receive list.
- **Vault-backed key storage, audit retention/deletion (30-day default),
  sandbox/production split** — operator/deployment concerns for the phase
  where a real backend exists (brief §6 hard gate); keys here are
  injected, never hardcoded or stored.
- **Persistence** — token revocations and the pseudonym registry are
  in-memory in v1; a restart forgets revocations (tokens still expire
  within their TTL). Durable storage lands with the backend.
