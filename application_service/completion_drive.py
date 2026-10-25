"""Drive document created for one completed application.

The file is tagged with the application id and reason. A repeated call finds
that file instead of creating another one. The first tab is Journal. Research
notes are stored as sibling tabs.
"""
from .completion import FIRST_TAB


class DriveCompletion:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def find_completion(self, application_id, reason):
        return self.artifacts.find_tagged(application_id, reason) if hasattr(self.artifacts, 'find_tagged') else None

    def create_completion(self, application_id, reason, title, journal_text, research, source=None):
        source = source or {}
        if source.get('company') and source.get('role') and hasattr(self.artifacts, 'application_folder'):
            parent_id = self.artifacts.application_folder(source['company'], source['role'])['id']
        elif hasattr(self.artifacts, 'applications_directory'):
            parent_id = self.artifacts.applications_directory()
        else:
            parent_id = None
        return self.artifacts.create_completed_document(
            application_id, reason, title, FIRST_TAB, journal_text, research, parent_id)

    def place_in_position_directory(self, document, source):
        if source.get('company') and source.get('role') and hasattr(self.artifacts, 'place_document'):
            self.artifacts.place_document(document['id'], source['company'], source['role'])


class RecordCompletion:
    def __init__(self, repository):
        self.repository = repository

    def completion_record(self, application_id):
        app = self.repository.get_application(application_id)
        status = getattr(app, 'status', None) or (app or {}).get('status')
        if status in {'Rejected', 'Invalidated', 'Filled', 'Withdrawn'}:
            return {'terminal_status': status}
        return None

    def mark_completed(self, application_id, reason, document):
        self.repository.complete_application(application_id, reason)
        marker = getattr(self.repository, 'note_completion_document', None)
        if marker:
            marker(application_id, document)
