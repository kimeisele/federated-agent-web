"""Ed25519 signing, verification, and key identifiers (§7.2, §6).

Keys are raw 32-byte Ed25519 secrets / 32-byte public keys. The key ID
``kid`` is ``sha256:<hex>`` over the raw public key bytes. Signatures and key
encodings use base64url without padding.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

__all__ = [
    "generate_keypair",
    "public_key_from_raw",
    "private_key_from_raw",
    "kid_for",
    "b64url_encode",
    "b64url_decode",
    "sign_canonical",
    "verify_canonical",
]

ED25519_PUBLIC_KEY_LEN = 32
ED25519_SIGNATURE_LEN = 64


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_raw, public_raw)`` for a fresh Ed25519 key."""
    private = Ed25519PrivateKey.generate()
    return private.private_bytes_raw(), private.public_key().public_bytes_raw()


def private_key_from_raw(raw: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_from_raw(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)


def kid_for(public_raw: bytes) -> str:
    """Return ``sha256:<hex>`` over the raw 32-byte public key (§6)."""
    return "sha256:" + hashlib.sha256(public_raw).hexdigest()


def b64url_encode(data: bytes) -> str:
    """Base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Decode base64url without padding; raises ValueError on invalid input."""
    pad = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + pad)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"invalid base64url value: {exc}") from exc


def sign_canonical(canonical: bytes, private_raw: bytes) -> str:
    """Sign canonical bytes with Ed25519; return base64url without padding."""
    signature = private_key_from_raw(private_raw).sign(canonical)
    return b64url_encode(signature)


def verify_canonical(canonical: bytes, signature_b64url: str, public_raw: bytes) -> bool:
    """Verify an Ed25519 signature over canonical bytes. Never raises."""
    try:
        signature = b64url_decode(signature_b64url)
        public_key_from_raw(public_raw).verify(signature, canonical)
        return True
    except (InvalidSignature, ValueError):
        return False
