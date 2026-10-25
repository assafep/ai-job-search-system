"""Import completed interview journals into posting keys and events.

A posting is completed when it appears in a completed journal. The same
company and role is one application even when job boards use different URLs.
"""
import hashlib
import json
import re
from datetime import datetime

from .journal_format import DATE_PATTERNS, date_prefix, normalize_date, normalize_posting_url, parse_application_title

STATE_HEADINGS = {
    'welcome', 'rejected', 'invalidated', 'offer', 'applied', 'bookmarked',
    'prepared', 'manual apply required', 'interviewing',
}
URL_RE = re.compile(r'https?://[^\s<>)\]]+')
SKIP_URL_HOSTS = ('mail.google.com', 'drive.google.com', 'claude.ai', 'linkedin.com/in')


def company_role_key(company, role):
    def norm(value):
        value = (value or '').lower().replace('&', ' and ')
        return re.sub(r'[^a-z0-9]+', ' ', value).strip()
    company_norm, role_norm = norm(company), norm(role)
    if not company_norm or not role_norm:
        return None
    return company_norm + ' / ' + role_norm


def _clean_url(value):
    return value.rstrip('.,);')


def posting_urls(text):
    found = []
    text = (text or '').replace('\\', '')
    for match in URL_RE.findall(text):
        url = _clean_url(match)
        lowered = url.lower()
        if any(host in lowered for host in SKIP_URL_HOSTS):
            continue
        normalized = normalize_posting_url(url)
        if normalized:
            found.append((normalized, url))
    return found


def _heading_name(line):
    match = re.match(r'^#{1,6}\s+(.*)$', line.strip())
    if not match:
        return None
    return match.group(1).replace('\\', '').replace('*', '').strip()


def _is_application_heading(name):
    return name.casefold() not in STATE_HEADINGS


def _application_id(company, role, document_id, heading):
    material = company_role_key(company, role) or (document_id + '\n' + heading)
    return 'app_' + hashlib.sha1(material.encode()).hexdigest()[:20]


