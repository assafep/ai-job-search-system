"""Readable, private report of one scripted provisioning invocation."""
from html import escape
from io import BytesIO
import os
from pathlib import Path
import tempfile


def write_report(results, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    styles = getSampleStyleSheet()
    story = [Paragraph('AJSS Provisioning and Deployment', styles['Title']), Spacer(1, 12)]
    story.append(Paragraph('Results of this script invocation. Deployment does not activate job processing.', styles['BodyText']))
    for name, result in results:
        story.extend([Spacer(1, 12), Paragraph(escape(name + ': ' + result.outcome.value), styles['Heading2'])])
        for line in result.evidence:
            story.append(Paragraph(escape(str(line)).replace('\n', '<br/>'), styles['BodyText']))
        if result.resume_instruction:
            story.extend([Spacer(1, 7), Paragraph(escape(result.resume_instruction), styles['BodyText'])])
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4).build(story)
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.provision-report-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(output.getvalue())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
