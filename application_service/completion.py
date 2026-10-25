"""Move one active journal tab into its own completed document.

The steps are safe to repeat. A document already tagged for this application
and reason is reused. The journal tab is removed only after that document
exists and the application record has been marked complete.
"""
import re

REASONS = {
    'rejected': 'Rejected',
    'invalidated': 'Invalidated',
    'filled': 'Filled',
    'withdrawn': 'Withdrawn',
    'ghosted': 'Ghosted',
    'expired': 'Expired',
}
FIRST_TAB = 'Journal'


def normalize_reason(value):
    reason = REASONS.get(str(value or '').strip().lower())
    if reason is None:
        raise ValueError('completion reason must be rejected, invalidated, filled, withdrawn, ghosted, or expired')
    return reason


def document_title(tab_name, reason):
    return 'Completed ({}) Journal - {}'.format(reason, tab_name.strip())


def completion_reason_from_text(text):
    """Return a completion reason when a message is clearly final, otherwise None."""
    value = text or ''
    if re.search(r'\b(withdrawn|withdrawal)\b', value, re.I):
        return 'Withdrawn'
    if re.search(r'\b(position has been filled|role has been filled|position filled|no longer accepting)\b', value, re.I):
        return 'Filled'
    if re.search(r'\b(unfortunately|not selected|not moving forward|other candidates|regret to inform)\b', value, re.I):
        return 'Rejected'
    if re.search(r'\b(does not meet|not a match for the requirements|invalid application)\b', value, re.I):
        return 'Invalidated'
    return None


class CompletionService:
    def __init__(self, journal, drive, records):
        self.journal = journal
        self.drive = drive
        self.records = records

    def complete(self, application_id, reason):
        reason = normalize_reason(reason)
        source = self.journal.export_for_completion(application_id)
        existing = self.drive.find_completion(application_id, reason)
        if existing is None:
            if source is None:
                record = self.records.completion_record(application_id)
                if record and record.get('terminal_status') == reason:
                    return {'application_id': application_id, 'reason': reason, 'recovered': True, 'document': None}
                raise KeyError(application_id)
            title = document_title(source['tab_name'], reason)
            existing = self.drive.create_completion(
                application_id, reason, title, source['journal_text'], source.get('research') or [], source)
        self.drive.place_in_position_directory(existing, source or {'company': '', 'role': ''})
        self.records.mark_completed(application_id, reason, existing)
        if source is not None:
            self.journal.remove_tab(application_id)
        return {
            'application_id': application_id,
            'reason': reason,
            'document_id': existing.get('id'),
            'document_title': existing.get('name'),
            'recovered': source is None,
        }
