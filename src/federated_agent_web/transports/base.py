"""Transport interface (§11).

The core transport API operates on complete signed documents and preserves
delivery state per document. Transports never interpret prompt content as
authority; core verification happens before dispatch. Acknowledgements are per
message — never "clear the whole outbox" — and partial delivery failure must
preserve every unacknowledged message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = ["Transport", "TransportSendResult", "TransportEnvelope"]


@dataclass(frozen=True)
class TransportSendResult:
    message_id: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class TransportEnvelope:
    message_id: str
    document_bytes: bytes
    destination: str
    source: str | None = None


class Transport(ABC):
    """Minimum abstract operations every FAW transport adapter must provide."""

    @abstractmethod
    def send(self, document: bytes, destination: str) -> TransportSendResult:
        """Deliver a complete signed document; preserve it on failure."""

    @abstractmethod
    def poll(self) -> list[TransportEnvelope]:
        """Return all undelivered envelopes (duplicate delivery is safe)."""

    @abstractmethod
    def ack(self, transport_message_id: str) -> None:
        """Acknowledge exactly one message; remove it from the delivery set."""

    @abstractmethod
    def nack(self, transport_message_id: str, reason: str) -> None:
        """Negatively acknowledge one message; retain it for inspection."""
