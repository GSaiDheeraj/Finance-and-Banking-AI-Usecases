"""
API-layer exceptions and their HTTP mapping.

Route handlers and the functions they call raise these at the point of failure; only
`main.py`'s exception handlers decide the resulting HTTP status. Business logic never
returns a status code or catches these to build a response itself — that would scatter
the same policy decision ("not found -> 404") across every route that could hit it.
"""
from __future__ import annotations


class AssessmentNotFoundError(LookupError):
    def __init__(self, version_id: str):
        super().__init__(f"Assessment {version_id!r} not found.")
        self.version_id = version_id


class AssessmentNotReadyError(RuntimeError):
    """The assessment exists but hasn't finished processing yet (no result to act on)."""

    def __init__(self, version_id: str, status: str):
        super().__init__(f"Assessment {version_id!r} is not ready yet (status={status!r}).")
        self.version_id = version_id
        self.status = status
