"""
Firebase Cloud Messaging (HTTP v1) — production push for Android and iOS.

Config (environment):
  PUSH_PROVIDER=fcm
  FCM_PROJECT_ID=<firebase project id>
  FCM_SERVICE_ACCOUNT_FILE=/path/to/service-account.json   (mounted secret, never in git)

Auth is an OAuth2 token minted from the service account (google-auth). Token errors
that mean "this device is gone" (UNREGISTERED / INVALID_ARGUMENT on the token) are
reported as invalid_token so the caller deactivates the device.

STATUS: implemented against the documented v1 API; needs a Firebase project and one
end-to-end send to confirm. [VERIFY]
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from .base import PushProvider, PushResult

logger = logging.getLogger("sokopay.push")
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmPushProvider(PushProvider):
    name = "fcm"

    def __init__(self) -> None:
        self.project_id = getattr(settings, "FCM_PROJECT_ID", "")
        self.sa_file = getattr(settings, "FCM_SERVICE_ACCOUNT_FILE", "")
        self._creds = None

    def _access_token(self) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("google-auth is required for FCM: pip install google-auth") from exc
        if self._creds is None:
            self._creds = service_account.Credentials.from_service_account_file(
                self.sa_file, scopes=[_SCOPE])
        if not self._creds.valid:
            self._creds.refresh(Request())
        return self._creds.token

    def send(self, token, title, body, data=None):
        if not (self.project_id and self.sa_file):
            logger.error("FCM not configured (FCM_PROJECT_ID / FCM_SERVICE_ACCOUNT_FILE).")
            return PushResult(success=False, message="push_not_configured")
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                # FCM data values must be strings.
                "data": {k: str(v) for k, v in (data or {}).items()},
                "android": {"priority": "high"},
                "apns": {"payload": {"aps": {"sound": "default"}}},
            }
        }
        try:
            resp = requests.post(
                f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send",
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=10,
            )
        except (requests.RequestException, RuntimeError) as exc:
            logger.exception("FCM send error")
            return PushResult(success=False, message=str(exc))
        if resp.status_code == 200:
            return PushResult(success=True)
        text = resp.text[:300]
        dead = resp.status_code == 404 or "UNREGISTERED" in text or (
            resp.status_code == 400 and "token" in text.lower())
        logger.warning("FCM send failed %s: %s", resp.status_code, text)
        return PushResult(success=False, invalid_token=dead, message=str(resp.status_code))
