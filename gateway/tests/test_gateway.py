"""Unit tests for gateway v1. Standard-library unittest only.

Run from the repo root:
    python3 -B -m unittest discover -s gateway/tests -v
    python3 -B -m unittest gateway.tests.test_gateway -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import (  # noqa: E402
    AuditLog, FakeVerifier, Gateway, GatewayError, PseudonymService,
    TokenService,
)
from gateway.filters import filter_request  # noqa: E402

SIGNING_KEY = b"test-signing-key-0123456789abcdef"
PSEUDO_SECRET = b"test-pseudonym-secret-0123456789"


class Clock:
    """Mutable fake clock for expiry/revocation tests."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_gateway(clock=None):
    clock = clock or Clock()
    tokens = TokenService(SIGNING_KEY, clock=clock)
    pseudos = PseudonymService(PSEUDO_SECRET)
    verifier = FakeVerifier()
    gateway = Gateway(tokens, pseudos, verifier=verifier,
                      audit_log=AuditLog(clock=clock))
    for party in ("client-REAL-1", "client-REAL-2", "vendor-REAL-1"):
        gateway.register_party(party)
    return gateway, tokens, pseudos, verifier, clock


class TokenScopeTests(unittest.TestCase):
    def test_scope_enforced_by_token_service(self):
        tokens = TokenService(SIGNING_KEY, clock=Clock())
        token = tokens.issue("client-REAL-1", ["catalogue:read"])
        claims = tokens.verify(token, required_scope="catalogue:read")
        self.assertEqual(claims.party_id, "client-REAL-1")
        with self.assertRaises(GatewayError) as ctx:
            tokens.verify(token, required_scope="order:create")
        self.assertEqual(ctx.exception.code, "not_allowed")

    def test_scope_enforced_through_gateway(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["catalogue:read"])
        vendor_pseudo = gateway.pseudonym_for("vendor-REAL-1", "client-REAL-1")
        # In-scope call succeeds...
        result = gateway.handle(
            "catalogue_read", token, {"vendor": vendor_pseudo})
        self.assertIn("item", result)
        # ...the same token creating an order does not.
        with self.assertRaises(GatewayError) as ctx:
            gateway.handle("order_create", token,
                           {"vendor": vendor_pseudo, "item": "lantern",
                            "qty": 1, "delivery_handle": "drop_1"})
        self.assertEqual(ctx.exception.code, "not_allowed")

    def test_party_comes_from_token_not_payload(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["balance:read"])
        result = gateway.handle("balance_read", token, {})
        self.assertEqual(result, {"balance": 250})
        # A payload cannot widen a call to another party: 'vendor' is not
        # even an allowed field for balance_read.
        with self.assertRaises(GatewayError):
            gateway.handle("balance_read", token,
                           {"vendor": "client-REAL-2"})


class TokenExpiryRevocationTests(unittest.TestCase):
    def test_expired_token_rejected(self):
        clock = Clock()
        tokens = TokenService(SIGNING_KEY, clock=clock)
        token = tokens.issue("client-REAL-1", ["catalogue:read"],
                             ttl_seconds=60)
        tokens.verify(token)  # valid at t=1000
        clock.now = 1061.0
        with self.assertRaises(GatewayError) as ctx:
            tokens.verify(token)
        self.assertEqual(ctx.exception.code, "not_allowed")

    def test_single_token_revocation(self):
        tokens = TokenService(SIGNING_KEY, clock=Clock())
        token = tokens.issue("client-REAL-1", ["catalogue:read"])
        tokens.revoke(token)
        with self.assertRaises(GatewayError):
            tokens.verify(token)

    def test_revoke_party_kill_switch(self):
        gateway, tokens, *_rest, clock = make_gateway()
        token_a = gateway.issue_token("client-REAL-1", ["balance:read"])
        token_b = gateway.issue_token("client-REAL-2", ["balance:read"])
        gateway.revoke_party("client-REAL-1")
        with self.assertRaises(GatewayError):
            gateway.handle("balance_read", token_a, {})
        # Everyone else is unaffected (brief section 7, test 7).
        self.assertEqual(gateway.handle("balance_read", token_b, {}),
                         {"balance": 250})

    def test_gateway_freeze(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["balance:read"])
        gateway.freeze()
        with self.assertRaises(GatewayError) as ctx:
            gateway.handle("balance_read", token, {})
        self.assertEqual(ctx.exception.code, "not_allowed")
        gateway.unfreeze()
        self.assertEqual(gateway.handle("balance_read", token, {}),
                         {"balance": 250})

    def test_tampered_token_rejected(self):
        tokens = TokenService(SIGNING_KEY, clock=Clock())
        token = tokens.issue("client-REAL-1", ["catalogue:read"])
        body, sig = token.split(".")
        forged = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
        with self.assertRaises(GatewayError):
            tokens.verify(forged)


