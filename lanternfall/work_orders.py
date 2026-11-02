"""Bounded inert engineering outbox. A request is never executable authority.

Only a separately trusted local worker may call complete() with reviewed artifact
and passing-test evidence bytes. This module does not execute tasks or independently
prove the evidence truthful. There is deliberately no HTTP completion endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
import tempfile

from .ledger_bridge import canonical, digest


class WorkOrders:
    def __init__(self, outbox=None, on_status=None):
        self.orders = {}
        self.last_submission = {}
        self.on_status = on_status or (lambda order: None)
        self.outbox = Path(outbox).absolute() if outbox is not None else None
        self.worker_key = None
        if self.outbox:
            # Never follow an outbox symlink out of the workspace boundary.
            boundary = Path(__file__).resolve().parents[2]
            relative = self.outbox.relative_to(boundary)
            current = boundary
            for part in relative.parts:
                if part in {".", ".."}:
                    raise ValueError("unsafe outbox")
                current = current / part
                if current.is_symlink():
                    raise ValueError("outbox symlink forbidden")
            self.outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.outbox.stat().st_mode & 0o077:
                raise ValueError("outbox must be owner-only")
            if any(self.outbox.iterdir()):
                raise ValueError("use a fresh per-session outbox; world recovery is not implemented")
            self.worker_key = os.urandom(32)
            fd = os.open(self.outbox / "worker.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(self.worker_key)
                output.flush()
                os.fsync(output.fileno())

    def _publish(self, order, create=False):
        if self.outbox:
            target = self.outbox / (order["id"] + ".json")
            if create and target.exists():
                raise ValueError("work order already persisted")
            fd, name = tempfile.mkstemp(prefix=".order-", dir=self.outbox)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(canonical(order) + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, target)
                directory = os.open(self.outbox, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        if order["id"] in self.orders:
            self.orders[order["id"]].update(order)
        else:
            self.orders[order["id"]] = order
        self.on_status(order)
        return order

    def submit(self, sender, target, text, tick, chat_id, seed):
        if target not in {"steward", "engineer"} or not text or len(text) > 480:
            raise ValueError("invalid directive")
        if len(self.orders) >= 128:
            raise ValueError("work queue capacity reached")
        if tick - self.last_submission.get(sender, -10) < 10:
            raise ValueError("work order cooldown")
        claim = {"sender": sender, "target": target, "directive": text,
                 "tick": tick, "chat_id": chat_id, "world_seed": seed}
        identifier = digest(claim)
        order = dict(claim, id=identifier, status="queued", untrusted_input=True,
                     station="Forge" if target == "engineer" else "Library")
        self._publish(order, create=True)
        self.last_submission[sender] = tick
        return order

    def start(self, identifier):
        order = self.orders[identifier]
        if order["status"] != "queued":
            raise ValueError("work order not queued")
        return self._publish(dict(order, status="in_progress"))

    def _complete_hashes(self, identifier, hashes):
        order = self.orders[identifier]
        if order["status"] != "in_progress":
            raise ValueError("completion requires active work")
        return self._publish(dict(order, status="verified", **hashes,
                                  completion_sha256=digest(hashes)))

    def complete(self, identifier, artifact, verification):
        if not artifact or not verification:
            raise ValueError("completion requires external evidence")
        return self._complete_hashes(identifier, {
            "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
            "verification_sha256": hashlib.sha256(verification).hexdigest()})

    def poll_completions(self):
        if self.outbox is None:
            return
        for order in list(self.orders.values()):
            if order["status"] != "in_progress":
                continue
            path = self.outbox / (order["id"] + ".completion.json")
            if not path.exists():
                continue
            try:
                raw = bounded_file(path, 4096)
                completion = json.loads(raw)
                if not isinstance(completion, dict) or set(completion) != {"body", "signature"}:
                    continue
                body, signature = completion["body"], completion["signature"]
                if not isinstance(body, dict) or set(body) != {"order_id", "request_sha256", "artifact_sha256", "verification_sha256"}:
                    continue
                if body["order_id"] != order["id"] or body["request_sha256"] != digest({k: order[k] for k in ("sender", "target", "directive", "tick", "chat_id", "world_seed")}):
                    continue
                if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in body.values()):
                    continue
                expected = hmac.new(self.worker_key, canonical(body), "sha256").hexdigest()
                if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
                    continue
                hashes = {k: body[k] for k in ("artifact_sha256", "verification_sha256")}
            except (ValueError, OSError, TypeError, KeyError):
                # Malformed/forged completion is inert; it never advances state.
                continue
            # A persistence failure is NOT a malformed input: propagate and pause.
            self._complete_hashes(order["id"], hashes)


def bounded_file(path, maximum=16 * 1024 * 1024):
    path = Path(path).absolute()
    boundary = Path(__file__).resolve().parents[2]
    relative = path.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        if part == "..":
            raise ValueError("nonlocal evidence path")
        current /= part
        if current.is_symlink():
            raise ValueError("evidence symlink forbidden")
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("regular evidence file required")
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("evidence too large")
    return raw


def write_completion(outbox, identifier, artifact, verification):
    """Trusted local worker attestation, not independent verification of its claims.

    The worker first runs/reviews tests separately. This command checks a structured
    passing report and signs hashes; it never runs the report's text or a shell.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("invalid order id")
    outbox = Path(outbox).absolute()
    order = json.loads(bounded_file(outbox / (identifier + ".json"), 8192))
    claim = {k: order[k] for k in ("sender", "target", "directive", "tick", "chat_id", "world_seed")}
    if order["id"] != identifier or digest(claim) != identifier or order["status"] != "in_progress":
        raise ValueError("intact active work order required")
    report_raw = bounded_file(verification)
    report = json.loads(report_raw)
    if not isinstance(report, dict) or report.get("passed") is not True or type(report.get("exit_code")) is not int or report["exit_code"] != 0:
        raise ValueError("explicit passing verification report required")
    body = {"order_id": identifier, "request_sha256": identifier,
            "artifact_sha256": hashlib.sha256(bounded_file(artifact)).hexdigest(),
            "verification_sha256": hashlib.sha256(report_raw).hexdigest()}
    key = bounded_file(outbox / "worker.key", 32)
    if len(key) != 32 or (outbox / "worker.key").stat().st_mode & 0o077:
        raise ValueError("owner-only worker capability required")
    completion = {"body": body, "signature": hmac.new(key, canonical(body), "sha256").hexdigest()}
    fd, name = tempfile.mkstemp(prefix=".completion-", dir=outbox)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(canonical(completion) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        # Never overwrite an earlier worker attestation.
        os.link(name, outbox / (identifier + ".completion.json"))
        directory = os.open(outbox, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(name)
    return completion


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Attest already-reviewed local work; never executes tasks")
    parser.add_argument("--outbox", required=True)
    parser.add_argument("--order", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--verification", required=True, help='JSON with passed:true, exit_code:0 plus sanitized evidence')
    args = parser.parse_args()
    result = write_completion(args.outbox, args.order, args.artifact, args.verification)
    print(json.dumps({"status": "completion_staged", "order_id": result["body"]["order_id"]}))