def _parse_date(display):
    normalized = normalize_date(display)
    try:
        return datetime.strptime(normalized[:10], '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def parse_completed_markdown(text, document_id):
    """Return application records parsed from one exported journal."""
    lines = [line.rstrip() for line in text.splitlines()]
    sections = []
    current = None
    for line in lines:
        name = _heading_name(line)
        if name is not None:
            if current:
                sections.append(current)
            current = {'heading': name, 'lines': []} if _is_application_heading(name) else None
            continue
        if current is not None and line.strip():
            current['lines'].append(line.strip())
    if current:
        sections.append(current)
    applications = []
    for section in sections:
        company, role = parse_application_title(section['heading'])
        events = []
        current_event = None
        for line in section['lines']:
            prefix = date_prefix(line, False)
            if prefix is None:
                for pattern in DATE_PATTERNS:
                    try:
                        datetime.strptime(line.split()[0], pattern)
                    except (ValueError, IndexError):
                        continue
                    prefix = (line.split()[0], ' '.join(line.split()[1:]))
                    break
            if prefix:
                if current_event:
                    events.append(current_event)
                display, remainder = prefix
                current_event = {'display_date': display, 'occurred_on': _parse_date(display), 'lines': []}
                if remainder:
                    current_event['lines'].append(remainder)
                continue
            if not role and not line.startswith(('*', '-')) and '://' not in line and len(line) < 80:
                company_line, role_line = parse_application_title(line)
                if role_line:
                    company, role = company or company_line, role_line
                elif company_line and company_line.casefold() != company.casefold():
                    role = company_line
                continue
            if current_event is None:
                current_event = {'display_date': '', 'occurred_on': None, 'lines': []}
            current_event['lines'].append(line.lstrip('*- ').strip())
        if current_event:
            events.append(current_event)
        body_text = '\n'.join(section['lines'])
        urls = []
        for normalized, raw in posting_urls(body_text):
            if normalized not in [item[0] for item in urls]:
                urls.append((normalized, raw))
        applications.append({
            'application_id': _application_id(company, role, document_id, section['heading']),
            'company': company or section['heading'],
            'role': role or '',
            'heading': section['heading'],
            'terminal_status': 'Rejected' if 'rejected' in section['heading'].casefold() else 'Rejected',
            'source_document_id': document_id,
            'urls': urls,
            'events': events,
        })
    return applications


def _attributes(body):
    attributes = []
    for label, name in (
        ('operation idempotency key:', 'idempotency_key'),
        ('rejection email:', 'email_url'),
        ('application package:', 'package_url'),
        ('posting url:', 'posting_url'),
    ):
        for line in body.splitlines():
            if line.lower().startswith(label):
                attributes.append((name, line.split(':', 1)[1].strip()))
    moved = re.search(r'moved from (.+?) to (.+)$', body, re.IGNORECASE | re.MULTILINE)
    if moved:
        attributes.append(('moved_from', moved.group(1).strip()))
        attributes.append(('moved_to', moved.group(2).strip()))
    return attributes


def load_completed(connection, applications):
    """Insert or refresh completed applications. Same company and role stay one row."""
    stored = 0
    for app in applications:
        completed_on = next((event['occurred_on'] for event in reversed(app['events']) if event['occurred_on']), None)
        with connection.transaction():
            identity = app['application_id']
            role_key = company_role_key(app['company'], app['role'])
            if role_key:
                existing = connection.execute(
                    'SELECT application_id FROM posting_keys WHERE key_type=%s AND lookup_key=%s',
                    ('company_role', role_key)).fetchone()
                if existing:
                    identity = existing[0]
            for normalized, _raw in app['urls']:
                existing = connection.execute(
                    'SELECT application_id FROM posting_keys WHERE key_type=%s AND lookup_key=%s',
                    ('url', normalized)).fetchone()
                if existing:
                    identity = existing[0]
                    break
            connection.execute('''INSERT INTO applications
                (application_id, company, role, posting_url, status, heading, terminal_status, completed_on, source_document_id, source_record_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (application_id) DO UPDATE SET
                  company=excluded.company, role=excluded.role, status=excluded.status,
                  heading=excluded.heading, terminal_status=excluded.terminal_status,
                  completed_on=excluded.completed_on, updated_at=CURRENT_TIMESTAMP''',
                (identity, app['company'], app['role'] or 'Unknown', 'urn:ajss:' + identity, app['terminal_status'],
                 app['heading'], app['terminal_status'], completed_on, app['source_document_id'], identity))
            role_key = company_role_key(app['company'], app['role'])
            if role_key:
                connection.execute('''INSERT INTO posting_keys (application_id, key_type, lookup_key, raw_value)
                    VALUES (%s,'company_role',%s,%s) ON CONFLICT DO NOTHING''',
                    (identity, role_key, app['company'] + ' / ' + app['role']))
            for normalized, raw in app['urls']:
                connection.execute('''INSERT INTO posting_keys (application_id, key_type, lookup_key, raw_value)
                    VALUES (%s,'url',%s,%s) ON CONFLICT (key_type, lookup_key) DO NOTHING''',
                    (identity, normalized, raw))
            connection.execute('DELETE FROM event_attributes WHERE event_id IN (SELECT event_id FROM events WHERE application_id=%s AND source_document_id=%s)',
                               (identity, app['source_document_id']))
            connection.execute('DELETE FROM events WHERE application_id=%s AND source_document_id=%s',
                               (identity, app['source_document_id']))
            for position, event in enumerate(app['events'], start=1):
                body = '\n'.join(event['lines']).strip()
                if not body:
                    continue
                event_id = 'evt_' + hashlib.sha1((identity + app['source_document_id'] + str(position) + body).encode()).hexdigest()[:20]
                connection.execute('''INSERT INTO events
                    (event_id, application_id, event_type, occurred_on, position, body, source_document_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                    (event_id, identity, 'journal', event['occurred_on'], position, body, app['source_document_id']))
                for name, value in _attributes(body):
                    connection.execute('INSERT INTO event_attributes (event_id, name, value) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                       (event_id, name, value))
        stored += 1
    return stored


def apply_migration(connection, sql_text):
    connection.execute(sql_text)


def completed_match(connection, raw):
    """Return completed rows for a URL or a 'company / role' string."""
    url = normalize_posting_url(raw)
    keys = [url] if url and '://' in url else []
    if ' / ' in raw:
        company, role = raw.split(' / ', 1)
        role_key = company_role_key(company, role)
        if role_key:
            keys.append(role_key)
    if not keys:
        return []
    rows = connection.execute(
        '''SELECT application_id, company, role, terminal_status, completed_on, lookup_key
           FROM completed_postings WHERE lookup_key = ANY(%s)''', (keys,)).fetchall()
    return [
        {'application_id': row[0], 'company': row[1], 'role': row[2],
         'terminal_status': row[3], 'completed_on': row[4], 'lookup_key': row[5]}
        for row in rows
    ]
