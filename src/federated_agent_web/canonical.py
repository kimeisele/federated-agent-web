"""Strict RFC 8785 (JCS) parsing and canonicalization (§7.1).

The parser rejects duplicate object members, NaN/Infinity, negative zero,
out-of-domain integers, and invalid Unicode (including lone surrogates) at
parse time. Canonical serialization is delegated to the maintained ``rfc8785``
package; ``json.dumps(sort_keys=True)`` is NOT a conforming substitute.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable

import rfc8785

__all__ = [
    "CanonicalizationError",
    "DuplicateMemberError",
    "UnsupportedNumberError",
    "InvalidJsonError",
    "InvalidUnicodeError",
    "parse_strict",
    "canonical_bytes",
    "content_digest",
    "digest_bytes",
    "strip_signature",
]

# JCS numbers are limited to the IEEE-754 double domain; rfc8785 rejects
# integers at or beyond 2^53, so we reject them at parse time uniformly.
_MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalizationError(ValueError):
    """Raised when input cannot be parsed or canonicalized per RFC 8785."""


class DuplicateMemberError(CanonicalizationError):
    """Raised when a JSON object repeats a member name (§7.1)."""


class UnsupportedNumberError(CanonicalizationError):
    """Raised for NaN/Infinity, negative zero, or out-of-domain numbers."""


class InvalidJsonError(CanonicalizationError):
    """Raised for syntactically malformed JSON input."""


class InvalidUnicodeError(CanonicalizationError):
    """Raised for invalid UTF-8 input bytes or lone surrogate code points."""


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise DuplicateMemberError(f"duplicate object member {key!r}")
        obj[key] = value
    return obj


def _reject_non_finite(token: str) -> float:
    raise UnsupportedNumberError(f"non-finite JSON number {token!r} is not allowed")


def _parse_float(token: str) -> float:
    value = float(token)
    if value == 0.0 and math.copysign(1.0, value) < 0.0:
        raise UnsupportedNumberError(f"negative zero {token!r} is not allowed")
    return value


def _parse_int(token: str) -> int:
    if token == "-0":
        raise UnsupportedNumberError("negative zero integer literal '-0' is not allowed")
    value = int(token)
    if abs(value) > _MAX_SAFE_INTEGER:
        raise UnsupportedNumberError(f"integer {token!r} exceeds the JCS number domain")
    return value


def _reject_surrogates(obj: Any) -> None:
    """Reject lone surrogate code points in strings, including member names.

    Python's ``json`` module decodes ``\uD800`` escapes into lone surrogate
    characters without error; RFC 8785 requires valid Unicode input, so the
    parsed structure must be scanned.
    """
    if isinstance(obj, str):
        for char in obj:
            if 0xD800 <= ord(char) <= 0xDFFF:
                raise InvalidUnicodeError("lone surrogate code point is not allowed")
    elif isinstance(obj, dict):
        for key, value in obj.items():
            _reject_surrogates(key)
            _reject_surrogates(value)
    elif isinstance(obj, list):
        for item in obj:
            _reject_surrogates(item)


def parse_strict(data: bytes) -> dict[str, Any]:
    """Parse UTF-8 JSON bytes with all §7.1 rejection rules active.

    Duplicate detection happens here, at parse time, via ``object_pairs_hook``;
    a JCS pass over an already-parsed mapping cannot recover collapsed
    duplicates.

    Failure classes are machine-distinguishable (§7 of the v0.5 profile):
    ``DuplicateMemberError``, ``UnsupportedNumberError``, ``InvalidJsonError``,
    and ``InvalidUnicodeError`` — never classified by message text.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidUnicodeError(f"input is not valid UTF-8: {exc}") from exc
    try:
        obj = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_non_finite,
            parse_float=_parse_float,
            parse_int=_parse_int,
        )
    except (json.JSONDecodeError, CanonicalizationError) as exc:
        if isinstance(exc, CanonicalizationError):
            raise
        raise InvalidJsonError(f"invalid JSON: {exc}") from exc
    _reject_surrogates(obj)
    if not isinstance(obj, dict):
        raise InvalidJsonError("top-level JSON value must be an object")
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Return the RFC 8785 canonical UTF-8 serialization of ``obj``."""
    try:
        return rfc8785.dumps(obj)
    except CanonicalizationError:
        raise
    except Exception as exc:  # rfc8785 raises IntegerDomainError etc.
        raise CanonicalizationError(f"cannot canonicalize value: {exc}") from exc


def strip_signature(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a signed document without its top-level ``signature``."""
    return {key: value for key, value in doc.items() if key != "signature"}


def digest_bytes(data: bytes) -> str:
    """Return ``sha256:<lowercase hex>`` over raw bytes (§7.2, §10)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_digest(doc: dict[str, Any]) -> str:
    """Return ``sha256:<hex>`` over JCS(document minus top-level signature)."""
    return digest_bytes(canonical_bytes(strip_signature(doc)))


def _ensure_callable_used() -> Callable[..., Any]:
    return rfc8785.dumps
