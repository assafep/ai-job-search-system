from services.document_generator import ensure_scheduling_link, generate_application_pdf


def test_styled_package_preserves_original_resume_pages_and_links():
    from io import BytesIO
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader
    source = BytesIO()
    resume = canvas.Canvas(source, pagesize=(595, 842))
    resume.setFont('Helvetica-Bold', 22)
    resume.drawString(70, 750, 'Original styled resume')
    resume.linkURL('https://example.test', (70, 700, 200, 720))
    resume.save()
    data = source.getvalue()
    package = generate_application_pdf(
        'Dear Example Hiring Team,\n\nI build reliable systems.\n\nSincerely,\nCandidate',
        resume_pdf=data, applicant_profile={'personal': {'name': 'Candidate'},
                                            'professional': {'headline': 'Backend engineer'}},
        calendly_url='https://calendly.com/example')
    original = PdfReader(BytesIO(data))
    result = PdfReader(BytesIO(package))
    assert len(result.pages) == 2
    assert 'Dear Example Hiring Team' in result.pages[0].extract_text()
    assert result.pages[1].get_contents().get_data() == original.pages[0].get_contents().get_data()
    assert result.pages[1].mediabox == original.pages[0].mediabox
    assert result.pages[1]['/Annots'][0].get_object()['/A']['/URI'] == 'https://example.test'


def test_application_pdf_contains_cover_letter_and_cv_text():
    pdf = generate_application_pdf(
        "Dear Example team,\nI build reliable systems.",
        "<h1>Candidate</h1><p>Senior backend engineer</p>",
    )

    assert pdf.startswith(b"%PDF-1.4")
    assert b"Dear Example team" in pdf
    assert b"Senior backend engineer" in pdf
    assert pdf.endswith(b"%%EOF\n")


def test_application_pdf_includes_scheduling_link_once():
    pdf = generate_application_pdf(
        "Dear team,\nPlease use https://calendly.com/example/intro.",
        "<p>Backend engineer</p>",
        "https://calendly.com/example/intro",
    )

    assert pdf.count(b"https://calendly.com/example/intro") == 1
    assert ensure_scheduling_link("Dear team", "https://calendly.com/example/intro").endswith(
        "https://calendly.com/example/intro"
    )
    assert ensure_scheduling_link(
        "Dear team\nhttps://calendly.com/example/intro",
        " https://calendly.com/example/intro ",
    ).count("https://calendly.com/example/intro") == 1
