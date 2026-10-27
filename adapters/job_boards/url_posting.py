"""Compatibility reader for an explicitly selected URL and role."""

from .framework import fetch_posting_details


def fetch_exact_posting(posting_url, role):
    result = fetch_posting_details(posting_url)
    if role.lower() not in result['description'].lower():
        raise ValueError('HTTP response lacks the selected role and full posting content')
    return result
