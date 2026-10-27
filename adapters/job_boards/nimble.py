"""Nimble's branded page publishes an exact Comeet position API URL."""

import re
from urllib.parse import urlsplit, parse_qs

from .interface import JobBoardAdapter
from .parsing import description, text


class NimbleJobBoard(JobBoardAdapter):
    provider_name = 'nimble'
    hosts = ('www.nimbleway.com', 'nimbleway.com')

    def fetch_details_scrape(self, context, transport):
        source = transport.read_text(context.posting_url)
        content = description(source, transport.description_reader)
        if len(content) >= 500:
            return transport.details(context.posting_url, context.posting_url, content)
        match = re.search(r'const position_path = `([^`]+)`', source)
        uid = parse_qs(urlsplit(context.posting_url).query).get('uid', [''])[0]
        if not match or not re.fullmatch(r'[\w.-]+', uid):
            raise ValueError('Nimble page has no exact position API URL')
        url = match[1].replace('${positionId}', uid)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'www.comeet.co', 'comeet.co'} or '/careers-api/2.0/company/' not in parsed.path:
            raise ValueError('Nimble page published an unexpected API URL')
        payload = transport.read_json(url)
        if payload.get('uid') is not None and str(payload['uid']) != uid:
            raise ValueError('Comeet API returned a different posting')
        content = '\n\n'.join('{}\n{}'.format(item['name'], text(str(item['value'])))
                              for item in payload.get('details') or []
                              if isinstance(item, dict) and item.get('name') and item.get('value'))
        return transport.details(context.posting_url, url, content)
