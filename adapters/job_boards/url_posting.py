"""Fetch an explicitly selected posting URL once, using an HTTP client."""

import shutil
import subprocess
from urllib.parse import urlsplit

from .posting_details import _text


def fetch_exact_posting(posting_url, role):
    if urlsplit(posting_url).scheme not in {'http', 'https'}:
        raise ValueError('posting URL must use HTTP or HTTPS')
    curl = shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for URL replay')
    response = subprocess.run([
        curl, '--silent', '--show-error', '--location', '--max-redirs', '5',
        '--proto', '=http,https', '--proto-redir', '=http,https', '--max-time', '20',
        '--user-agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        '--header', 'Accept: text/html,application/xhtml+xml',
        '--write-out', '\n%{http_code}\n%{url_effective}', posting_url,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=25)
    if response.returncode:
        raise RuntimeError('posting HTTP request failed: ' + response.stderr.strip())
    html, status, source_url = response.stdout.rsplit('\n', 2)
    if status != '200':
        raise ValueError('exact posting URL returned HTTP {}; no alternative source used'.format(status))
    plain = _text(html)
    start = plain.lower().find(role.lower())
    end = plain.find('Apply to this job', max(start, 0))
    description = plain[start:end] if start >= 0 and end > start else plain[start:]
    if start < 0 or len(description) < 500:
        raise ValueError('HTTP response lacks the selected role and full posting content')
    return {'posting_url': posting_url, 'source_url': source_url, 'description': description}
