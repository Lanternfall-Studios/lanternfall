"""Field-level disclosure rules (brief sections 4.1, 4.2, 4.4).

Two mechanisms, both enforced in code and covered by tests:

- **Allowlists per operation.** A request may contain only the fields on
  that operation's allowlist; anything else is rejected, not silently
  dropped -- a caller sending extra fields is told ``not_allowed`` so the
  mistake surfaces instead of quietly half-working.
- **The never-receive list.** Fields that must never cross the gateway in
  either direction (real identities, payment details, real database ids,
  prompts/memory of another party's agent, a vendor's costs/margins/stock
  depth...). Presence anywhere in a payload -- top level or nested -- is
  a rejection.

Responses are minimised the other way: anything not on the response
allowlist is stripped, and a never-receive field in a response is a hard
rejection (that direction indicates a backend bug, and failing loud beats
leaking quiet).
"""

from .errors import NotAllowed

NEVER_RECEIVE_FIELDS = frozenset({
    # real identity
    "real_name", "legal_name", "full_name", "email", "email_address",
    "phone", "phone_number",
    # payment
    "payment_details", "payment_method", "card_number",
    "credit_card_number", "bank_account", "billing_address",
    # real identifiers / credentials
    "real_id", "uuid", "database_id", "db_id", "password", "api_key",
    "secret", "signing_key",
    # another party's agent internals
    "prompt", "system_prompt", "instructions", "agent_memory", "memory",
    "conversation",
    # another party's private business / history
    "purchase_history", "order_history", "sales_history", "customer_list",
    "cost", "cost_price", "margin", "stock_depth", "inventory_depth",
    # physical address (a pseudonymous delivery_handle is the allowed form)
    "home_address", "delivery_address", "street_address",
})

# operation -> (allowed request fields, allowed response fields)
# Mirrors the v1 call allowlist in brief section 4.1.
OPERATIONS = {
    "catalogue_read": (
        frozenset({"vendor"}),
        frozenset({"item", "price", "in_stock"}),
    ),
    "order_create": (
        frozenset({"vendor", "item", "qty", "delivery_handle"}),
        frozenset({"order", "status", "total"}),
    ),
    "order_status": (
        frozenset({"order"}),
        frozenset({"order", "status"}),
    ),
    "trade_settle": (
        frozenset({"counterparty", "item", "qty"}),
        frozenset({"settlement", "status"}),
    ),
    "balance_read": (
        frozenset(),
        frozenset({"balance"}),
    ),
}

REQUIRED_SCOPES = {
    "catalogue_read": "catalogue:read",
    "order_create": "order:create",
    "order_status": "order:status",
    "trade_settle": "trade:settle",
    "balance_read": "balance:read",
}


def _scan_never_receive(value, path=""):
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in NEVER_RECEIVE_FIELDS:
                raise NotAllowed(reason="never_receive_field:" + key)
            _scan_never_receive(sub, path + "/" + str(key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan_never_receive(item, path)


def _spec(operation):
    try:
        return OPERATIONS[operation]
    except KeyError:
        raise NotAllowed(reason="unknown_operation")


def filter_request(operation, payload):
    """Validate a request payload; return a clean copy with only the
    allowlisted fields, or raise NotAllowed."""
    allowed, _ = _spec(operation)
    if not isinstance(payload, dict):
        raise NotAllowed(reason="payload_not_object")
    _scan_never_receive(payload)
    unknown = set(payload) - allowed
    if unknown:
        raise NotAllowed(reason="field_not_allowed:" + ",".join(sorted(unknown)))
    return {k: payload[k] for k in payload if k in allowed}


def filter_response(operation, payload):
    """Minimise a response payload; return only allowlisted fields, or
    raise NotAllowed if a never-receive field is present."""
    _, allowed = _spec(operation)
    if not isinstance(payload, dict):
        raise NotAllowed(reason="response_not_object")
    _scan_never_receive(payload)
    return {k: payload[k] for k in payload if k in allowed}
