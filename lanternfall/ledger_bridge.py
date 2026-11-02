"""Opt-in hash-only checkpoint transport. No core import or key required headlessly.

An anchor attests an enrolled signer's claim, not correct simulation or task
execution. The operator supplies a trusted, pinned verifier wrapper and an
external owner-only signing key. No original Cradle migration is performed.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
import subprocess


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def checkpoint(engine, kind="tick") -> dict:
    world = engine.world
    traces = {a.name: a.trace for a in engine.agents if hasattr(a, "trace")}
    chapters = {name: [e for e in trace if e.get("kind") == "model_change"]
                for name, trace in traces.items()}
    return {"version": 1, "kind": kind, "world_seed": world.spec.seed,
            "tick_number": world.tick,
            "events_jsonl_sha256": hashlib.sha256(b"".join(
                canonical(e) + b"\n" for e in world.event_log)).hexdigest(),
            "world_snapshot_sha256": digest(world.snapshot()),
            "commons_stock": world.nodes[world.spec.commons_zone].stock,
            "trajectories_sha256": digest({a.name: a.trajectory_hash() if hasattr(a, "trajectory_hash")
                                           else digest(a.trace)
                                           for a in engine.agents if hasattr(a, "trace")}),
            "market_trades_sha256": digest(world.market_history),
            "model_changes_sha256": digest(chapters),
            "work_orders_sha256": digest([e for e in world.event_log
                                          if e["kind"] == "work_order"])}


class LedgerBridge:
    def __init__(self, verifier=None, key=None, actor=None):
        if any((verifier, key, actor)) and not all((verifier, key, actor)):
            raise ValueError("anchoring requires verifier, external key and enrolled actor")
        self.verifier = Path(verifier).resolve(strict=True) if verifier else None
        self.key = Path(key).absolute() if key else None
        self.actor = actor
        if self.key:
            # Check custody without reading key bytes or following a key symlink.
            info = self.key.lstat()
            boundary = Path(__file__).resolve().parents[2]
            self.key.relative_to(boundary)
            self.verifier.relative_to(boundary)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or ".." in self.key.parts):
                raise ValueError("signing key must be an external owner-only regular file")
            current = boundary
            for part in self.key.relative_to(boundary).parts:
                if (current / ".git").exists() or current.is_symlink():
                    raise ValueError("signing key must be outside both source repositories")
                current /= part
            if self.key.resolve() != self.key:
                raise ValueError("signing key symlinks forbidden")
        self.last_result = None

    def emit(self, payload):
        identifier = digest(payload)
        if self.verifier is None:
            result = {"status": "headless", "receipt_sha256": identifier,
                      "payload": payload}
        else:
            response = subprocess.run(
                [str(self.verifier), "request", "--key", str(self.key),
                 "--actor", self.actor, "--action", "receipt.anchor",
                 "--params", canonical(payload).decode("ascii")],
                capture_output=True, timeout=15, check=True)
            anchored = json.loads(response.stdout)
            if (not anchored.get("ok") or anchored.get("result", {}).get("receipt_sha256") != identifier
                    or anchored.get("result", {}).get("status") != "anchored"):
                raise RuntimeError("checkpoint acknowledgement does not match receipt")
            result = {"status": "anchored", "receipt_sha256": identifier,
                      "verifier_response": anchored}
        self.last_result = result
        return result
