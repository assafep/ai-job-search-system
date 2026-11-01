"""Minimal Google Docs tab client and journal readers; no Application Service."""
import json,os,re,urllib.error,urllib.parse,urllib.request
from time import monotonic
from .auth import access_token_value
URL_RE=re.compile(r"https?://[^\s]+", re.IGNORECASE)

class GoogleDocsClient:
    def __init__(self, access_token, document_id):
        self.access_token = access_token
        self.document_id = document_id
        self.timeout = float(os.environ.get('GOOGLE_API_TIMEOUT_SECONDS', '30'))
        self.cache_seconds = float(os.environ.get('JOURNAL_READ_CACHE_SECONDS', '5'))
        if self.timeout <= 0 or self.cache_seconds < 0:
            raise ValueError('Google API timeout must be positive and journal cache duration non-negative')
        self._document = None
        self._document_expires = 0

    def invalidate_document(self):
        self._document = None
        self._document_expires = 0

    def request(self, method, url, payload=None):
        if method != 'GET':
            self.invalidate_document()
        request = urllib.request.Request(url, method=method)
        request.add_header("Authorization", "Bearer " + access_token_value(self.access_token))
        request.add_header("Content-Type", "application/json")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(request, data=data, timeout=self.timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}

    def get_document(self):
        if self._document is not None and monotonic() < self._document_expires:
            return self._document
        query = urllib.parse.urlencode({"includeTabsContent": "true"})
        url = "https://docs.googleapis.com/v1/documents/{}?{}".format(self.document_id, query)
        document = self.request("GET", url)
        self._document = document
        self._document_expires = monotonic() + self.cache_seconds
        return document

    def batch_update(self, requests, revision_id=None):
        # Invalidate even if a response is lost after Google accepts a write.
        self.invalidate_document()
        url = "https://docs.googleapis.com/v1/documents/{}:batchUpdate".format(self.document_id)
        body = {"requests": requests}
        if revision_id:
            body["writeControl"] = {"requiredRevisionId": revision_id}
        try:
            return self.request("POST", url, body)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            # Text positions were computed from this revision. Replaying them
            # against a newer revision could modify somebody else's notes.
            # The caller can rebuild a fresh, idempotent operation on retry.
            raise RuntimeError("Google Docs API HTTP {}: {}".format(error.code, detail))



def body_content(tab):
    document_tab = tab.get("documentTab") or {}
    body = document_tab.get("body") or {}
    return body.get("content") or (tab.get("body") or {}).get("content") or []


def utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def hyperlink_requests(text, start_index, application_id):
    requests = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,)")
        if not url:
            continue
        url_end = match.start() + len(url)
        requests.append({"updateTextStyle": {
            "range": {
                "startIndex": start_index + utf16_length(text[:match.start()]),
                "endIndex": start_index + utf16_length(text[:url_end]),
                "tabId": application_id,
            },
            "textStyle": {"link": {"url": url}},
            "fields": "link",
        }})
    return requests


def tab_end_index(tab):
    content = body_content(tab)
    return max([item.get("endIndex", 1) for item in content] or [1])


def tab_paragraphs(tab):
    paragraphs = []
    for structural in body_content(tab):
        paragraph = structural.get("paragraph")
        if not paragraph:
            continue
        chunks = []
        links = []
        for element in paragraph.get("elements") or []:
            text_run = element.get("textRun") or {}
            chunks.append(text_run.get("content", ""))
            style = text_run.get("textStyle") or {}
            link_url = (style.get("link") or {}).get("url")
            if link_url:
                links.append(link_url)
        is_list_item = bool(paragraph.get("bullet"))
        text = "".join(chunks).rstrip("\n")
        if is_list_item:
            # Some Drive connector responses include the rendered '-' glyph
            # in textRun content, while the native Docs API does not.
            text = re.sub(r"^\s*[-•●○■▪◦]\s+", "", text)
        para = {
            "text": text,
            "isListItem": is_list_item,
            "startIndex": structural.get("startIndex", 0),
            "endIndex": structural.get("endIndex", 0),
        }
        if links:
            para["links"] = links
        paragraphs.append(para)
    return paragraphs


def first_posting_url(events, paragraphs=None):
    for event in events:
        for bullet in event.get("bullets", []):
            match = URL_RE.search(bullet)
            if match:
                url = match.group(0).rstrip(".,)")
                # Skip Drive artifact links; the posting URL is the job-board URL
                if "drive.google.com/file/d/" in url:
                    continue
                return url
    for paragraph in paragraphs or []:
        # Prefer hyperlink URL if the paragraph text is wrapped in a link
        links = paragraph.get("links", [])
        for link in links:
            if "drive.google.com/file/d/" not in link:
                return link
        match = URL_RE.search(paragraph.get("text", ""))
        if match:
            url = match.group(0).rstrip(".,)")
            if "drive.google.com/file/d/" not in url:
                return url
    return ""


