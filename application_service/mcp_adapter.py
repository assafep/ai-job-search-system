"""MCP transport adapter. Domain operations are owned by the Application Service.

Remote requests dispatch through the existing HTTP API handler, with its auth,
validation and persistence rules. The stdio client calls that API over HTTP.
Tools and their argument schemas come from openapi.yaml, never arbitrary URLs.
"""
import asyncio
import io
import hmac
import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
import yaml
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
INSTRUCTIONS = (
    'Use the Application Service for job-search state. Read current state before writing. '
    'Mutations must be explicit and retry-safe; reuse the same idempotency_key on retries. '
    'Never access Drive, Gmail or Calendar directly. For validate-bookmarked and submit-prepared, '
    'ChatGPT itself must use its native browser; never delegate their website actions to a worker or browser adapter. '
    'Use nextChatGPTStageTask, the supplied agent contracts and the stage result tools. '
    'Call beginChatGPTSubmission before the final submit click, then record observed confirmation. '
    'Pause for a specific CAPTCHA, login, upload or unavailable native-browser capability. '
    'Do not claim CAPTCHA bypass. Stage decision agents remain '
    'stateless; stage runtimes own execution. Prepared membership authorizes automatic '
    'submission by the runtime; do not add approval gates. Unconfirmed submissions need '
    'manual reconciliation. Migrations, repairs, rollback and deletion require an explicit '
    'operator request. Use startStageTask to admit the requested scope; listStages reports its execution host. '
    'poll getStageTask and retrieve getStageTaskReport. Omitted limits mean all eligible '
    'postings. Reuse the request key. Never assemble stages with state mutation tools.'
)


