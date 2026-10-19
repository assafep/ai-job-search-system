import json

import pytest

from services.site_trial_state import checkpoint_path, record_checkpoint


def test_checkpoint_path_is_private_and_site_scoped():
    assert checkpoint_path('jobify', 'app-1') == '.ai-job-search-system/workloads-internal-state/submit-prepared/site-trials/jobify/app-1.json'
    for site, app in [('unknown', 'a'), ('rvc', '../a'), ('rvc', '')]:
        with pytest.raises(ValueError):
            checkpoint_path(site, app)


def test_registration_checkpoint_only_writes_internal_state():
    class Persistence:
        def get_application(self, app):
            return {'status': 'Prepared'}
        def store_file(self, path, content, mime):
            self.saved = json.loads(content)
    persistence = Persistence()
    record_checkpoint(persistence, 'rvc', 'app-1', 'profile_review', 'Native language is required')
    assert persistence.saved['submission_confirmed'] is False
    assert persistence.saved['status'] == 'Prepared but not submitted'


def test_checkpoint_cannot_claim_submission():
    with pytest.raises(ValueError):
        record_checkpoint(None, 'jobify', 'app-1', 'Submitted successfully', 'account created')


def test_recipe_actions_are_verified_and_never_final_submit():
    from pathlib import Path
    recipes = json.loads((Path(__file__).resolve().parents[1] / 'adapters/browser/site_recipes.json').read_text())
    assert recipes['jobify']['actions']['open_application_handoff']['index'] == 1
    assert recipes['echojobs']['actions'] == {}
    assert recipes['rvc']['actions']['confirm_profile']['creates_account'] is True
