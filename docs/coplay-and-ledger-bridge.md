# Local co-play and hash-only ledger checkpoints

## Launch

```sh
cd ~/echoes/company_sandbox/lanternfall
python3 -B run.py --serve --interactive --seed 42 --port 8765
```

Open the printed **chris session** or **jon session** URL. The dashboard lives at
`http://127.0.0.1:8765/`; the session URL includes an actor-scoped capability in its
fragment. The UI removes the fragment and keeps the capability in sessionStorage.
Do not publish these links or terminal output. Each of the seven slots has its own
printed capability, including agent slots usable by an external SDK client.

The room starts at tick zero (no precomputed 300-tick season). It runs until Ctrl-C.
Default cadence is a minimum wait of **314159265 ns between turns**, plus processing
and persistence time; there are no catch-up bursts or hard-real-time guarantees.
The legacy Cradle verifier still has its signed **600 ms** clock: this feature does
not migrate that clock. Anchoring blocks for verifier acceptance and can slow turns.

Roster: `chris`, `jon`, `steward`, `engineer`, `steve`, `tom`, `thales`. All start in
Haven (seed 42), all have the same economic primitives and per-actor queue bound
of 16. `steward` and `engineer` are **scripted work coordinators**, not running LLMs
or autonomous code-editing subprocesses. Other slots idle unless given actions.
Server-assigned trajectory provenance identifies these policies honestly.
Library and Forge are named **workstations in Haven**, not additional graph zones.
The player table shows movement; work status and workstation appear in the event
feed and work-order panel. There is no 3D/avatar renderer.

Browser controls cover movement, harvest, crafting, floor sale, bids/asks, and chat.
The JSON control can queue every legal primitive, including bilateral proposals:

```json
["propose", "jon", "ember", 1, "salt", 1, "1x ember"]
["respond", 12, true]
```

Use the generated item identifiers and actual proposal ID from your view, not
necessarily these example items. The counterparty still must consent and own the
requested items. Queue acceptance is not action success: inspect later events.
Orders and resources still pass the same `Engine.apply` checks for every slot.

`python3 run.py --serve` without `--interactive` retains the read-only demo viewer.

## Local HTTP/SDK contract

- `POST /api/action`: `{"actor":"chris","action":["gather"]}`
- `POST /api/chat`: `{"actor":"chris","text":"@steward audit commons depletion"}`
- `GET /api/view?actor=chris`: read-only view, no agent cursor advancement.
- `GET /state.json`: shared public room snapshot, no capabilities or directive bodies.

The first three require `Authorization: Bearer <that actor's session capability>`.
POST requires `Content-Type: application/json`, exact fields, and at most 4096 bytes.
Unknown shapes, invalid quantities/types, unknown actors, full queues, and foreign
Origins are rejected. Host checks mitigate DNS rebinding; session capabilities and
JSON-only POSTs prevent cross-origin action submission. There is no CORS access.
Rendered dynamic table/market strings are HTML-escaped; events use textContent.

Pure helpers in `lanternfall.sdk`:

- `path_to_resource(view, item)` -> movement primitives followed by gather.
- `propose_safe_trade(view, target, give, give_qty, want, want_qty)` -> explicit terms.
- `submit_work_order(target, directive)` -> ordinary `say` primitive.
- `audit_commons_health(view)` -> stock/cap/status observation.

Plans grant no authority, atomicity, resources, or bypass of spatial/gate rules;
conditions may change before a later step. These are local game actions, **not yet
individually Ed25519-signed Cradle economic actions**. Cradle checkpoints attest
hash commitments to this separate simulation rather than replay its economy.

## Inert work orders and real-worker handoff

An addressed chat reaches the worker through `view["recent_chat"]` during its turn:

1. Persist a structured, explicitly untrusted request; log `queued`; say an ack.
2. Spend subsequent turns walking legally toward Haven.
3. At the Library/Forge, persist/log `in_progress`; say it is awaiting an authorized
   worker. **This status does not mean a process is editing code.**
4. A separately authorized local engineer performs/reviews the real changes and
   tests in the appropriate feature branch. Chat never chooses a command, path,
   plugin, credential, repository privilege, or approval policy.
5. That trusted worker stages a completion attestation using the printed outbox:

```sh
python3 -B -m lanternfall.work_orders \
  --outbox /absolute/printed/outbox \
  --order FULL_64_HEX_ORDER_ID \
  --artifact /absolute/workspace/path/reviewed.patch \
  --verification /absolute/workspace/path/verification.json
```

The verification file must be JSON with `"passed": true` and integer
`"exit_code": 0`; include sanitized command/test evidence. The command hashes the
artifact and report, checks the active order, and authenticates the completion
using the session's owner-only **worker.key HMAC capability**. It runs **no code**.
Next turn checks that MAC and request binding before emitting `verified` and a
completion hash in `events.jsonl` and the next ledger checkpoint. Forged, malformed,
wrong-request, and duplicate completions cannot advance an order.

