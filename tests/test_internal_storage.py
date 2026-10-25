from application_service.google_drive_artifacts import GoogleDriveArtifactStore, safe_folder_name, FOLDER_MIME
from services.ready_submission import ReadySubmission


def test_system_directory_keeps_leading_dot():
    assert safe_folder_name('.ai-job-search-system') == '.ai-job-search-system'
    assert ReadySubmission.receipt_path('app-1') == '.ai-job-search-system/workloads-internal-state/submit-prepared/submission-receipts/app-1.json'


def test_profile_migration_preserves_file_id_and_is_repeatable():
    class Store(GoogleDriveArtifactStore):
        def __init__(self):
            super().__init__('token', 'root')
            self.parent = 'root'
            self.moves = []
        def _ensure_folder(self, parent, name):
            assert name == '.ai-job-search-system'
            return {'id': 'system'}
        def _find_child(self, parent, name, *args):
            return {'id': 'profile'} if parent == self.parent and name == 'applicant_profile.json' else None
        def _move(self, id, old, new):
            self.moves.append((id, old, new))
            self.parent = new
    store = Store()
    assert store.migrate_profile() == 'profile'
    assert store.migrate_profile() == 'profile'
    assert store.moves == [('profile', 'root', 'system')]


def test_generic_state_writes_update_existing_file_instead_of_duplicating():
    class Store(GoogleDriveArtifactStore):
        def _ensure_folder(self, parent, name): return {'id': 'state'}
        def _find_child(self, parent, name=None, *args, **kwargs): return {'id': 'receipt'}
        def _replace_content(self, id, content, mime): return {'id': id, 'content': content}
        def _save_in_folder(self, *args): raise AssertionError('must not duplicate receipt')
    result = Store('token', 'root').save_path(ReadySubmission.receipt_path('app-1'), b'updated', 'application/json')
    assert result == {'id': 'receipt', 'content': b'updated'}
