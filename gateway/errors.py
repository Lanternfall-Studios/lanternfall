"""Gateway error types.

Errors that cross the gateway boundary are plain codes only (brief section
4.2): ``not_found``, ``not_yours``, ``not_allowed``. ``not_found`` and
``not_yours`` deliberately collapse into one uniform response so a caller
probing IDs cannot tell whether an ID exists. The internal ``reason`` is
for gateway-side debugging only and is never part of the outward message.
"""


class GatewayError(Exception):
    """An error whose outward face is just its code."""

    def __init__(self, code, reason=None):
        self.code = code
        self.reason = reason
        # The outward message IS the code: no detail leaks across.
        super().__init__(code)


class UniformNotFound(GatewayError):
    """Raised for unknown AND for not-yours lookups, identically."""

    def __init__(self, reason=None):
        super().__init__("not_found", reason=reason)


class NotAllowed(GatewayError):
    def __init__(self, reason=None):
        super().__init__("not_allowed", reason=reason)
