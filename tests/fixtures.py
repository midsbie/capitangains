"""Back-compat shim: the doubles moved to tests.support.doubles (Issue #1, Step 7
deletes this file once its importers are repointed)."""

from __future__ import annotations

from tests.support.doubles import Trade, Transfer

__all__ = ["Trade", "Transfer"]