def _resolve(value, document):
    """Expand local OpenAPI refs and translate OAS 3.0 nullable to JSON Schema."""
    if isinstance(value, list):
        return [_resolve(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    if '$ref' in value:
        target = document
        ref = value['$ref']
        if not ref.startswith('#/'):
            raise ValueError('Only local OpenAPI references are supported')
        for part in ref[2:].split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        return _resolve(target, document)
    result = {key: _resolve(item, document) for key, item in value.items() if key != 'nullable'}
    if value.get('nullable'):
        return {'anyOf': [result, {'type': 'null'}]}
    return result


class Catalog:
    def __init__(self, contract=None, profile=None):
        profile = profile or os.environ.get('AJSS_MCP_TOOL_PROFILE', 'domain')
        if profile not in ('domain', 'stages'):
            raise ValueError('Unknown MCP tool profile')
        document = yaml.safe_load((contract or ROOT / 'openapi.yaml').read_text())
        self.operations = {}
        self.tools = []
        for path, methods in document['paths'].items():
            for method, operation in methods.items():
                if method not in ('get', 'post', 'put', 'patch', 'delete'):
                    continue
                if operation.get('x-mcp-expose') is False:
                    continue
                name = operation['operationId']
                if profile == 'stages' and name not in {
                    'listStages', 'listStageTasks', 'startStageTask', 'getStageTask', 'getStageTaskReport',
                    'journalQueueCounts', 'listApplications', 'getApplication',
                    'listApplicationEvents', 'getJournalStatus',
                    'getStageExecutionReadiness', 'nextChatGPTStageTask', 'completeChatGPTValidation', 'beginChatGPTSubmission',
                    'completeChatGPTSubmission', 'pauseChatGPTStageTask', 'getChatGPTSubmissionPackage',
                }:
                    continue
                properties, required, parameters = {}, [], []
                for raw in methods.get('parameters', []) + operation.get('parameters', []):
                    parameter = _resolve(raw, document)
                    if parameter['in'] not in ('path', 'query'):
                        raise ValueError('Unsupported API parameter location')
                    key = parameter['name']
                    properties[key] = parameter['schema']
                    if parameter.get('required'):
                        required.append(key)
                    parameters.append(parameter)
                body = operation.get('requestBody')
                if body:
                    properties['body'] = _resolve(body['content']['application/json']['schema'], document)
                    # This is a stricter adapter boundary: retryable mutations must be keyed.
                    if 'idempotency_key' in properties['body'].get('properties', {}):
                        properties['body']['properties']['idempotency_key'].update(type='string', minLength=1)
                        body_required = properties['body'].setdefault('required', [])
                        if 'idempotency_key' not in body_required:
                            body_required.append('idempotency_key')
                    if body.get('required'):
                        required.append('body')
                schema = {'type': 'object', 'properties': properties, 'required': required,
                          'additionalProperties': False}
                Draft202012Validator.check_schema(schema)
                keyed = bool(body and 'idempotency_key' in properties['body'].get('properties', {}))
                description = operation.get('description') or operation.get('summary') or name
                if not operation.get('x-mcp-read-only', method == 'get'):
                    description += ' Mutates application state; use only for an explicit requested operation.'
                self.tools.append(types.Tool(
                    name=name, description=description, inputSchema=schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=operation.get('x-mcp-read-only', method == 'get'),
                        destructiveHint=operation.get('x-mcp-destructive', method != 'get'),
                        idempotentHint=method in ('get', 'put') or keyed or operation.get('x-mcp-read-only', False),
                        openWorldHint=operation.get('x-mcp-open-world', False))))
                self.operations[name] = (method.upper(), path, parameters, schema)

    def request(self, name, arguments):
        if name not in self.operations:
            raise ValueError('Unknown Application Service tool')
        method, path, parameters, schema = self.operations[name]
        # Do not echo potentially sensitive caller-supplied values in validation failures.
        if not Draft202012Validator(schema).is_valid(arguments):
            raise ValueError('Arguments do not match the tool schema; include a non-empty idempotency_key for keyed writes')
        query = {}
        for parameter in parameters:
            key = parameter['name']
            if key not in arguments:
                continue
            value = arguments[key]
            if parameter['in'] == 'path':
                # The service splits path segments without unquoting. Reject separators
                # rather than let a caller select a different resource or operation.
                if not value or any(c in value for c in '/\\?#%') or value in ('.', '..'):
                    raise ValueError('Invalid resource identifier')
                path = path.replace('{' + key + '}', quote(value, safe=''))
            else:
                query[key] = value
        if query:
            path += '?' + urlencode(query)
        return method, path, arguments.get('body')


def dispatch_service(method, path, payload, token):
    """Invoke the API boundary in-process; no loopback HTTP or repository access."""
    from .main import Handler, output
    raw = json.dumps(payload).encode() if payload is not None else b''
    handler = object.__new__(Handler)
    handler.path = path
    handler.headers = {'Authorization': 'Bearer ' + token, 'Content-Length': str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, output(value)))
    handler.unauthorized = lambda: handler.send_json(401, {'error': 'authentication required'})
    getattr(handler, 'do_' + method)()
    if not responses:
        raise RuntimeError('Application Service produced no JSON response')
    return responses[-1]


def create_server(transport, catalog=None):
    catalog = catalog or Catalog()
    server = Server('ajss-application-service', version='0.1.0', instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools():
        return catalog.tools

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            method, path, payload = catalog.request(name, arguments)
        except ValueError as exc:
            return types.CallToolResult(content=[types.TextContent(type='text', text=str(exc))], isError=True)
        try:
            status, value = await transport(method, path, payload)
            if status >= 500:
                value = {'error': 'Application Service backend unavailable', 'status': status}
            return types.CallToolResult(
                content=[types.TextContent(type='text', text=json.dumps(value))],
                structuredContent={'status': status, 'data': value}, isError=status >= 400)
        except Exception:
            # Transport failures can include tokens, URLs, or backend credentials.
            return types.CallToolResult(content=[types.TextContent(type='text', text=(
                'Application Service request failed. A write may have succeeded; '
                'reconcile current state before retrying with the same idempotency_key.'))], isError=True)

    return server


async def http_response(raw, headers, method='POST'):
    """Run one stateless Streamable HTTP request with the official MCP SDK."""
    token_header = headers.get('Authorization', '')
    token = token_header.removeprefix('Bearer ')

    async def transport(method, path, payload):
        # These handlers are synchronous today. This request stays serialized in
        # the existing API server and uses the API's normal persistence locking.
        return dispatch_service(method, path, payload, token)

    server = create_server(transport)
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)
    async with manager.run():
        return await asgi_response(manager.handle_request, raw, headers, method, '/mcp')


