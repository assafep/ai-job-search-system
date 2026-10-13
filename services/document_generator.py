"""Deterministic generation of the combined application PDF.

The generator returns bytes and does not write to Drive. A workload must send
those bytes to the persistence API's artifact endpoint.
"""

import html
import re
import textwrap
from io import BytesIO
from datetime import date


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</(p|div|h[1-6]|li|section)>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _lines(cover_letter, cv_html, width=92):
    combined = "{}\n\n--- CV ---\n\n{}".format(
        (cover_letter or "").strip(), _plain_text(cv_html))
    result = []
    for paragraph in combined.splitlines():
        if not paragraph.strip():
            result.append("")
        else:
            result.extend(textwrap.wrap(paragraph, width=width) or [""])
    return result


def _pdf_bytes(lines, lines_per_page=48):
    pages = [lines[index:index + lines_per_page] for index in range(0, len(lines), lines_per_page)] or [[]]
    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Each page is emitted after its content stream, so page objects are
    # 5, 7, ...; 4, 6, ... are streams and cannot be listed as page kids.
    page_ids = [5 + index * 2 for index in range(len(pages))]
    objects.append(("<< /Type /Pages /Kids [{}] /Count {} >>".format(
        " ".join("{} 0 R".format(page_id) for page_id in page_ids), len(pages))).encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, page_lines in enumerate(pages):
        content_id = 4 + index * 2
        commands = ["BT", "/F1 10 Tf", "72 760 Td"]
        for line in page_lines:
            safe = line.encode("latin-1", "replace").decode("latin-1")
            safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append("({}) Tj".format(safe))
            commands.append("0 -14 Td")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        objects.append(("<< /Length {} >>\nstream\n".format(len(stream))).encode("ascii") + stream + b"\nendstream")
        objects.append(("<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> /MediaBox [0 0 612 792] /Contents {} 0 R >>".format(content_id)).encode("ascii"))

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend("{} 0 obj\n".format(object_id).encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend("xref\n0 {}\n".format(len(objects) + 1).encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend("{:010d} 00000 n \n".format(offset).encode("ascii"))
    output.extend("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n".format(
        len(objects) + 1, xref).encode("ascii"))
    return bytes(output)


def ensure_scheduling_link(cover_letter, calendly_url=None):
    """Append the configured scheduling link once when one is available."""
    letter = (cover_letter or "").strip()
    url = (calendly_url or "").strip()
    if not url or url in letter:
        return letter
    suffix = "\n\nSchedule an introductory call: {}".format(url)
    return letter + suffix


def generate_application_pdf(cover_letter, cv_html=None, calendly_url=None,
                             resume_pdf=None, applicant_profile=None, application=None):
    """Return a PDF containing the linked cover letter followed by the CV text."""
    if resume_pdf is not None:
        return _styled_package(cover_letter, resume_pdf, applicant_profile or {}, calendly_url, application)
    if application and application.get('company') and application.get('role'):
        cover_letter = 'Application: {} / {}\n\n{}'.format(application['company'], application['role'], cover_letter)
    return _pdf_bytes(_lines(ensure_scheduling_link(cover_letter, calendly_url), cv_html))


def _styled_package(letter, resume_pdf, profile, scheduling_url, application=None):
    """Render a fixed cover template and copy original resume pages verbatim."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from pypdf import PdfReader, PdfWriter

    personal = profile.get('personal') or {}
    professional = profile.get('professional') or {}
    name = personal.get('name') or personal.get('full_name')
    if not name:
        raise ValueError('applicant name is required for the cover letter')
    navy = colors.HexColor('#192d50')
    normal = ParagraphStyle('body', fontName='Helvetica', fontSize=10.5, leading=15,
                            spaceAfter=10)
    header = ParagraphStyle('name', parent=normal, fontName='Helvetica-Bold',
                            fontSize=20, leading=24, textColor=navy, spaceAfter=2)
    muted = ParagraphStyle('contact', parent=normal, textColor=colors.HexColor('#555555'),
                           fontSize=10, leading=14, spaceAfter=2)
    italic = ParagraphStyle('invitation', parent=normal, fontName='Helvetica-Oblique',
                            textColor=navy, fontSize=10, leading=14)
    esc = html.escape
    story = [Paragraph(esc(name), header)]
    if professional.get('headline'):
        story.append(Paragraph(esc(professional['headline']), muted))
    contacts = [personal.get(k) for k in ('location', 'email', 'linkedin_url')]
    story += [Paragraph(' &#8226; '.join(esc(v) for v in contacts if v), muted),
              Spacer(1, 10), HRFlowable(width='100%', thickness=1, color=navy),
              Spacer(1, 16), Paragraph(date.today().strftime('%B %d, %Y'), normal)]
    if application and application.get('company') and application.get('role'):
        story.append(Paragraph('<b>Application: {} / {}</b>'.format(
            esc(application['company']), esc(application['role'])), normal))
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', letter.strip()) if p.strip()]
    if not paragraphs:
        raise ValueError('cover letter must not be empty')
    for index, paragraph in enumerate(paragraphs):
        value = '<br/>'.join('<b>{}</b>'.format(esc(line)) if line.strip() == name
                            else esc(line) for line in paragraph.splitlines())
        if index == 0 and paragraph.startswith('Dear '):
            value = '<b>' + value + '</b>'
        if paragraph == name:
            value = '<b>' + value + '</b>'
        story.append(Paragraph(value, normal))
        if index == 0 and scheduling_url:
            url = esc(scheduling_url, quote=True)
            story.append(Paragraph('I’d welcome the chance to speak directly — feel free to find a time '
                                   'for an introductory call here:<br/><link href="{}">{}</link>'.format(url, url), italic))
    if scheduling_url:
        url = esc(scheduling_url, quote=True)
        story.append(Paragraph('Schedule an introductory call: <link href="{}">{}</link>'.format(url, url), italic))
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=(612, 792), leftMargin=67.2, rightMargin=67.2,
                      topMargin=60, bottomMargin=60).build(story)
    cover = PdfReader(BytesIO(output.getvalue()))
    if len(cover.pages) != 1:
        raise ValueError('cover letter exceeds one page; shorten the letter before storing')
    resume = PdfReader(BytesIO(resume_pdf))
    if not resume.pages:
        raise ValueError('original resume PDF has no pages')
    writer = PdfWriter()
    writer.append(cover)
    writer.append(resume)
    combined = BytesIO()
    writer.write(combined)
    return combined.getvalue()
