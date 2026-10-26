from io import BytesIO

from pypdf import PdfReader

from services.activity_report import render_pdf


def test_report_is_readable_pdf_with_counts_and_failure_reasons():
    report = {'stage_task_id': 'test', 'started_at': 'start', 'finished_at': 'finish',
              'workload_results': [{'stage': 'submit-prepared', 'application_id': 'app',
                                    'result': {'status': 'Blocked', 'blockers': ['Human verification required']}}],
              'final_application_counts': {'Manual Apply Required': 1}}
    content = render_pdf(report)
    assert content.startswith(b'%PDF')
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(content)).pages)
    assert 'Activity Report' in text
    assert 'Human verification required' in text
    assert 'Manual Apply Required' in text
    assert '1 execution(s)' in text
