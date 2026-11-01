"""Per-party scoped tokens (brief section 3.2, rule 2).

Each token is an HMAC-SHA256-signed claim set. The party is *inside* the
token, never a parameter the caller supplies, so a token minted for one
party physically cannot name another. Tokens are short-lived (default one
hour), carry an explicit scope list, and are revocable two ways:

- ``revoke(token_or_id)`` kills one token;
- ``revoke_party(party_id)`` kills every token issued for that party up to
  now (the per-party kill switch, brief section 7 test 7).

Verification failures of every kind -- malformed, bad signature, expired,
revoked, missing scope -- raise the same outward ``not_allowed`` code.

The signing key is injected by the caller. In production it lives in a
vault (brief section 3.2 rule 8); it is never stored in this package.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from .errors import NotAllowed


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


@dataclass(frozen=True)
class TokenClaims:
    party_id: str
    scopes: tuple
    issued_at: float
    expires_at: float
    token_id: str


class TokenService:
    def __init__(self, signing_key, clock=time.time):
        if isinstance(signing_key, str):
            signing_key = signing_key.encode("utf-8")
        if len(signing_key) < 16:
            raise ValueError("signing_key too short")
        self._key = signing_key
        self._clock = clock
        self._revoked_token_ids = set()
        # party_id -> clock time of the revoke_party call
        self._revoked_parties = {}

    # -- issuing ---------------------------------------------------------
    def issue(self, party_id, scopes, ttl_seconds=3600):
        if not party_id or not isinstance(party_id, str):
            raise ValueError("party_id required")
        scopes = tuple(sorted(set(scopes)))
        if not scopes:
            raise ValueError("at least one scope required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = float(self._clock())
        payload = {
            "sub": party_id,
            "scopes": list(scopes),
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_hex(8),
        }
        body = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        sig = _b64encode(
            hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        )
        return body + "." + sig

    # -- verification ----------------------------------------------------
    def verify(self, token, required_scope=None):
        try:
            body, sig = token.split(".", 1)
            expected = _b64encode(
                hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(sig, expected):
                raise NotAllowed(reason="bad_signature")
            payload = json.loads(_b64decode(body))
            claims = TokenClaims(
                party_id=payload["sub"],
                scopes=tuple(payload["scopes"]),
                issued_at=float(payload["iat"]),
                expires_at=float(payload["exp"]),
                token_id=payload["jti"],
            )
        except NotAllowed:
            raise
        except Exception:
            raise NotAllowed(reason="malformed_token")
        now = float(self._clock())
        if now >= claims.expires_at:
            raise NotAllowed(reason="expired")
        if claims.token_id in self._revoked_token_ids:
            raise NotAllowed(reason="revoked")
        revoked_at = self._revoked_parties.get(claims.party_id)
        if revoked_at is not None and claims.issued_at <= revoked_at:
            raise NotAllowed(reason="party_revoked")
        if required_scope is not None and required_scope not in claims.scopes:
            raise NotAllowed(reason="missing_scope")
        return claims

    # -- revocation ------------------------------------------------------
    def revoke(self, token_or_id):
        """Revoke one token, given either the token string or its jti."""
        token_id = token_or_id
        if "." in token_or_id:
            try:
                payload = json.loads(_b64decode(token_or_id.split(".", 1)[0]))
                token_id = payload["jti"]
            except Exception:
                return  # unparseable: nothing identifiable to revoke
        self._revoked_token_ids.add(token_id)

    def revoke_party(self, party_id):
        self._revoked_parties[party_id] = float(self._clock())