class PseudonymTests(unittest.TestCase):
    def test_unlinkable_across_counterparties(self):
        pseudos = PseudonymService(PSEUDO_SECRET)
        at_a = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-1")
        at_b = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-2")
        self.assertNotEqual(at_a, at_b)

    def test_stable_for_same_pair(self):
        pseudos = PseudonymService(PSEUDO_SECRET)
        first = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-1")
        again = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-1")
        self.assertEqual(first, again)

    def test_pseudonym_hides_real_id(self):
        pseudos = PseudonymService(PSEUDO_SECRET)
        pseudo = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-1")
        self.assertNotIn("client-REAL-1", pseudo)
        self.assertNotIn("REAL", pseudo)
        self.assertTrue(pseudo.startswith("party_"))

    def test_resolve_is_counterparty_scoped(self):
        pseudos = PseudonymService(PSEUDO_SECRET)
        pseudo = pseudos.pseudonym_for("client-REAL-1", "vendor-REAL-1")
        self.assertEqual(pseudos.resolve(pseudo, "vendor-REAL-1"),
                         "client-REAL-1")
        # Minted for vendor-1: resolves to nothing in vendor-2's context.
        self.assertIsNone(pseudos.resolve(pseudo, "vendor-REAL-2"))


class RequestFilterTests(unittest.TestCase):
    def test_never_receive_fields_rejected(self):
        for field in ("email", "real_name", "payment_details", "uuid",
                      "prompt", "margin", "card_number"):
            with self.assertRaises(GatewayError, msg=field) as ctx:
                filter_request("order_create",
                               {"vendor": "party_x", "item": "lantern",
                                "qty": 1, "delivery_handle": "drop_1",
                                field: "canary"})
            self.assertEqual(ctx.exception.code, "not_allowed")

    def test_never_receive_nested_field_rejected(self):
        with self.assertRaises(GatewayError):
            filter_request("order_create",
                           {"vendor": "party_x", "item": "lantern", "qty": 1,
                            "delivery_handle": {"prompt": "nested canary"}})

    def test_unlisted_field_rejected(self):
        with self.assertRaises(GatewayError) as ctx:
            filter_request("catalogue_read",
                           {"vendor": "party_x", "bogus_field": 1})
        self.assertEqual(ctx.exception.code, "not_allowed")

    def test_never_receive_rejected_through_gateway(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["order:create"])
        vendor_pseudo = gateway.pseudonym_for("vendor-REAL-1", "client-REAL-1")
        with self.assertRaises(GatewayError):
            gateway.handle("order_create", token,
                           {"vendor": vendor_pseudo, "item": "lantern",
                            "qty": 1, "delivery_handle": "drop_1",
                            "email": "canary@example.com"})


