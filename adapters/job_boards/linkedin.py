"""LinkedIn HTML and its existing exact-ID Jobify mirror fallback."""

import re
from urllib.parse import urlsplit

from .interface import JobBoardAdapter
from .parsing import description, text


class LinkedInJobBoard(JobBoardAdapter):
    provider_name = 'linkedin'
    hosts = ('linkedin.com', 'www.linkedin.com')
    host_suffixes = ('linkedin.com',)

    def fetch_details_scrape(self, context, transport):
        urls = [context.posting_url]
        match = re.search(r'/jobs/view/[^/?]*?(\d+)(?:/|$)', urlsplit(context.posting_url).path)
        if match:
            urls.append('https://jobify360.co.il/jobs/{}-in'.format(match[1]))
        failures = []
        for url in urls:
            try:
                source = transport.read_text(url)
                candidates = [description(source, transport.description_reader)]
                markup = re.search(r'<div[^>]*class=[\"\'][^\"\']*show-more-less-html__markup[^\"\']*[\"\'][^>]*>(.*?)</div>', source, re.S)
                if markup:
                    candidates.append(text(markup[1]))
                if urlsplit(url).hostname == 'jobify360.co.il':
                    body = text(source)
                    start = body.find('Description')
                    end = body.find('שאלות ותשובות', max(0, start))
                    if start >= 0:
                        candidates.append(body[start:end if end > start else None])
                content = max(candidates, key=len)
                if len(content) >= 500:
                    return transport.details(context.posting_url, url, content)
                failures.append('no full job description in response')
            except (OSError, ValueError) as exc:
                failures.append(str(exc))
        raise ValueError('; '.join(failures))
