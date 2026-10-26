"""The deployed file-based entrypoint must expose the service's public paths."""
import json
from pathlib import Path
import re

from api.index import handler
from application_service.main import Handler


def test_service_and_mcp_paths_reach_the_python_handler():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'vercel.json').read_text())
    assert issubclass(handler, Handler)
    assert not config.get('crons')  # Routing deployment must not start consumers.
    for path in ('/health', '/ready', '/stages', '/stage-tasks', '/mcp',
                 '/.well-known/oauth-authorization-server',
                 '/.well-known/oauth-protected-resource', '/register', '/authorize', '/token'):
        routes = [rule for rule in config['rewrites'] if re.fullmatch(rule['source'], path)]
        assert routes, path
        target = root / routes[0]['destination'].lstrip('/')
        assert target.is_file() and target.relative_to(root).as_posix() == 'api/index.py'
