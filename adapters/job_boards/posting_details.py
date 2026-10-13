"""Read source posting details before a stateless application agent runs."""

import html
import json
import os
import re
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request


def posting_content_bytes(details):
    """Serialize the normalized posting so later workflows need no network."""
    return json.dumps({
        'posting_url': details['posting_url'],
        'source_url': details.get('source_url') or details['posting_url'],
        'description': details['description'],
    }, ensure_ascii=False, indent=2).encode('utf-8')


def parse_saved_posting(content):
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    data = json.loads(content)
    if not isinstance(data, dict) or not data.get('posting_url') or len(str(data.get('description') or '')) < 500:
        raise ValueError('saved posting content is incomplete')
    return data


def posting_artifact_name(company, role):
    label = '{} - {}'.format(company, role)
    label = re.sub(r'[\\/:*?"<>|]+', '-', label).strip()
    return 'Job Posting - {}.json'.format(label[:180])


def _text(value):
    value = html.unescape(value)
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    value = re.sub(r'<[^>]+>', '\n', value)
    return re.sub(r'\n\s*\n+', '\n', html.unescape(value)).strip()


def fetch_posting_details(posting_url):
    # Public posting APIs identify the exact job without rendering the careers
    # site: https://docs.greenhouse.io/job-board.html and
    # https://developers.smartrecruiters.com/docs/endpoints .
    parsed = urllib.parse.urlsplit(posting_url)
    api_url = None
    if parsed.hostname in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        match = re.fullmatch(r'/([^/]+)/jobs/(\d+)/?', parsed.path)
        if match:
            api_url = 'https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}'.format(*match.groups())
    elif parsed.hostname == 'jobs.smartrecruiters.com':
        match = re.match(r'^/([^/]+)/(\d+)(?:-|/|$)', parsed.path)
        if match:
            api_url = 'https://api.smartrecruiters.com/v1/companies/{}/postings/{}'.format(*match.groups())
    urls = ([api_url] if api_url else []) + [posting_url]
    timeout = float(os.environ.get('POSTING_REQUEST_TIMEOUT_SECONDS', '12'))
    budget = float(os.environ.get('POSTING_FETCH_BUDGET_SECONDS', '25'))
    if timeout <= 0 or budget <= 0:
        raise ValueError('posting fetch timeouts must be positive')
    deadline = monotonic() + budget
    failures = []
    linkedin = re.search(r'linkedin\.com/jobs/view/[^/?]*?(\d+)(?:[/?]|$)', posting_url)
    if linkedin:
        # The mirror uses the exact LinkedIn job ID, avoiding title-only matches.
        urls.append('https://jobify360.co.il/jobs/{}-in'.format(linkedin.group(1)))
    for url in urls:
        remaining = deadline - monotonic()
        if remaining <= 0:
            failures.append('retrieval time budget exhausted')
            break
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=min(timeout, remaining)) as response:
                source = response.read(3000000).decode('utf-8', 'replace')
            descriptions = []
            if url == api_url:
                payload = json.loads(source)
                if urllib.parse.urlsplit(url).hostname == 'boards-api.greenhouse.io':
                    descriptions.append(_text(payload.get('content') or ''))
                else:
                    sections = (payload.get('jobAd') or {}).get('sections') or {}
                    descriptions.append(_text('\n'.join((sections.get(key) or {}).get('text') or ''
                        for key in ('jobDescription', 'qualifications', 'additionalInformation'))))
            for block in re.findall(r'<script[^>]*type\s*=\s*[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', source, re.S | re.I):
                try:
                    data = json.loads(block)
                except ValueError:
                    continue
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if isinstance(entry, dict):
                        graph = entry.get('@graph', [])
                        if isinstance(graph, list):
                            entries.extend(graph)
                        types = entry.get('@type', [])
                        types = [types] if isinstance(types, str) else types
                        if isinstance(types, list) and 'JobPosting' in types and isinstance(entry.get('description'), str):
                            descriptions.append(_text(entry['description']))
            markup = re.search(r'<div[^>]*class=[\"\'][^\"\']*show-more-less-html__markup[^\"\']*[\"\'][^>]*>(.*?)</div>', source, re.S)
            if markup:
                descriptions.append(_text(markup.group(1)))
            if not descriptions and urllib.parse.urlsplit(url).hostname == 'jobify360.co.il':
                body = _text(source)
                start = body.find('Description')
                end = body.find('שאלות ותשובות', max(0, start))
                if start >= 0:
                    descriptions.append(body[start:end if end > start else None])
            description = max(descriptions, key=len) if descriptions else ''
            if len(description) >= 500:
                return {'posting_url': posting_url, 'source_url': url, 'description': description}
            failures.append('no full job description in response')
        except urllib.error.HTTPError as exc:
            failures.append('HTTP ' + str(exc.code))
        except (OSError, ValueError) as exc:
            failures.append(type(exc).__name__)
    raise ValueError('full job posting unavailable; package preparation must stop: ' + posting_url +
                     ' (' + '; '.join(failures) + ')')
