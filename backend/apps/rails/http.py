"""
Hardened HTTP client for payment-partner APIs.

Rules it enforces, because money calls are not like other HTTP calls:

* HTTPS only, certificate verification always on (the old codebase used verify=False).
* Short connect timeout, bounded read timeout, bounded response size.
* **Money-moving POSTs are never retried automatically.** A timeout after the request
  left us means "we don't know if the partner acted" — the caller records the outcome
  as UNKNOWN and resolves it by re-querying status (which is safe to retry) and, failing
  that, by reconciliation. Retrying the POST could pay someone twice.
* Read-only GETs (status, reports) are retried with a short backoff.
* Every request is signed (see signing.py) and carries our idempotency key.
* Logs say what happened (method, path, status, duration) — never bodies, keys or
  phone numbers.

The transport is injectable so tests (and the contract-test harness) can replay
partner responses without the network.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode, urlsplit

import requests

from .exceptions import RailConfigError, RailError

logger = logging.getLogger("sokopay.rails.http")

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
GET_RETRIES = 2
GET_BACKOFF = (0.5, 1.5)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RailError("Partner returned a non-JSON response.") from exc
        return data if isinstance(data, dict) else {"data": data}


class RailTransportError(RailError):
    """
    The call did not produce an HTTP response.

    `maybe_sent` says whether the partner might have received (and acted on) the request:
    False only when we know the connection never opened (e.g. connect timeout / DNS).
    """

    def __init__(self, message: str, *, maybe_sent: bool):
        super().__init__(message)
        self.maybe_sent = maybe_sent


# A transport takes (method, url, headers, body, timeout) and returns HttpResponse,
# raising RailTransportError on network failure.
Transport = Callable[[str, str, dict, bytes, tuple], HttpResponse]


def requests_transport(session: requests.Session | None = None) -> Transport:
    session = session or requests.Session()
    session.trust_env = False            # no proxy/CA overrides from the environment

    def send(method: str, url: str, headers: dict, body: bytes, timeout: tuple) -> HttpResponse:
        try:
            resp = session.request(method, url, headers=headers, data=body or None,
                                   timeout=timeout, stream=True, verify=True,
                                   allow_redirects=False)
        except (requests.ConnectTimeout, requests.exceptions.SSLError) as exc:
            raise RailTransportError(f"{type(exc).__name__}", maybe_sent=False) from exc
        except requests.RequestException as exc:   # read timeout, reset, etc.
            raise RailTransportError(f"{type(exc).__name__}", maybe_sent=True) from exc
        try:
            chunks, size = [], 0
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise RailTransportError("response too large", maybe_sent=True)
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise RailTransportError(f"{type(exc).__name__}", maybe_sent=True) from exc
        finally:
            resp.close()
        return HttpResponse(resp.status_code, b"".join(chunks), dict(resp.headers))

    return send


class PartnerHttpClient:
    def __init__(self, *, partner: str, base_url: str, signer, transport: Transport | None = None,
                 allow_insecure_http: bool = False, sleep: Callable[[float], None] = time.sleep):
        parts = urlsplit(base_url or "")
        if parts.scheme != "https" and not (allow_insecure_http and parts.scheme == "http"):
            raise RailConfigError(f"{partner}: base URL must be https://")
        if not parts.netloc:
            raise RailConfigError(f"{partner}: base URL is missing a host")
        self.partner = partner
        self.base_url = base_url.rstrip("/")
        self.base_path = parts.path.rstrip("/")
        self.signer = signer
        self.transport = transport or requests_transport()
        self.sleep = sleep

    def request(self, method: str, path: str, *, json_body: dict | None = None,
                params: dict | None = None, idempotency_key: str | None = None) -> HttpResponse:
        method = method.upper()
        query = f"?{urlencode(sorted((params or {}).items()))}" if params else ""
        url = f"{self.base_url}{path}{query}"
        body = (json.dumps(json_body, separators=(",", ":"), sort_keys=True).encode()
                if json_body is not None else b"")
        attempts = 1 + (GET_RETRIES if method == "GET" else 0)

        for attempt in range(attempts):
            headers = {"Accept": "application/json", "User-Agent": "SokoPay/1.0"}
            if body:
                headers["Content-Type"] = "application/json"
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            # Sign each attempt afresh (new timestamp + nonce).
            headers.update(self.signer.sign(method, f"{self.base_path}{path}{query}", body))
            started = time.monotonic()
            try:
                resp = self.transport(method, url, headers, body, (CONNECT_TIMEOUT, READ_TIMEOUT))
            except RailTransportError as exc:
                logger.warning("rail=%s %s %s failed: %s (attempt %d)",
                               self.partner, method, path.split("?")[0], exc, attempt + 1)
                if attempt + 1 < attempts:
                    self.sleep(GET_BACKOFF[min(attempt, len(GET_BACKOFF) - 1)])
                    continue
                raise
            logger.info("rail=%s %s %s -> %s in %dms", self.partner, method, path.split("?")[0],
                        resp.status, int((time.monotonic() - started) * 1000))
            if method == "GET" and resp.status >= 500 and attempt + 1 < attempts:
                self.sleep(GET_BACKOFF[min(attempt, len(GET_BACKOFF) - 1)])
                continue
            return resp
        raise RailError("unreachable")  # pragma: no cover
