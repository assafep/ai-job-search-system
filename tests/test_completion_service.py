from application_service.completion import CompletionService, document_title
from services.completion_scan import scan_completions


class Journal:
    def __init__(self, source):
        self.source = source
        self.removed = False

    def export_for_completion(self, application_id):
        return None if self.removed else self.source

    def remove_tab(self, application_id):
        self.removed = True


class Drive:
    def __init__(self):
        self.created = None
        self.placed = 0

    def find_completion(self, application_id, reason):
        return self.created

    def create_completion(self, application_id, reason, title, journal_text, research, source=None):
        self.created = {'id': 'doc-1', 'name': title, 'journal': journal_text, 'research': research, 'first_tab': 'Journal'}
        return self.created

    def place_in_position_directory(self, document, source):
        self.placed += 1


class Records:
    def __init__(self):
        self.marked = None

    def completion_record(self, application_id):
        return self.marked

    def mark_completed(self, application_id, reason, document):
        self.marked = {'application_id': application_id, 'terminal_status': reason, 'document_id': document['id']}


def test_completion_is_idempotent_and_names_the_document():
    source = {'tab_name': 'Faye / Senior Backend Engineer', 'company': 'Faye', 'role': 'Senior Backend Engineer',
              'journal_text': '2026-10-13\nApplied', 'research': [{'title': 'Notes', 'content': 'prep'}]}
    journal, drive, records = Journal(source), Drive(), Records()
    service = CompletionService(journal, drive, records)
    first = service.complete('app-1', 'rejected')
    second = service.complete('app-1', 'rejected')
    assert first['document_title'] == document_title(source['tab_name'], 'Rejected')
    assert drive.created['first_tab'] == 'Journal'
    assert drive.created['research'][0]['title'] == 'Notes'
    assert journal.removed
    assert second['document_id'] == 'doc-1'
    assert records.marked['terminal_status'] == 'Rejected'


def test_mail_scan_completes_one_matching_application():
    calls = []
    applications = [{'application_id': 'app-1', 'company': 'Faye', 'role': 'Senior Backend Engineer', 'status': 'Applied'}]
    messages = [{'subject': 'Your application to Faye', 'body': 'Unfortunately we will not be moving forward'}]
    handled = scan_completions(messages, applications, lambda application_id, reason: calls.append((application_id, reason)) or {'ok': True})
    assert calls == [('app-1', 'Rejected')]
    assert handled == [{'ok': True}]


if __name__ == '__main__':
    test_completion_is_idempotent_and_names_the_document()
    test_mail_scan_completes_one_matching_application()
    print('ok')
