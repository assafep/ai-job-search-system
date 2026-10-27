"""Read-only employer-source audit with a readable PDF result."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from adapters.job_boards.framework import get_framework
from adapters.job_boards.interface import DiscoveryContext
from .discovery_sources import select_live_postings, source_boards


def audit(profile):
    """Inspect public feeds without a persistence client or registry writes."""
    results = []
    framework = get_framework()
    for source in ('greenhouse', 'ashby', 'comeet', 'lever', 'smartrecruiters'):
        for token, company in source_boards(source).items():
            item = {'source': source, 'token': token, 'company': company}
            try:
                jobs = framework.list_jobs(DiscoveryContext(boards={token: company}), source).unwrap()
                item.update(total=len(jobs), selected=select_live_postings(jobs, profile))
            except (RuntimeError, ValueError, OSError) as error:
                item['error'] = str(error)
            results.append(item)
    return results


def write_pdf(results, output):
    styles = getSampleStyleSheet()
    story = [Paragraph('Israel backend discovery source audit', styles['Title']),
             Paragraph(datetime.now(timezone.utc).strftime('Checked %Y-%m-%d %H:%M UTC'), styles['Normal']),
             Spacer(1, 12)]
    candidates = sum(len(item.get('selected', [])) for item in results)
    failures = sum('error' in item for item in results)
    story.append(Paragraph(
        '{} employer feeds checked; {} discovery candidates; {} feed failures. '
        'This audit reads public feeds only and does not create applications. '
        'Candidates pass geography, role, profile-skill and 30-day publication filters. '
        'Full suitability is evaluated later by the scoring stage.'.format(len(results), candidates, failures),
        styles['Normal']))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        'Greenhouse and Lever dates mean first publication. SmartRecruiters '
        'releasedDate is the publication time, and only Israel locations are read '
        'in full because the description is on the posting detail. Ashby dates mean last '
        'publication and may describe a republished role. Comeet time_updated is '
        'an edit time, not a publication date, and edits older than 60 days are '
        'left out. Update times are '
        'never treated as publication dates. Unknown publication age is explicitly '
        'labeled and retained only for records returned by a live employer feed. '
        'Himalayas is searched separately and only roles that name Israel are kept.',
        styles['Normal']))
    for item in results:
        story.append(Spacer(1, 14))
        story.append(Paragraph(escape('{} ({}/{})'.format(
            item['company'], item['source'], item['token'])), styles['Heading2']))
        if item.get('error'):
            story.append(Paragraph(escape('Feed error: ' + item['error']), styles['Normal']))
            continue
        story.append(Paragraph('{} live jobs; {} selected.'.format(
            item['total'], len(item['selected'])), styles['Normal']))
        for job in item['selected']:
            url = escape(job.get('posting_url') or '', {'"': '&quot;'})
            title = escape(job.get('role') or '')
            story.append(Paragraph('<link href="{}" color="{}">{}</link>'.format(
                url, colors.HexColor('#1955a5').hexval(), title), styles['Heading3']))
            story.append(Paragraph(escape('{} | Publication: {} | {}'.format(
                job.get('location') or '', job.get('posted_at') or 'unknown', job['freshness'])),
                styles['Normal']))
            story.append(Paragraph(escape('Profile skill evidence: ' + ', '.join(job['matched_skills'])),
                                   styles['Normal']))
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(destination), pagesize=A4, leftMargin=44, rightMargin=44).build(story)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='Local applicant profile JSON for read-only diagnostics')
    parser.add_argument('--output', required=True, help='Destination PDF path')
    args = parser.parse_args()
    with open(args.profile) as handle:
        profile = json.load(handle)
    results = audit(profile)
    write_pdf(results, args.output)
    print('PDF audit: {}'.format(args.output))


if __name__ == '__main__':
    main()
