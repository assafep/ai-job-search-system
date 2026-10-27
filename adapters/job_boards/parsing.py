"""Provider-independent structured job HTML parsing. No HTTP or routing policy."""

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def text(value):
    value = html.unescape(value)
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    value = re.sub(r'<[^>]+>', '\n', value)
    return re.sub(r'\n\s*\n+', '\n', html.unescape(value)).strip()


def plain_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def structured_jobs(source):
    for block in re.findall(r'<script[^>]*type\s*=\s*[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', source, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        entries = data if isinstance(data, list) else [data]
        while entries:
            entry = entries.pop()
            if not isinstance(entry, dict):
                continue
            types = entry.get('@type', [])
            if 'JobPosting' in ([types] if isinstance(types, str) else types or []):
                yield entry
            for key in ('@graph', 'itemListElement', 'item'):
                children = entry.get(key, [])
                entries.extend(children if isinstance(children, list) else [children])


class _DescriptionParser(HTMLParser):
    """Read only explicit job containers; never accept a long careers shell."""
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        selected = (attrs.get('itemprop') == 'description' or attrs.get('id') == 'job-description'
                    or 'job-description' in attrs.get('class', '').split()
                    or attrs.get('data-testid') == 'job-description')
        if self.depth or selected:
            if tag not in {'br', 'hr', 'img', 'input', 'meta', 'link', 'wbr', 'source', 'area', 'embed', 'param', 'col', 'base'}:
                self.depth += 1
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            self.parts.append('\n')
            if not self.depth:
                self.candidates.append(''.join(self.parts).strip())
                self.parts = []

    def handle_data(self, value):
        if self.depth:
            self.parts.append(value)


def container_description(source):
    parser = _DescriptionParser()
    parser.feed(source)
    return max(parser.candidates, key=len, default='')


def description(source, description_reader=None, posting_url=None):
    def matches(job):
        if not posting_url or not isinstance(job.get('url'), str):
            return True
        expected = urlsplit(posting_url)
        actual = urlsplit(urljoin(posting_url, job['url']))
        return (expected.netloc, expected.path.rstrip('/')) == (actual.netloc, actual.path.rstrip('/'))

    candidates = [text(job['description']) for job in structured_jobs(source)
                  if matches(job) and isinstance(job.get('description'), str)]
    candidates.append((description_reader or container_description)(source))
    return max(candidates, key=len, default='')


def scrape_details(url, transport):
    source = transport.read_text(url)
    return transport.details(url, url, description(source, transport.description_reader,
                                                   transport.resolved.get(url, url)))


def scrape_listings(adapter, context, transport, *, search=False):
    if search and context.page != 1:
        from .interface import UnsupportedOperation
        raise UnsupportedOperation('Generic HTML search supports only the first page')
    found = {}
    for url, company in adapter.discovery_pages(context):
        source = transport.read_text(url)
        jobs = list(structured_jobs(source))
        if not jobs:
            raise ValueError('Discovery page contains no structured JobPosting records')
        for job in jobs:
            target = job.get('url')
            if not target or not job.get('title'):
                continue
            target = urljoin(url, target)
            employer = job.get('hiringOrganization') or {}
            location = job.get('jobLocation') or []
            location = location if isinstance(location, list) else [location]
            addresses = [item.get('address') for item in location
                         if isinstance(item, dict) and isinstance(item.get('address'), dict)]
            country_names = {'ISRAEL': 'IL', 'UNITED STATES': 'US', 'UNITED KINGDOM': 'GB', 'UK': 'GB'}
            countries = set()
            for address in addresses:
                country = address.get('addressCountry') or ''
                if isinstance(country, dict):
                    country = country.get('name') or ''
                country = str(country).upper()
                country = country_names.get(country, country)
                if len(country) == 2:
                    countries.add(country)
            found[target] = {
                'company': employer.get('name') or company,
                'role': job['title'], 'posting_url': target,
                'description': text(job.get('description') or ''),
                'source': adapter.provider_name, 'source_id': str(job.get('identifier') or target),
                'location': '; '.join(', '.join(str(a[k]) for k in ('addressLocality', 'addressRegion', 'addressCountry') if a.get(k)) for a in addresses),
                'countries': sorted(countries),
                'posted_at': job.get('datePosted'),
            }
    if not found:
        raise ValueError('Discovery page has no usable posting URLs')
    jobs = list(found.values())
    if search:
        # A page may ignore query-string filters. Preserve the requested scope.
        if context.countries:
            wanted = {country.upper() for country in context.countries}
            jobs = [job for job in jobs if wanted.intersection(job['countries'])]
        if context.query:
            phrases = [phrase.lower().split() for phrase in context.query.split(' OR ')]
            jobs = [job for job in jobs if any(all(word in (
                job['role'] + ' ' + job['description']).lower() for word in phrase)
                for phrase in phrases)]
        if context.locations:
            jobs = [job for job in jobs if any(location.lower() in job['location'].lower()
                                              for location in context.locations)]
        jobs = jobs[context.offset:context.offset + context.limit]
    return jobs
