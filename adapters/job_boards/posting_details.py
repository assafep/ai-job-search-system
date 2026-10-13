"""Read source posting details before a stateless application agent runs."""

import html
import json
import re
import urllib.request


def _text(value):
    value = html.unescape(value)
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    value = re.sub(r'<[^>]+>', '\n', value)
    return re.sub(r'\n\s*\n+', '\n', html.unescape(value)).strip()


def fetch_posting_details(posting_url):
    urls = [posting_url]
    linkedin = re.search(r'linkedin\.com/jobs/view/[^/?]*?(\d+)(?:[/?]|$)', posting_url)
    if linkedin:
        # The mirror uses the exact LinkedIn job ID, avoiding title-only matches.
        urls.append('https://jobify360.co.il/jobs/{}-in'.format(linkedin.group(1)))
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=25) as response:
                source = response.read(3000000).decode('utf-8', 'replace')
            descriptions = []
            for block in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', source, re.S):
                try:
                    data = json.loads(block)
                except ValueError:
                    continue
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if isinstance(entry, dict):
                        entries.extend(entry.get('@graph', []))
                        if entry.get('@type') == 'JobPosting' and entry.get('description'):
                            descriptions.append(_text(entry['description']))
            markup = re.search(r'<div[^>]*class=[\"\'][^\"\']*show-more-less-html__markup[^\"\']*[\"\'][^>]*>(.*?)</div>', source, re.S)
            if markup:
                descriptions.append(_text(markup.group(1)))
            if not descriptions and url != posting_url:
                body = _text(source)
                start = body.find('Description')
                end = body.find('שאלות ותשובות', max(0, start))
                if start >= 0:
                    descriptions.append(body[start:end if end > start else None])
            description = max(descriptions, key=len) if descriptions else ''
            if len(description) >= 500:
                return {'posting_url': posting_url, 'source_url': url, 'description': description}
        except (OSError, ValueError):
            continue
    raise ValueError('full job posting unavailable; package preparation must stop: ' + posting_url)
