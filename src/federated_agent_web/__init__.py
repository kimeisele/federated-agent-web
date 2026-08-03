"""Federated Agent Web — transport-agnostic delegation contract (v0.2).

Reference implementation of the FAW build specification. See SPEC.md for the
normative contract and CONFORMANCE.md for the conformance claims.
"""

__version__ = "0.2.0"

from . import canonical, crypto, documents, evidence, identity, pending, replay, runner, verify  # noqa: F401

__all__ = ["canonical", "crypto", "documents", "identity", "pending", "replay", "evidence", "runner", "verify"]
