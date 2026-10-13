"""Human-readable PDF is the public activity-report format; JSON is private state."""

from collections import Counter
from datetime import datetime
from html import escape
from io import BytesIO


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    styles = getSampleStyleSheet()
    output = BytesIO()
    story = [Paragraph('Job Search — Activity Report', styles['Title']), Spacer(1, 12)]
    def paragraph(text, style='BodyText'):
        story.append(Paragraph(escape(str(text)), styles[style]))
        story.append(Spacer(1, 7))
    def date_label(value):
        try:
            return datetime.fromisoformat(value).strftime('%d %b %Y, %H:%M:%S %Z')
        except (ValueError, TypeError):
            return str(value)
    paragraph('Started: ' + date_label(report.get('started_at', 'Unknown')))
    paragraph('Finished: ' + date_label(report.get('finished_at', 'In progress')))
    if report.get('workers'):
        paragraph('Posting workers: {}. Peak concurrent postings: {}.'.format(report['workers'], report.get('peak_active_postings', 'Unknown')))
        paragraph('Storage access serialized; peak simultaneous storage requests: {}.'.format(
            (report.get('storage_synchronization') or {}).get('peak_concurrent_requests', 'Unknown')))
    operations = (report.get('storage_synchronization') or {}).get('operations', {})
    if operations:
        paragraph('Storage request time: {:.1f} seconds. Combined worker wait: {:.1f} seconds.'.format(
            sum(value['request_seconds'] for value in operations.values()),
            sum(value['wait_seconds'] for value in operations.values())))
        paragraph('Worker waits overlap; their sum is not run elapsed time.')
        for route, value in sorted(operations.items(), key=lambda item: item[1]['request_seconds'], reverse=True)[:5]:
            paragraph('{}: {} requests, {:.1f} seconds requesting, {:.1f} seconds waiting, {} errors.'.format(
                route, value['requests'], value['request_seconds'], value['wait_seconds'], value['errors']))
    if 'checkpoint_uploads' in report:
        paragraph('Report checkpoint uploads: {}.'.format(report['checkpoint_uploads']))
    paragraph('Activity by workload', 'Heading2')
    results = report.get('workload_results') or []
    applications = {a['application_id']: a for a in
                    (report.get('initial_bookmarked_applications') or []) + (report.get('initial_ready_applications') or [])}
    groups = {}
    for item in results:
        groups.setdefault(item.get('workflow', 'unknown'), []).append(item)
    for name in ['discover-and-bookmark', 'prepare-bookmarked', 'submit-ready', 'handle-incoming-email', 'prepare-interview', 'reconcile']:
        items = groups.get(name, [])
        paragraph(name.replace('-', ' ').title(), 'Heading3')
        statuses = Counter('failed' if item.get('error') else item.get('status') or (item.get('result') or {}).get('status', 'processed') for item in items)
        paragraph('{} execution(s). {}'.format(len(items), ', '.join('{}: {}'.format(k, v) for k, v in sorted(statuses.items())) or 'No triggered workloads.'))
        polling = (report.get('external_event_polling') or {}).get(name)
        if polling:
            paragraph('Source polling: {}. Scanned: {}; queued: {}. {}'.format(polling.get('status'), polling.get('scanned', 0), polling.get('queued', 0), polling.get('reason', '')))
        for item in items:
            execution = item.get('execution') or {}
            app = (execution.get('trigger_context') or {}).get('application') or item.get('application') or applications.get(item.get('application_id'), {})
            label = ' / '.join(str(app.get(k, '')) for k in ('company', 'role')).strip(' /') or item.get('application_id') or item.get('workload_id', '')
            if item.get('error'):
                paragraph('{}: {}'.format(label, item['error']))
            elif item.get('activity'):
                paragraph('{}: {}'.format(label, item['activity']))
            for op in execution.get('persistence_operations') or []:
                if op['operation'] == 'generate_and_store_artifact':
                    paragraph('Package saved: ' + op.get('name', 'Application PDF'))
                if op['operation'] == 'create_application':
                    paragraph('Bookmarked match: {} / {}'.format(op['company'], op['role']))
                if op['operation'] == 'ensure_ready_to_apply':
                    paragraph('Promoted to Ready for Auto-Apply: ' + str(op.get('application_id', '')))
            submission = item.get('submission') or item.get('result') or {}
            if submission:
                paragraph('{}: {}'.format(label, submission.get('status', 'Unknown')))
                for blocker in submission.get('blockers') or []:
                    paragraph('Reason: ' + blocker)
    for migration in report.get('migrations') or []:
        paragraph('Migrated {} / {} to Ready for Auto-Apply.'.format(migration.get('company'), migration.get('role')))
    for item in report.get('migration_errors') or []:
        app = applications.get(item.get('application_id'), {})
        paragraph('Migration not applied to {}: {}'.format(app.get('company', item.get('application_id')), item.get('reason')))
    paragraph('Final application counts', 'Heading2')
    rows = [['Status', 'Applications']] + [[k, str(v)] for k, v in (report.get('final_application_counts') or {}).items()]
    table = Table(rows, colWidths=[340, 120])
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#192d50')),
                               ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                               ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#edf1f7')]),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    story.append(table)
    for limitation in report.get('remaining_requirements') or []:
        paragraph('Remaining requirement: ' + limitation)
    SimpleDocTemplate(output, pagesize=A4, leftMargin=55, rightMargin=55, topMargin=50, bottomMargin=50).build(story)
    return output.getvalue()


def publish(persistence, report):
    from persistence_api.storage_layout import workload_state_path
    path = workload_state_path('system-run', 'reports/Activity Report - {}.pdf'.format(report['run_id']))
    saved = persistence.store_file(path, render_pdf(report), 'application/pdf')
    return dict(saved, path=path)
