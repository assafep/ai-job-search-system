"""Extract resume contact details without changing the source document."""

import re
import zipfile
from xml.etree import ElementTree

import phonenumbers


def resume_text(path):
    if path.lower().endswith('.pdf'):
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
    if path.lower().endswith('.docx'):
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read('word/document.xml'))
        return '\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def normalize_phone(value, countries=None):
    """Validate and store a callable number in international E.164 format."""
    if not isinstance(value, str) or not value.strip():
        return None
    regions = countries or [None]
    for region in regions:
        try:
            number = phonenumbers.parse(value.strip(), region)
            if phonenumbers.is_valid_number(number):
                return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    return None


def extract_contacts(text, countries=None):
    personal = {}
    email = re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text)
    if email:
        personal['email'] = email.group(0)
    linkedin = re.search(r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+', text, re.I)
    if linkedin:
        personal['linkedin_url'] = 'https://' + re.sub(r'^https?://', '', linkedin.group(0))
    first = next((line.strip() for line in text.splitlines() if line.strip()), '')
    if 2 <= len(first.split()) <= 5 and all(part.replace('-', '').replace("'", '').isalpha() for part in first.split()):
        personal['name'] = first
    phones = []
    for region in countries or [None]:
        for match in phonenumbers.PhoneNumberMatcher(text, region):
            if phonenumbers.is_valid_number(match.number):
                value = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
                if value not in phones:
                    phones.append(value)
    # Multiple numbers are ambiguous: let the user choose rather than select
    # an arbitrary contact or reference number as their offered default.
    if len(phones) == 1:
        personal['phone'] = phones[0]
    return personal
