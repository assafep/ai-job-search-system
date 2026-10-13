import pytest


@pytest.fixture
def candidate_profile(tmp_path):
    """Actual original resume PDF for runtime package/lifecycle contracts."""
    from reportlab.pdfgen import canvas
    path = tmp_path / 'original-resume.pdf'
    pdf = canvas.Canvas(str(path))
    pdf.drawString(60, 700, 'Candidate original resume')
    pdf.save()
    return {'schema_version': 1, 'personal': {'name': 'Candidate'},
            'professional': {}, 'employment': [], 'source_resume': {'path': str(path)}}
