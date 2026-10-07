"""Fail-closed compatibility shim; portable identity comes from ReleaseTrust."""
from __future__ import annotations
from dataclasses import dataclass

class CompatProfileError(ValueError):
    """Legacy implicit/staging identity selection is unavailable."""

@dataclass(frozen=True)
class CompatIdentity:
    public_alias: str
    staging: bool
    profile_path: str | None = None
    profile_sha256: str | None = None
    profile_id: str | None = None

def resolve_compat_identity(manifest_alias: str, *, environ=None, root=None) -> CompatIdentity:
    raise CompatProfileError("implicit identity selection is unavailable; use portable ReleaseTrust-validated Settings")
