"""Single-installation MCP OAuth with PKCE, explicit owner login and durable state.

The MCP SDK owns OAuth protocol validation. The service token authenticates the
installation owner on the consent page; it is never sent to a client or callback.
OAuth credentials are separate, short-lived, and scoped to this MCP resource.
"""
import hashlib
import hmac
import os
import secrets
import time
from html import escape
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, TokenError,
)
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

SCOPE = 'ajss'


def owner_token():
    value = (os.environ.get('APPLICATION_SERVICE_TOKEN') or os.environ.get('PERSISTENCE_API_TOKEN')
             or os.environ.get('AJSS_API_TOKEN'))
    if not value:
        raise ValueError('MCP requires a configured service token')
    return value


def issuer_url():
    value = os.environ.get('AJSS_MCP_ISSUER')
    if not value and os.environ.get('VERCEL_PROJECT_PRODUCTION_URL'):
        value = 'https://' + os.environ['VERCEL_PROJECT_PRODUCTION_URL']
    parsed = urlsplit(value or '')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/')
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('AJSS_MCP_ISSUER must be the canonical HTTPS service origin')
    return value.rstrip('/')


class Store:
    """Service-owned OAuth records in the existing runtime table, never local memory."""
    def _key(self, kind, key):
        return 'mcp_oauth:' + kind + ':' + hashlib.sha256(key.encode()).hexdigest()

    def put(self, kind, key, value):
        from .database import connect
        from .initialization import save_resource
        record = dict(value, _generation=hashlib.sha256(owner_token().encode()).hexdigest())
        with connect() as connection:
            save_resource(connection, self._key(kind, key), record)

    def get(self, kind, key, consume=False):
        from .database import connect
        from .initialization import resource
        with connect() as connection:
            if consume:
                row = connection.execute('DELETE FROM installation_resources WHERE resource_key=%s RETURNING data',
                                         (self._key(kind, key),)).fetchone()
                record = row[0] if row else None
            else:
                record = resource(connection, self._key(kind, key))
        if not record or record.get('_generation') != hashlib.sha256(owner_token().encode()).hexdigest():
            return None
        if record.get('expires_at') and record['expires_at'] <= time.time():
            return None
        return {key: value for key, value in record.items() if key != '_generation'}


