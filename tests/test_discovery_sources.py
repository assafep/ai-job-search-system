from datetime import datetime, timezone

from services.discovery_sources import select_live_postings, fetch_employer_boards
from services.workflow_runtime import WorkflowRuntime


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


def test_comeet_and_lever_runtimes_use_the_catalog_and_profile_filter(monkeypatch):
    class Board:
        def __init__(self, boards):
            self.boards = boards

        def list_jobs(self):
            assert self.boards
            return [posting(posted_at=None, countries=['IL']),
                    posting(location='New York', countries=['US'], posted_at=None)]

    monkeypatch.setattr('services.discovery_sources.source_boards', lambda source: {'example': 'Example'})
    monkeypatch.setattr('adapters.job_boards.comeet.ComeetJobBoard', Board)
    monkeypatch.setattr('adapters.job_boards.lever.LeverJobBoard', Board)
    for source in ('comeet', 'lever'):
        context = {'source_names': [source]}
        WorkflowRuntime._enrich_discovery_context(context, PROFILE)
        assert len(context['job_postings']) == 1
        assert context['job_postings'][0]['countries'] == ['IL']


def test_himalayas_runtime_keeps_only_recent_israel_skill_matches(monkeypatch):
    class Board:
        def search(self, query, country='IL'):
            assert query == 'backend'
            assert country == 'IL'
            return [posting(countries=['IL'], posting_url='https://himalayas.app/jobs/new'),
                    posting(location='Remote', countries=[], posting_url='https://himalayas.app/jobs/remote'),
                    posting(posted_at='2020-01-01T00:00:00Z', countries=['IL'],
                            posting_url='https://himalayas.app/jobs/old')]

    monkeypatch.setattr('adapters.job_boards.himalayas.HimalayasJobBoard', Board)
    context = {'source_names': ['himalayas'], 'search_terms': ['backend']}
    WorkflowRuntime._enrich_discovery_context(context, PROFILE)
    assert len(context['job_postings']) == 1
    assert context['job_postings'][0]['countries'] == ['IL']


def test_smartrecruiters_runtime_uses_the_catalog_and_profile_filter(monkeypatch):
    class Board:
        def __init__(self, boards):
            assert boards == {'example': 'Example'}

        def list_jobs(self):
            return [posting(countries=['IL']), posting(location='New York', countries=['US'])]

    monkeypatch.setattr('services.discovery_sources.source_boards', lambda source: {'example': 'Example'})
    monkeypatch.setattr('adapters.job_boards.smartrecruiters.SmartRecruitersJobBoard', Board)
    context = {'source_names': ['smartrecruiters']}
    WorkflowRuntime._enrich_discovery_context(context, PROFILE)
    assert len(context['job_postings']) == 1
    assert context['job_postings'][0]['countries'] == ['IL']


def test_greenhouse_runtime_uses_catalog_and_filters_before_discovery(monkeypatch):
    class Board:
        def __init__(self, boards):
            assert boards == {'example': 'Example'}

        def list_jobs(self):
            return [posting(posted_at=None), posting(location='New York', posted_at=None)]

    monkeypatch.delenv('GREENHOUSE_BOARDS', raising=False)
    monkeypatch.setattr('services.discovery_sources.source_boards', lambda source: {'example': 'Example'})
    monkeypatch.setattr('adapters.job_boards.greenhouse.GreenhouseJobBoard', Board)
    context = {'source_names': ['greenhouse']}
    WorkflowRuntime._enrich_discovery_context(context, PROFILE)
    assert len(context['job_postings']) == 1
    assert context['job_postings'][0]['countries'] == ['IL']


def test_one_broken_employer_does_not_discard_healthy_results():
    class Board:
        def __init__(self, boards):
            self.broken = 'broken' in boards

        def list_jobs(self):
            if self.broken:
                raise RuntimeError('HTTP 404')
            return [posting()]

    jobs, errors = fetch_employer_boards(Board, {'broken': 'Broken', 'healthy': 'Healthy'})
    assert len(jobs) == 1
    assert errors == ['broken: HTTP 404']


def test_all_failed_boards_raise_instead_of_reporting_zero_jobs():
    import pytest

    class Broken:
        def __init__(self, boards):
            pass

        def list_jobs(self):
            raise RuntimeError('HTTP 503')

    with pytest.raises(RuntimeError, match='HTTP 503'):
        fetch_employer_boards(Broken, {'broken': 'Broken'})
