"""
Request signing and webhook signature verification for partner APIs.

Partners differ in *what* they sign and *where* the signature goes, so each partner
adapter picks a signer and tells it the message layout. The cryptography is the same:
HMAC-SHA256 with a shared secret, compared in constant time.

The default canonical request is:

    <METHOD>\n<path?query>\n<unix-timestamp>\n<nonce>\n<sha256-hex(body)>

Timestamp + nonce let the partner reject replays. Replace `canonical()` in a partner
adapter if the partner's documented layout differs. [VERIFY per partner]
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(secret: str, message: bytes, *, encoding: str = "hex") -> str:
    digest = hmac.new(secret.encode(), message, hashlib.sha256)
    return digest.hexdigest() if encoding == "hex" else base64.b64encode(digest.digest()).decode()


class HmacRequestSigner:
    """Adds `Authorization: <scheme> <client_id>:<signature>` plus timestamp/nonce headers."""

    def __init__(self, client_id: str, secret: str, *, scheme: str = "HMAC",
                 encoding: str = "hex", timestamp_header: str = "X-Timestamp",
                 nonce_header: str = "X-Nonce", clock=time.time):
        self.client_id = client_id
        self.secret = secret
        self.scheme = scheme
        self.encoding = encoding
        self.timestamp_header = timestamp_header
        self.nonce_header = nonce_header
        self.clock = clock

    @staticmethod
    def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
        return "\n".join([method, path, timestamp, nonce, sha256_hex(body)]).encode()

    def sign(self, method: str, path: str, body: bytes) -> dict:
        timestamp = str(int(self.clock()))
        nonce = secrets.token_hex(12)
        sig = hmac_sha256(self.secret, self.canonical(method, path, timestamp, nonce, body),
                          encoding=self.encoding)
        return {
            "Authorization": f"{self.scheme} {self.client_id}:{sig}",
            self.timestamp_header: timestamp,
            self.nonce_header: nonce,
        }


def verify_webhook_signature(secret: str, body: bytes, sent: str, *, encoding: str = "hex",
                             timestamp: str | None = None, tolerance: int = 300,
                             clock=time.time) -> bool:
    """
    HMAC-SHA256 over the raw body (or "<timestamp>.<body>" when the partner signs a
    timestamp too, which also bounds replay). Constant-time comparison. Never raises.
    """
    if not secret or not sent:
        return False
    message = body
    if timestamp is not None:
        try:
            if abs(clock() - int(timestamp)) > tolerance:
                return False
        except (TypeError, ValueError):
            return False
        message = timestamp.encode() + b"." + body
    expected = hmac_sha256(secret, message, encoding=encoding)
    sent = sent.strip()
    for prefix in ("sha256=", "SHA256="):
        if sent.startswith(prefix):
            sent = sent[len(prefix):]
    if encoding == "hex":
        sent = sent.lower()
    return hmac.compare_digest(expected, sent)
