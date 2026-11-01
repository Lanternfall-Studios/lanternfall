"""Forwarding interface toward the core signed-action verifier.

Reality check (repo at main 670afe6): the game in *this* repo has no core
connection at all -- it is a standalone stdlib prototype whose ``world.py``
is the authority. The signed verifier (enrolled actors, Ed25519-signed
actions, append-only ledger, verifier as sole authority) lives in the
sibling repo, Lanternfall-Studios/lanternfall-core. So this module defines
the seam, not a live integration:

- ``VerifierClient`` -- the protocol the gateway forwards through. The
  gateway hands over only the actor's *pseudonym*, the already-filtered
  payload, and the caller's opaque signature string. It holds no signing
  keys and no real party ids cross this seam.
- ``FakeVerifier`` -- a local, in-memory stand-in for tests and sandbox
  runs. It records exactly what reached it (so tests can prove what did
  *not* cross) and returns canned, allowlist-shaped responses.
- ``UnavailableVerifier`` -- the default when no verifier is wired: every
  forward fails closed with ``not_allowed``.
"""

import hashlib
import json

from .errors import NotAllowed


class VerifierClient:
    """Protocol: submit one signed action / query, return a response dict."""

    def submit(self, operation, actor_pseudonym, payload, signature=""):
        raise NotImplementedError


class UnavailableVerifier(VerifierClient):
    def submit(self, operation, actor_pseudonym, payload, signature=""):
        raise NotAllowed(reason="verifier_unavailable")


def _short_hash(payload):
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:8]


class FakeVerifier(VerifierClient):
    """In-memory verifier for tests. ``responses`` may override the canned
    response per operation."""

    def __init__(self, responses=None):
        self.submissions = []  # (operation, actor_pseudonym, payload, signature)
        self._responses = responses or {}

    def submit(self, operation, actor_pseudonym, payload, signature=""):
        self.submissions.append((operation, actor_pseudonym, dict(payload), signature))
        if operation in self._responses:
            return dict(self._responses[operation])
        if operation == "catalogue_read":
            return {"item": "lantern", "price": 7, "in_stock": True}
        if operation == "order_create":
            return {"order": "ord_" + _short_hash(payload),
                    "status": "accepted", "total": 14}
        if operation == "order_status":
            return {"order": payload.get("order", "ord_unknown"),
                    "status": "accepted"}
        if operation == "trade_settle":
            return {"settlement": "stl_" + _short_hash(payload),
                    "status": "settled"}
        if operation == "balance_read":
            return {"balance": 250}
        raise NotAllowed(reason="unknown_operation")
