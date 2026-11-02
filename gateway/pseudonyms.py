"""Per-counterparty pseudonyms (brief section 3.2, rule 4).

A party's pseudonym is an HMAC of (party, counterparty) under a gateway
secret: stable for the same pair, different for every other counterparty,
and containing no trace of the real party id. Vendor A therefore knows a
client under one pseudonym and Vendor B under another, and the two cannot
collude to join them (brief section 7 test 3).

``resolve`` is gateway-internal only. It works from a registry populated
when a pseudonym is minted, and it is scoped by counterparty: a pseudonym
minted for counterparty X resolves to nothing when presented in
counterparty Y's context -- which is exactly what makes cross-counterparty
linkage fail closed.
"""

import base64
import hashlib
import hmac


class PseudonymService:
    def __init__(self, secret):
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if len(secret) < 16:
            raise ValueError("secret too short")
        self._secret = secret
        # (pseudonym, counterparty_id) -> party_id ; gateway-internal only
        self._registry = {}

    def pseudonym_for(self, party_id, counterparty_id):
        msg = (party_id + "\x00" + counterparty_id).encode("utf-8")
        digest = hmac.new(self._secret, msg, hashlib.sha256).digest()[:12]
        pseudonym = "party_" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self._registry[(pseudonym, counterparty_id)] = party_id
        return pseudonym

    def resolve(self, pseudonym, counterparty_id):
        """Return the real party id, or None. Gateway-internal use only:
        the result must never be returned across the boundary."""
        return self._registry.get((pseudonym, counterparty_id))
