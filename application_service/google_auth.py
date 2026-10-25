"""Small Google OAuth access-token provider for long-running repositories."""

import base64
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


class ServiceAccountToken:
    """Mint a Drive access token from a service-account key."""

    def __init__(self, info, opener=None, sign=None):
        if not info.get("client_email") or not info.get("private_key"):
            raise RuntimeError("Service-account JSON requires client_email and private_key")
        self.info = info
        timeout = float(os.environ.get("GOOGLE_API_TIMEOUT_SECONDS", "30"))
        if timeout <= 0:
            raise ValueError("Google API timeout must be positive")
        self.opener = opener or partial(urllib.request.urlopen, timeout=timeout)
        self.sign = sign or _sign_rs256
        self._access_token = None
        self._expires_at = 0

    def token(self):
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        now = int(time.time())
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(json.dumps({
            "iss": self.info["client_email"],
            "scope": "https://www.googleapis.com/auth/drive",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }, separators=(",", ":")).encode())
        signing = header + b"." + claims
        assertion = signing + b"." + _b64(self.sign(self.info["private_key"], signing))
        payload = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion.decode(),
        }).encode()
        request = urllib.request.Request("https://oauth2.googleapis.com/token", data=payload, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.opener(request) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("access_token"):
            raise RuntimeError("Google token response did not include access_token")
        self._access_token = result["access_token"]
        self._expires_at = time.time() + int(result.get("expires_in", 3600))
        return self._access_token


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _sign_rs256(private_key, payload):
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        return _openssl_sign(private_key, payload)
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    return key.sign(payload, padding.PKCS1v15(), hashes.SHA256())


def _openssl_sign(private_key, payload):
    import subprocess
    import tempfile
    handle = tempfile.NamedTemporaryFile(prefix="ajss-sa-", suffix=".pem", delete=False)
    path = handle.name
    try:
        handle.write(private_key.encode())
        handle.close()
        os.chmod(path, 0o600)
        completed = subprocess.run(
            ["openssl", "dgst", "-binary", "-sha256", "-sign", path],
            input=payload, capture_output=True, check=False)
    finally:
        os.unlink(path)
    if completed.returncode != 0:
        raise RuntimeError("Could not sign the service-account assertion")
    return completed.stdout


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
    raw = environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from None
        return ServiceAccountToken(info, opener=opener)
    return None


def access_token_value(access_token):
    return access_token.token() if hasattr(access_token, "token") else access_token
