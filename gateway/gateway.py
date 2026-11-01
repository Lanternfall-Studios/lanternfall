"""The gateway itself: one strict door (brief section 3).

``Gateway.handle`` is the only entry point. For every call it:

1. verifies the caller's token (party and scope come from the token,
   never from the payload);
2. resolves any counterparty pseudonym in the payload *in the caller's
   context* -- an unresolvable pseudonym, whether it never existed or was
   minted for someone else, fails with the identical uniform
   ``not_found`` (brief sections 4.2 and 7 test 1/6);
3. enforces the per-operation field allowlist and never-receive list;
4. forwards only the filtered payload, under the caller's pseudonym, to
   the verifier seam;
5. minimises the response through the response allowlist;
6. writes a value-free audit entry either way.

Also here: the gateway freeze switch (brief section 3.2 rule 10) and the
per-party kill switch (delegated to the token service).
"""

from .audit import AuditLog
from .errors import GatewayError, NotAllowed, UniformNotFound
from .filters import REQUIRED_SCOPES, filter_request, filter_response
from .pseudonyms import PseudonymService
from .tokens import TokenService
from .verifier import UnavailableVerifier

# Payload fields that carry a counterparty pseudonym, per operation.
_PSEUDONYM_FIELDS = ("vendor", "counterparty")


class Gateway:
    def __init__(self, token_service, pseudonym_service, verifier=None,
                 audit_log=None):
        self._tokens = token_service
        self._pseudonyms = pseudonym_service
        self._verifier = verifier if verifier is not None else UnavailableVerifier()
        self._audit = audit_log if audit_log is not None else AuditLog()
        self._parties = set()
        self._frozen = False

    # -- operator surface -------------------------------------------------
    def register_party(self, party_id):
        self._parties.add(party_id)

    def issue_token(self, party_id, scopes, ttl_seconds=3600):
        if party_id not in self._parties:
            raise UniformNotFound(reason="unregistered_party")
        return self._tokens.issue(party_id, scopes, ttl_seconds=ttl_seconds)

    def pseudonym_for(self, party_id, counterparty_id):
        return self._pseudonyms.pseudonym_for(party_id, counterparty_id)

    def revoke_party(self, party_id):
        """Kill switch, one party: all its existing tokens stop working."""
        self._tokens.revoke_party(party_id)

    def freeze(self):
        """Kill switch, whole gateway: every call returns not_allowed."""
        self._frozen = True

    def unfreeze(self):
        self._frozen = False

    @property
    def audit_log(self):
        return self._audit

    # -- the one door ------------------------------------------------------
    def handle(self, operation, token, payload, signature=""):
        if not isinstance(payload, dict):
            payload = {}
        attempted_fields = sorted(payload.keys())
        actor_pseudonym = "party_unknown"
        counterparty_pseudonym = None
        try:
            if self._frozen:
                raise NotAllowed(reason="gateway_frozen")
            if operation not in REQUIRED_SCOPES:
                raise NotAllowed(reason="unknown_operation")
            claims = self._tokens.verify(
                token, required_scope=REQUIRED_SCOPES[operation])
            actor = claims.party_id
            actor_pseudonym = self._pseudonyms.pseudonym_for(actor, "gateway")
            if actor not in self._parties:
                raise UniformNotFound(reason="unregistered_actor")

            # Resolve counterparty pseudonyms in the actor's context.
            counterparty_id = None
            for field in _PSEUDONYM_FIELDS:
                if field in payload:
                    resolved = self._pseudonyms.resolve(payload[field], actor)
                    if resolved is None:
                        # Unknown and not-yours are indistinguishable.
                        raise UniformNotFound(reason="unresolvable_pseudonym")
                    counterparty_id = resolved
            if counterparty_id is not None:
                actor_pseudonym = self._pseudonyms.pseudonym_for(
                    actor, counterparty_id)
                counterparty_pseudonym = self._pseudonyms.pseudonym_for(
                    counterparty_id, actor)

            filtered = filter_request(operation, payload)
            response = self._verifier.submit(
                operation, actor_pseudonym, filtered, signature)
            minimised = filter_response(operation, response)
        except GatewayError as err:
            self._audit.record(
                actor_pseudonym=actor_pseudonym,
                counterparty_pseudonym=counterparty_pseudonym,
                action=operation,
                result=err.code,
                fields_disclosed=[],
            )
            raise
        self._audit.record(
            actor_pseudonym=actor_pseudonym,
            counterparty_pseudonym=counterparty_pseudonym,
            action=operation,
            result="ok",
            fields_disclosed=sorted(minimised.keys()),
        )
        return minimised