async def asgi_response(app, raw, headers, method, path):
    parsed_path = urlsplit(path)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': method, 'scheme': 'https', 'path': parsed_path.path, 'raw_path': parsed_path.path.encode(),
             'query_string': parsed_path.query.encode(), 'root_path': '',
             'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
             'server': ('localhost', 8080), 'client': ('127.0.0.1', 0)}
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    content = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return start['status'], start.get('headers', []), content


def _send_response(handler, status, headers, content):
    handler.send_response(status)
    for key, value in headers:
        if key.lower() not in (b'content-length', b'transfer-encoding', b'connection'):
            handler.send_header(key.decode(), value.decode())
    handler.send_header('Content-Length', str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def _unauthorized(handler):
    from .mcp_oauth import issuer_url
    try:
        challenge = 'Bearer resource_metadata="' + issuer_url() + '/.well-known/oauth-protected-resource/mcp"'
    except ValueError:
        challenge = 'Bearer'
    return _send_response(handler, 401, [(b'www-authenticate', challenge.encode()),
                                       (b'content-type', b'application/json')],
                          b'{"error":"authentication required"}')


def handle_http(handler, oauth=False):
    from .mcp_oauth import Provider, SCOPE, issuer_url, owner_token
    try:
        expected = owner_token()
        if not oauth:
            authorization = handler.headers.get('Authorization', '')
            if not authorization.startswith('Bearer '):
                return _unauthorized(handler)
            supplied = authorization.removeprefix('Bearer ')
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                provider = Provider(issuer_url())
                access = asyncio.run(provider.load_access_token(supplied)) if supplied else None
                if not access or SCOPE not in access.scopes:
                    return _unauthorized(handler)
            origin = handler.headers.get('Origin')
            if origin and urlsplit(origin).netloc != handler.headers.get('Host'):
                return handler.send_json(403, {'error': 'origin not allowed'})
            if handler.command != 'POST':
                return _send_response(handler, 405, [(b'allow', b'POST')], b'')
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 0 or length > MAX_BYTES:
            return handler.send_json(413, {'error': 'MCP request too large'})
        raw = handler.rfile.read(length)
        if oauth:
            provider = Provider(issuer_url())
            result = asyncio.run(asgi_response(provider.app(), raw, handler.headers, handler.command, handler.path))
        else:
            # OAuth credentials grant MCP access only; the inner API retains its
            # existing bearer authentication and all domain validation.
            headers = dict(handler.headers.items())
            headers['Authorization'] = 'Bearer ' + expected
            result = asyncio.run(http_response(raw, headers))
        return _send_response(handler, *result)
    except (ValueError, TypeError):
        return handler.send_json(400, {'error': 'invalid MCP request or missing authentication configuration'})
    except Exception:
        return handler.send_json(503, {'error': 'MCP transport or authentication backend unavailable'})


def deployment_credentials():
    url = os.environ.get('APPLICATION_SERVICE_URL')
    token = os.environ.get('APPLICATION_SERVICE_TOKEN') or os.environ.get('AJSS_API_TOKEN')
    if not url and not token:
        checkpoint = ROOT / '.onboarding' / 'cloud.json'
        cloud = json.loads(checkpoint.read_text())
        if not cloud.get('application_ready'):
            raise ValueError('Application Service has not been deployed')
        runtime = cloud.get('runtime') or {}
        url, token = runtime.get('base_url'), runtime.get('bearer_token')
    parsed = urlsplit(url or '')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not token or any(c in token for c in '\r\n')):
        raise ValueError('Configure an HTTPS Application Service URL and bearer token')
    return url.rstrip('/'), token


async def stdio_main():
    url, token = deployment_credentials()
    async with httpx.AsyncClient(base_url=url, headers={'Authorization': 'Bearer ' + token},
                                 timeout=60, follow_redirects=False) as client:
        async def transport(method, path, payload):
            response = await client.request(method, path, json=payload)
            return response.status_code, response.json()
        server = create_server(transport)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == '__main__':
    import sys
    try:
        asyncio.run(stdio_main())
    except Exception:
        print('AJSS MCP startup failed. Check deployment credentials and service availability.', file=sys.stderr)
        sys.exit(1)
