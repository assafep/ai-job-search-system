import json

from application_service.google_auth import ServiceAccountToken, token_from_environment


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def test_service_account_assertion_uses_the_client_email():
    seen = []

    def opener(request, timeout=None):
        seen.append(request.data.decode())
        return Response({"access_token": "minted", "expires_in": 3600})

    token = ServiceAccountToken(
        {"client_email": "writer@example.test", "private_key": "unused"},
        opener=opener, sign=lambda key, payload: b"sig")
    assert token.token() == "minted"
    assert "grant_type=" in seen[0] and "assertion=" in seen[0]
    assert token.token() == "minted"
    assert len(seen) == 1


def test_environment_selects_the_service_account_when_refresh_is_absent():
    provider = token_from_environment({
        "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps({
            "client_email": "writer@example.test", "private_key": "pem"}),
    })
    assert isinstance(provider, ServiceAccountToken)


if __name__ == "__main__":
    test_service_account_assertion_uses_the_client_email()
    test_environment_selects_the_service_account_when_refresh_is_absent()
    print("ok")