class Provider:
    def __init__(self, issuer, store=None):
        self.issuer = issuer
        self.resource = issuer + '/mcp'
        self.store = store or Store()

    async def get_client(self, client_id):
        saved = self.store.get('client', client_id)
        return OAuthClientInformationFull.model_validate(saved) if saved else None

    async def register_client(self, client_info):
        # SDK checks metadata/redirect URI registration and token endpoint auth.
        self.store.put('client', client_info.client_id, client_info.model_dump(mode='json'))

    async def authorize(self, client, params):
        if params.resource and params.resource != self.resource:
            raise AuthorizeError(error='invalid_target', error_description='Wrong MCP resource')
        nonce = secrets.token_urlsafe(32)
        self.store.put('pending', nonce, {
            'client_id': client.client_id, 'params': params.model_dump(mode='json'),
            'expires_at': int(time.time()) + 300,
        })
        return self.issuer + '/mcp-oauth/consent?' + urlencode({'request': nonce})

    async def consent(self, request: Request):
        nonce = request.query_params.get('request', '')
        headers = {'Cache-Control': 'no-store', 'Pragma': 'no-cache',
                   'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY',
                   'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'"}
        pending = self.store.get('pending', nonce)
        if not pending:
            return HTMLResponse('Authorization request expired. Connect again.', status_code=400, headers=headers)
        # Chrome checks form-action against redirects as well as the POST URL.
        # Allow only this OAuth request's already validated callback origin.
        callback = urlsplit(str(AnyHttpUrl(pending['params']['redirect_uri'])))
        host = callback.hostname
        if ':' in host:
            host = '[' + host + ']'
        callback_origin = callback.scheme + '://' + host
        if callback.port is not None:
            callback_origin += ':' + str(callback.port)
        headers['Content-Security-Policy'] = (
            "default-src 'none'; form-action 'self' " + callback_origin + "; frame-ancestors 'none'")
        if request.method == 'GET':
            client = await self.get_client(pending['client_id'])
            label = escape(client.client_name or 'MCP client') if client else 'MCP client'
            return HTMLResponse(
                '<!doctype html><title>Connect AJSS</title><h1>Connect ' + label + ' to AJSS</h1>'
                '<p>This grants access to read and update this installation’s job-search state.</p>'
                '<form method="post"><label>Application Service token '
                '<input type="password" name="token" autocomplete="off" required></label>'
                '<button type="submit">Authorize connection</button></form>', headers=headers)
        # The unguessable pending nonce binds consent to the validated OAuth request.
        origin = request.headers.get('origin')
        if origin and origin != self.issuer:
            return HTMLResponse('Origin not allowed.', status_code=403, headers=headers)
        form = await request.form()
        supplied = str(form.get('token', ''))
        if not hmac.compare_digest(supplied.encode(), owner_token().encode()):
            return HTMLResponse('Invalid service token.', status_code=403, headers=headers)
        pending = self.store.get('pending', nonce, consume=True)
        if not pending:
            return HTMLResponse('Authorization request already used.', status_code=400, headers=headers)
        params = AuthorizationParams.model_validate(pending['params'])
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32), client_id=pending['client_id'],
            scopes=params.scopes or [SCOPE], expires_at=time.time() + 120,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource)
        self.store.put('code', code.code, code.model_dump(mode='json'))
        query = {'code': code.code}
        if params.state is not None:
            query['state'] = params.state
        callback = str(params.redirect_uri)
        return RedirectResponse(callback + ('&' if '?' in callback else '?') + urlencode(query),
                                status_code=303, headers=headers)

    async def load_authorization_code(self, client, authorization_code):
        saved = self.store.get('code', authorization_code)
        return AuthorizationCode.model_validate(saved) if saved else None

    def _issue(self, client_id, scopes, family=None):
        family = family or secrets.token_urlsafe(32)
        access = AccessToken(token='ajss_at_' + secrets.token_urlsafe(32), client_id=client_id,
                             scopes=scopes, expires_at=int(time.time()) + 3600, resource=self.resource)
        refresh = RefreshToken(token='ajss_rt_' + secrets.token_urlsafe(32), client_id=client_id,
                               scopes=scopes, expires_at=int(time.time()) + 30 * 86400)
        self.store.put('access', access.token, dict(access.model_dump(mode='json'), family=family))
        self.store.put('refresh', refresh.token, dict(refresh.model_dump(mode='json'), family=family))
        self.store.put('family', family, {'active': access.token, 'expires_at': refresh.expires_at})
        return OAuthToken(access_token=access.token, refresh_token=refresh.token, expires_in=3600,
                          scope=' '.join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        saved = self.store.get('code', authorization_code.code, consume=True)
        if not saved or saved['client_id'] != client.client_id:
            raise TokenError(error='invalid_grant', error_description='Authorization code already used or expired')
        return self._issue(client.client_id, authorization_code.scopes)

    async def load_refresh_token(self, client, refresh_token):
        saved = self.store.get('refresh', refresh_token)
        return RefreshToken.model_validate(saved) if saved else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        saved = self.store.get('refresh', refresh_token.token, consume=True)
        if not saved or saved['client_id'] != client.client_id or not self.store.get('family', saved['family']):
            raise TokenError(error='invalid_grant', error_description='Refresh token already used or expired')
        return self._issue(client.client_id, scopes, saved['family'])

    async def load_access_token(self, token):
        saved = self.store.get('access', token)
        if not saved:
            return None
        family = self.store.get('family', saved['family'])
        if not family or family.get('active') != token or saved.get('resource') != self.resource:
            return None
        return AccessToken.model_validate(saved)

    async def revoke_token(self, token):
        kind = 'access' if isinstance(token, AccessToken) else 'refresh'
        saved = self.store.get(kind, token.token, consume=True)
        if saved:
            self.store.get('family', saved['family'], consume=True)

    def app(self):
        routes = create_auth_routes(
            self, issuer_url=AnyHttpUrl(self.issuer),
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True))
        # SDK 1.26 requires a client_secret field even for registered public
        # clients on revocation. Normalize it before the SDK authenticator runs.
        routes = [Route('/revoke', PublicClientRevocation(route.endpoint), methods=['POST', 'OPTIONS'])
                  if route.path == '/revoke' else route for route in routes]
        routes += create_protected_resource_routes(
            resource_url=AnyHttpUrl(self.resource), authorization_servers=[AnyHttpUrl(self.issuer)],
            scopes_supported=[SCOPE], resource_name='AJSS Application Service')
        routes.append(Route('/mcp-oauth/consent', self.consent, methods=['GET', 'POST']))
        return Starlette(routes=routes)


class PublicClientRevocation:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    async def __call__(self, scope, receive, send):
        if scope['method'] != 'POST':
            return await self.endpoint(scope, receive, send)
        request = Request(scope, receive)
        form = dict(await request.form())
        form.setdefault('client_secret', '')
        raw = urlencode(form).encode()
        async def normalized_receive():
            return {'type': 'http.request', 'body': raw, 'more_body': False}
        await self.endpoint(scope, normalized_receive, send)
