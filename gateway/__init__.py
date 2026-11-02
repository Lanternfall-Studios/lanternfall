"""Lanternfall integration gateway v1.

One strict door between the game side and any backend/verifier, per the
backend-integration & privacy brief: per-party scoped tokens,
per-counterparty pseudonyms, field allowlists with a never-receive list,
value-free audit logging, and a forwarding seam toward the core signed
verifier. Standard library only. See gateway/README.md.
"""

from .audit import AuditLog
from .errors import GatewayError, NotAllowed, UniformNotFound
from .filters import NEVER_RECEIVE_FIELDS, OPERATIONS, REQUIRED_SCOPES
from .gateway import Gateway
from .pseudonyms import PseudonymService
from .tokens import TokenClaims, TokenService
from .verifier import FakeVerifier, UnavailableVerifier, VerifierClient

__all__ = [
    "AuditLog",
    "FakeVerifier",
    "Gateway",
    "GatewayError",
    "NEVER_RECEIVE_FIELDS",
    "NotAllowed",
    "OPERATIONS",
    "PseudonymService",
    "REQUIRED_SCOPES",
    "TokenClaims",
    "TokenService",
    "UnavailableVerifier",
    "UniformNotFound",
    "VerifierClient",
]
