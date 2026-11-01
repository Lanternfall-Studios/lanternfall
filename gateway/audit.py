"""Audit logging that doesn't leak (brief sections 3.2 rule 7, 4.4).

An audit entry contains exactly: timestamp, actor pseudonym, counterparty
pseudonym, action, result code, and the *names* of fields disclosed.
Never values, never prompts, never payloads, never real party ids -- the
API does not accept them, so a caller cannot log them by accident.

Entries are kept in memory and, if a path is given, appended as JSONL.
Retention/deletion scheduling (brief default: 30 days) is an operator
concern and is deliberately not implemented here; see gateway/README.md.
"""

import json
import re
import time

_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_ENTRY_KEYS = ("ts", "actor_pseudonym", "counterparty_pseudonym",
               "action", "result", "fields_disclosed")


class AuditLog:
    def __init__(self, path=None, clock=time.time):
        self._path = path
        self._clock = clock
        self._entries = []

    def record(self, *, actor_pseudonym, action, result, fields_disclosed,
               counterparty_pseudonym=None):
        fields = sorted(fields_disclosed)
        for name in fields:
            if not _FIELD_NAME.match(name):
                raise ValueError("fields_disclosed must be field names only")
        entry = {
            "ts": float(self._clock()),
            "actor_pseudonym": actor_pseudonym,
            "counterparty_pseudonym": counterparty_pseudonym,
            "action": action,
            "result": result,
            "fields_disclosed": fields,
        }
        self._entries.append(entry)
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self):
        return [dict(e) for e in self._entries]

    def to_json(self):
        """Whole log as one JSON string -- used by the hygiene tests to
        prove no sensitive value appears anywhere in it."""
        return json.dumps(self._entries, sort_keys=True)
