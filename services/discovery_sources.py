"""Source configuration and conservative, profile-based discovery filtering."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re


CATALOG = Path(__file__).resolve().parents[1] / 'config' / 'discovery-sources.json'
SOURCE_NAMES = {
    'freehire', 'jobicy', 'adzuna', 'greenhouse', 'ashby', 'comeet', 'lever', 'himalayas',
    'smartrecruiters',
}
ISRAEL_LOCATION = re.compile(
    r'\b(?:israel|isr|tel[ -]?aviv(?:[ -]yafo)?|herzli[yz]a|haifa|jerusalem|'
    r'ra[\W_]?anana|netanya|ramat[ -]gan|pet[ah]+[ -]ti[kq]va|kfar[ -]saba|'
    r'beer[ -]sheva|be.er[ -]sheva|rehovot|yokneam|holon|bnei[ -]brak|'
    r'kinneret|gush[ -]dan)\b|'
    r'ישראל|תל[ -]?אביב|ירושלים|חיפה|הרצליה|רעננה|נתניה|רמת[ -]?גן|'
    r'פתח[ -]?תקווה|כפר[ -]?סבא|באר[ -]?שבע|רחובות|יוקנעם|יקנעם|חולון|'
    r'בני[ -]?ברק|ראשון[ -]?לציון|גבעתיים|מודיעין|רמת[ -]?השרון', re.I)
ENGINEERING_ROLE = re.compile(
    r'backend|back[ -]end|server|software|platform|distributed|data engineer|'
    r'big data|מפתח|פיתוח|מתכנת', re.I)
EXCLUDED_ROLE = re.compile(
    r'\b(?:intern|internship|junior|student|frontend|front[ -]end|mobile|'
    r'android|ios|qa|sdet|director|vp|devops|sre)\b', re.I)


def source_boards(source):
    with CATALOG.open() as handle:
        boards = json.load(handle).get(source, {})
    if not isinstance(boards, dict) or any(
            not isinstance(key, str) or not key.strip() or
            not isinstance(value, str) or not value.strip()
            for key, value in boards.items()):
        raise ValueError('discovery catalog must map board tokens to company names')
    return boards


def _date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def fetch_employer_boards(board_class, boards):
    """One failing employer must not discard another employer's live results."""
    postings, errors = [], []
    succeeded = 0
    for token, company in boards.items():
        try:
            postings.extend(board_class({token: company}).list_jobs())
            succeeded += 1
        except (RuntimeError, ValueError, OSError) as error:
            errors.append('{}: {}'.format(token, error))
    if errors and not succeeded:
        raise RuntimeError('; '.join(errors))
    return postings, errors


def select_live_postings(postings, profile=None, countries=None, max_age_days=30, now=None):
    """Filter live employer feeds, keeping unknown publication age explicit.

    Updated timestamps never prove recency. Unknown age is acceptable only here,
    where the record was just returned by a live employer's published-job feed.
    Skill matches are an inexpensive discovery prefilter; scoring owns suitability.
    """
    profile = profile or {}
    preferred = {str(country).upper() for country in (
        countries or (profile.get('job_search') or {}).get('countries') or ['IL'])}
    skills = [skill for category, values in (profile.get('skills') or {}).items()
              if category in {'languages_and_frameworks', 'data_and_streaming', 'architecture_and_practices'}
              and isinstance(values, list) for skill in values if isinstance(skill, str)]
    now = now or datetime.now(timezone.utc)
    selected = []
    for posting in postings:
        location = posting.get('location') or ''
        actual = {str(country).upper() for country in posting.get('countries') or []}
        if ISRAEL_LOCATION.search(location):
            actual.add('IL')
        if not actual.intersection(preferred):
            continue
        role = posting.get('role') or ''
        if not ENGINEERING_ROLE.search(role) or EXCLUDED_ROLE.search(role):
            continue
        text = role + ' ' + (posting.get('description') or '')
        matches = [skill for skill in skills if re.search(
            r'(?<!\w)' + re.escape(skill) + r'(?!\w)', text, re.I)]
        if skills and not matches:
            continue
        published = _date(posting.get('posted_at'))
        if published and (published < now - timedelta(days=max_age_days) or published > now + timedelta(days=1)):
            continue
        selected.append(dict(posting, countries=sorted(actual), matched_skills=matches,
                             freshness='recent' if published else 'live_age_unknown'))
    return sorted(selected, key=lambda item: (
        _date(item.get('posted_at')) or datetime.min.replace(tzinfo=timezone.utc),
        len(item['matched_skills'])), reverse=True)
