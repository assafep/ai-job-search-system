from adapters.job_boards.interface import AdapterResult
from datetime import datetime, timezone

from services.discovery_sources import select_live_postings, fetch_employer_boards
from services.stage_executor import StageExecutor


NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
PROFILE = {'job_search': {'countries': ['IL']},
           'skills': {'languages_and_frameworks': ['Java', 'Scala', 'Python']}}


def posting(**changes):
    return dict({'role': 'Senior Backend Engineer', 'location': 'Tel Aviv, Israel',
                 'description': 'Java and Scala distributed services',
                 'posted_at': '2026-10-15T10:00:00Z'}, **changes)


def test_hebrew_and_kinneret_locations_count_as_israel():
    selected = select_live_postings([posting(location='תל אביב-יפו'), posting(location='Kinneret')],
                                    PROFILE, now=NOW)
    assert len(selected) == 2
    assert all(item['countries'] == ['IL'] for item in selected)


def test_recent_live_feed_matches_profile_and_israel_city_aliases():
    selected = select_live_postings([posting(location='Tel Aviv-Yafo Municipality, ISR'),
                                     posting(location='Herzliya'), posting(location='Haifa')],
                                    PROFILE, now=NOW)
    assert len(selected) == 3
    assert all(item['countries'] == ['IL'] for item in selected)
    assert selected[0]['matched_skills'] == ['Java', 'Scala']


def test_remote_does_not_imply_israel_eligibility():
    assert select_live_postings([posting(location='Remote, United States'),
                                 posting(location='Remote, EMEA'), posting(location='Remote')],
                                PROFILE, now=NOW) == []


def test_recent_edit_does_not_refresh_old_publication():
    old = posting(posted_at='2025-10-01T00:00:00Z', updated_at='2026-10-18T00:00:00Z')
    assert select_live_postings([old], PROFILE, now=NOW) == []


def test_live_feed_unknown_publication_is_labeled_not_claimed_recent():
    selected = select_live_postings([posting(posted_at=None)], PROFILE, now=NOW)
    assert selected[0]['freshness'] == 'live_age_unknown'


def test_irrelevant_titles_skills_and_future_dates_are_excluded():
    jobs = [posting(role='Junior Backend Engineer'), posting(role='Software Engineer, Mobile SDK'),
            posting(description='Rust embedded firmware'), posting(posted_at='2027-01-01'),
            posting(role='DevOps / Platform Engineer')]
    assert select_live_postings(jobs, PROFILE, now=NOW) == []


def test_employer_runtimes_use_the_catalog_and_profile_filter(monkeypatch):
    class Framework:
        def list_jobs(self, context, source):
            assert context.boards == {'example': 'Example'}
            return AdapterResult(True, [posting(posted_at=None, countries=['IL']),
                                       posting(location='New York', countries=['US'], posted_at=None)])

    monkeypatch.setattr('services.discovery_sources.source_boards', lambda source: {'example': 'Example'})
    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Framework)
    for source in ('comeet', 'lever', 'greenhouse', 'ashby', 'smartrecruiters'):
        context = {'source_names': [source]}
        StageExecutor._enrich_discovery_context(context, PROFILE)
        assert len(context['job_postings']) == 1
        assert context['job_postings'][0]['countries'] == ['IL']


def test_himalayas_runtime_keeps_only_recent_israel_skill_matches(monkeypatch):
    class Framework:
        def search(self, context, source):
            assert source == 'himalayas'
            assert context.query == 'backend'
            assert context.countries == ['IL']
            return AdapterResult(True, [posting(countries=['IL'], posting_url='https://himalayas.app/jobs/new'),
                    posting(location='Remote', countries=[], posting_url='https://himalayas.app/jobs/remote'),
                    posting(posted_at='2020-01-01T00:00:00Z', countries=['IL'],
                            posting_url='https://himalayas.app/jobs/old')])

    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Framework)
    context = {'source_names': ['himalayas'], 'search_terms': ['backend']}
    StageExecutor._enrich_discovery_context(context, PROFILE)
    assert len(context['job_postings']) == 1
    assert context['job_postings'][0]['countries'] == ['IL']


def test_one_broken_employer_does_not_discard_healthy_results():
    class Framework:
        def list_jobs(self, context, source):
            if 'broken' in context.boards:
                return AdapterResult(False, error='HTTP 404')
            return AdapterResult(True, [posting()])

    jobs, errors = fetch_employer_boards('example', {'broken': 'Broken', 'healthy': 'Healthy'}, framework=Framework())
    assert len(jobs) == 1
    assert errors == ['broken: HTTP 404']


def test_all_failed_boards_raise_instead_of_reporting_zero_jobs():
    import pytest

    class Framework:
        def list_jobs(self, context, source):
            return AdapterResult(False, error='HTTP 503')

    with pytest.raises(RuntimeError, match='HTTP 503'):
        fetch_employer_boards('example', {'broken': 'Broken'}, framework=Framework())
