"""Small Google OAuth access-token provider for long-running repositories."""

import json
import os
import time
from functools import partial
import urllib.parse
import urllib.request


class GoogleAccessToken:
    def __init__(self, refresh_token, client_id, client_secret, opener=None):
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        timeout = float(os.environ.get('GOOGLE_API_TIMEOUT_SECONDS', '30'))
        if timeout <= 0:
            raise ValueError('Google API timeout must be positive')
        self.opener = opener or partial(urllib.request.urlopen, timeout=timeout)
        self._access_token = None
        self._expires_at = 0

    def token(self):
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        payload = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            method="POST",
            data=payload,
        )
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.opener(request) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("access_token"):
            raise RuntimeError("Google token refresh response did not include access_token")
        self._access_token = result["access_token"]
        self._expires_at = time.time() + int(result.get("expires_in", 3600))
        return self._access_token


def token_from_environment(environ=None, opener=None):
    environ = os.environ if environ is None else environ
    direct = environ.get("GOOGLE_ACCESS_TOKEN")
    if direct:
        return direct
    refresh_fields = [
        environ.get("GOOGLE_REFRESH_TOKEN"),
        environ.get("GOOGLE_CLIENT_ID"),
        environ.get("GOOGLE_CLIENT_SECRET"),
    ]
    if all(refresh_fields):
        return GoogleAccessToken(*refresh_fields, opener=opener)
    if any(refresh_fields):
        raise RuntimeError(
            "Google refresh authentication requires GOOGLE_REFRESH_TOKEN, "
            "GOOGLE_CLIENT_ID, and GOOGLE_CLIENT_SECRET"
        )
    return None


def access_token_value(access_token):
    return access_token.token() if hasattr(access_token, "token") else access_token