class AuditHygieneTests(unittest.TestCase):
    def test_audit_records_names_and_pseudonyms_never_values(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["order:create"])
        vendor_pseudo = gateway.pseudonym_for("vendor-REAL-1", "client-REAL-1")
        canary_handle = "drop_CANARY_HANDLE_123"
        canary_item = "itm_CANARY_ITEM_456"
        result = gateway.handle(
            "order_create", token,
            {"vendor": vendor_pseudo, "item": canary_item, "qty": 2,
             "delivery_handle": canary_handle},
            signature="sig_CANARY_SIGNATURE_789")
        self.assertEqual(result["status"], "accepted")

        log_text = gateway.audit_log.to_json()
        # No request values, no real ids, no signatures anywhere in the log.
        self.assertNotIn("CANARY", log_text)
        self.assertNotIn("client-REAL-1", log_text)
        self.assertNotIn("vendor-REAL-1", log_text)
        # What IS recorded: pseudonym, action, outcome, field names only.
        (entry,) = gateway.audit_log.entries()
        self.assertTrue(entry["actor_pseudonym"].startswith("party_"))
        self.assertEqual(entry["action"], "order_create")
        self.assertEqual(entry["result"], "ok")
        self.assertEqual(entry["fields_disclosed"],
                         ["order", "status", "total"])
        self.assertEqual(
            set(entry.keys()),
            {"ts", "actor_pseudonym", "counterparty_pseudonym", "action",
             "result", "fields_disclosed"})

    def test_rejected_calls_are_audited_without_values(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["balance:read"])
        with self.assertRaises(GatewayError):
            gateway.handle("balance_read", token,
                           {"email": "canary@example.com"})
        log_text = gateway.audit_log.to_json()
        self.assertNotIn("canary@example.com", log_text)
        (entry,) = gateway.audit_log.entries()
        self.assertEqual(entry["result"], "not_allowed")
        self.assertEqual(entry["fields_disclosed"], [])


class UniformErrorTests(unittest.TestCase):
    def test_unknown_and_foreign_pseudonyms_are_indistinguishable(self):
        gateway, *_ = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["catalogue:read"])
        # A pseudonym for the vendor minted for a DIFFERENT client:
        foreign = gateway.pseudonym_for("vendor-REAL-1", "client-REAL-2")
        errors = []
        for pseudo in ("party_never_existed", foreign):
            with self.assertRaises(GatewayError) as ctx:
                gateway.handle("catalogue_read", token, {"vendor": pseudo})
            errors.append(ctx.exception)
        self.assertEqual(errors[0].code, "not_found")
        self.assertEqual(errors[0].code, errors[1].code)
        self.assertEqual(str(errors[0]), str(errors[1]))


class ForwardingTests(unittest.TestCase):
    def test_verifier_sees_pseudonym_and_filtered_payload_only(self):
        gateway, _t, _p, verifier, _c = make_gateway()
        token = gateway.issue_token("client-REAL-1", ["order:create"])
        vendor_pseudo = gateway.pseudonym_for("vendor-REAL-1", "client-REAL-1")
        gateway.handle("order_create", token,
                       {"vendor": vendor_pseudo, "item": "lantern",
                        "qty": 3, "delivery_handle": "drop_9"},
                       signature="opaque-signature")
        (op, actor_pseudo, payload, sig), = verifier.submissions
        self.assertEqual(op, "order_create")
        self.assertTrue(actor_pseudo.startswith("party_"))
        self.assertNotIn("REAL", actor_pseudo)
        self.assertEqual(payload["vendor"], vendor_pseudo)
        self.assertEqual(sig, "opaque-signature")

    def test_response_is_minimised_to_allowlist(self):
        clock = Clock()
        tokens = TokenService(SIGNING_KEY, clock=clock)
        pseudos = PseudonymService(PSEUDO_SECRET)
        leaky = FakeVerifier(responses={
            "balance_read": {"balance": 250, "internal_account_note": "x",
                             "debug_trace": "y"}})
        gateway = Gateway(tokens, pseudos, verifier=leaky,
                          audit_log=AuditLog(clock=clock))
        gateway.register_party("client-REAL-1")
        token = gateway.issue_token("client-REAL-1", ["balance:read"])
        self.assertEqual(gateway.handle("balance_read", token, {}),
                         {"balance": 250})


if __name__ == "__main__":
    unittest.main()