`verified` means **an authorized worker attested reviewed passing evidence**, not
an independent judge proved the patch correct. HMAC authenticates the local worker
capability; an optional Cradle anchor adds enrolled-actor Ed25519 signatures.
Only a trusted worker gets the outbox capability. There is no HTTP completion API.
The in-process trusted `Room.complete_work` API is also available to integrations.

Default session files are outside both source repositories, below
`~/echoes/company_sandbox/.steward-audit/work_orders/session-*/`. The launcher prints
outbox/artifact locations; `events.jsonl` is fsynced live and final summary/world
artifacts are saved on Ctrl-C. `--work-outbox` must name a fresh private directory;
`--out` selects artifact output within the workspace. Do not stage runtime files.
The outbox is capped at 128 requests/session, with a ten-tick sender cooldown.
Requests, chat, and artifacts are **plaintext local data**: no secrets, personal
health data, raw private model prompts, or sensitive third-party material.

## Opt-in Cradle bridge

Without configuration, headless tests and simulation have no sibling import,
key, daemon, filesystem, network, or OpenSSL requirement. An opt-in engine hook
emits tick checkpoints and aggregate market/model-change/work-order checkpoints;
the CLI also emits a final season checkpoint. Changes within a tick are committed
by the aggregate hash, not one ledger event per individual trade.

Canonical bytes: ASCII JSON, sorted keys, compact separators, ensure_ascii=True,
allow_nan=False. Events JSONL is each canonical event plus LF; world.json contains
canonical snapshot bytes without a trailing newline. Receipt fields:

- version=1; kind=`tick|season|market_trade|model_change|work_order`;
- world_seed, tick_number, commons_stock: bounded nonnegative integers;
- events_jsonl_sha256, world_snapshot_sha256;
- trajectories_sha256 (commitment to per-agent trajectory hashes);
- market_trades_sha256, model_changes_sha256, work_orders_sha256.

Only these counters, fixed enums, and hashes cross into the core ledger, never
raw chat, task text, model traces, prompts, artifacts, or keys. Hashes are not
anonymization, proof of factual correctness, or a replacement for retaining the
corresponding reviewed artifacts.

With a **fresh, upgraded development Cradle** already serving and a separately
enrolled signer whose owner-only key is **outside both Git source trees**:

```sh
python3 -B run.py --serve --interactive --seed 42 \
  --ledger-bridge /absolute/workspace/path/to/development/cradle/pasture/verifier.sh \
  --bridge-key /absolute/workspace/path/outside-both-repos/bridge-actor.key \
  --bridge-actor ENROLLED_16_HEX_ACTOR_ID
```

Equivalent environment: `LANTERNFALL_LEDGER_BRIDGE`, `LANTERNFALL_BRIDGE_KEY`,
`LANTERNFALL_BRIDGE_ACTOR`. All three must be supplied together. Use a trusted
wrapper with its original public pin; do not obtain pins from a server response.
The bridge never reads key bytes into Python, copies keys, starts a daemon, uses
root custody automatically, or executes a shell. The existing core client signs
`receipt.anchor` and the verifier fsyncs its Ed25519-attested event. Receipt digest
acknowledgement must match; independent core `verifier.sh verify` replays the chain.

The original live `pasture/cradle` is NOT upgraded by this source change. Do not
point this flag there expecting a migration: its old engine rejects the action.
Original rollout needs reviewed, signed/versioned migration and backup/recovery.
The new core supports at most 4096 distinct receipts per enrolled actor; reaching
that bound pauses anchored play. Rotate/extend only by explicit operator review.

## Failure and security limits

- A tick/outbox/event/anchor persistence failure pauses the room; there are no
  automatic retries or rollback claims across the two independent filesystems.
  A timed-out anchor may have committed: inspect core replay before retrying.
- Gameplay snapshots/events are not a complete restartable world checkpoint.
  Fresh per-session outboxes are required; crash recovery/resume is not implemented.
  Original live ledgers are never rewritten or repaired by these tools.
- No production server, tenant isolation, public login, TLS, cross-machine client,
  paid model integration, autonomous shell runner, Git push/merge/deploy, or authority
  to execute arbitrary in-game work orders is supplied. Same-account local code
  can read the session files: this is not OS isolation.
- Histories and trajectory hashing grow with session duration. This is a bounded
  local prototype, not a throughput/long-running MMO deployment benchmark.

## Verify

```sh
python3 -B tests/smoke_test.py
python3 -B tests/model_agent_test.py
LANTERNFALL_CORE_SOURCE=../lanternfall-core \
  python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Without the optional environment variable only the cross-repository integration
case is skipped. With it, a fresh disposable world, dedicated enrolled actor,
real Ed25519 signing/fsync/replay, duplicate rejection, and tamper failure are tested.
No original key or live ledger is used. The CLI test starts an ephemeral-port
server, queues human chat, observes work status, and interrupts only its own child.
