"""Regenerate the static job-board architecture SVGs and four-page PDF.

Run with the repository virtual environment: .venv/bin/python docs/diagrams/job-boards/generate.py
Only reportlab is required. Coordinates and labels are shared by SVG and PDF.
"""

from html import escape
from math import atan2, cos, sin, pi
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

ROOT = Path(__file__).resolve().parent
W, H = 1500, 1050
PDF = canvas.Canvas(str(ROOT / 'job-board-architecture.pdf'), pagesize=(W, H))
PDF.setTitle('Job-board framework: calls, classes and sequences')
PDF.setAuthor('Job Search Automation')
svg = []


def text(x, y, value, size=19, color='#203047', anchor='start', bold=False):
    svg.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{"bold" if bold else "normal"}">{escape(value)}</text>')
    PDF.setFillColor(HexColor(color))
    PDF.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    method = {'start': PDF.drawString, 'middle': PDF.drawCentredString, 'end': PDF.drawRightString}[anchor]
    method(x, H-y, value)


def line(x1, y1, x2, y2, color='#536880', dashed=False, arrow=False):
    dash = ' stroke-dasharray="7 6"' if dashed else ''
    svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2"{dash}/>')
    PDF.setStrokeColor(HexColor(color))
    PDF.setLineWidth(2)
    PDF.setDash([7, 6] if dashed else [])
    PDF.line(x1, H-y1, x2, H-y2)
    PDF.setDash([])
    if arrow:
        a = atan2(y2-y1, x2-x1)
        for offset in (-pi/6, pi/6):
            line(x2, y2, x2-12*cos(a+offset), y2-12*sin(a+offset), color)


def box(x, y, w, h, title, body=(), fill='#edf4ff'):
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="#9bb3cc" stroke-width="1.5"/>')
    PDF.setFillColor(HexColor(fill))
    PDF.setStrokeColor(HexColor('#9bb3cc'))
    PDF.roundRect(x, H-y-h, w, h, 12, fill=1, stroke=1)
    title_size = min(21, 21 * (w-24) / max(1, stringWidth(title, 'Helvetica-Bold', 21)))
    text(x+w/2, y+32, title, title_size, anchor='middle', bold=True)
    for i, label in enumerate(body):
        label_size = min(18, 18 * (w-24) / max(1, stringWidth(label, 'Helvetica', 18)))
        text(x+w/2, y+62+i*25, label, label_size, anchor='middle')


def start(title, subtitle):
    global svg
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="white"/><g font-family="Arial, Helvetica, sans-serif">']
    text(55, 55, title, 32, bold=True)
    text(55, 90, subtitle, 19, color='#536880')


def finish(name, page):
    text(55, 1018, 'Job Search Automation | Current implementation | Read paths and native submission are shown separately', 16, '#536880')
    text(1440, 1018, str(page), 16, '#536880', anchor='end')
    (ROOT / (name + '.svg')).write_text('\n'.join(svg + ['</g></svg>']), encoding='utf-8')
    PDF.showPage()


start('1. Runner to board implementation', 'Discovery and bookmarking converge on AdapterFramework; persistence follows the completed read.')
box(490, 125, 520, 100, 'services/stage_runner.py', ['run_all() / run_one()'])
line(750, 225, 750, 260, arrow=True)
box(490, 260, 520, 100, 'StageExecutor', ['services/stage_executor.py'])
line(490, 310, 260, 310); line(260, 310, 260, 405, arrow=True)
line(1010, 310, 1240, 310); line(1240, 310, 1240, 405, arrow=True)
text(260, 390, 'Discovery: prepare_context()', 18, anchor='middle')
text(1240, 390, 'Bookmark: run_agent()', 18, anchor='middle')
box(55, 405, 410, 120, '_enrich_discovery_context()', ['Search sources: framework.search()', 'Employer feeds: fetch_employer_boards()'])
box(1035, 405, 410, 120, 'bookmark_read.py', ['read_discovered_posting()', 'framework.fetch_posting_details()'])
line(260, 525, 260, 575); line(260, 575, 550, 575, arrow=True)
line(1240, 525, 1240, 575); line(1240, 575, 950, 575, arrow=True)
box(550, 530, 400, 110, 'AdapterFramework', ['search() / list_jobs() / fetch_details()', '_read(): API first, scraping fallback'])
line(750, 640, 750, 680, arrow=True)
box(550, 680, 400, 100, 'Specific board adapter', ['Example: GreenhouseJobBoard'])
line(950, 730, 1025, 730, arrow=True)
box(1025, 680, 420, 100, 'ReadSession', ['transport.py -> urllib or Scrapling'])
box(55, 675, 410, 110, 'Configuration', ['configuration.py + job-boards.json', 'Defaults merged with provider overrides'], '#f0f7ed')
line(465, 700, 550, 610, dashed=True, arrow=True)
line(1235, 780, 1235, 820, arrow=True)
text(1235, 850, 'Job-board website / API', 21, anchor='middle', bold=True)
box(270, 890, 960, 90, 'After retrieval: services/workload_executor.py', ['plan_persistence_writes() -> execute_persistence_writes() -> Application Service'], '#fff5e8')
finish('01-call-flow', 1)

start('2. Classes and responsibilities', 'Solid arrows mean calls/uses. Dashed arrows mean inheritance toward the base class.')
box(60, 145, 520, 205, 'AdapterFramework | framework.py', ['search() / list_jobs() / fetch_details()', '_read(): routes, fallback and validation', 'submit(): one host callback'])
box(940, 145, 500, 205, 'JobBoardAdapter | interface.py', ['search_api() / search_scrape()', 'list_jobs_api() / list_jobs_scrape()', 'fetch_details_api() / fetch_details_scrape()'])
line(580, 245, 940, 245, arrow=True); text(760, 225, 'selects and calls', 18, anchor='middle')
box(60, 440, 520, 150, 'BoardConfiguration | configuration.py', ['for_provider(name)', 'Returns budget + API/scraping policies'], '#f0f7ed')
line(320, 350, 320, 440, arrow=True)
box(940, 430, 500, 145, 'EmployerJobBoard | interface.py', ['list_jobs_api() delegates to list_jobs()', 'Passes context.boards and transport'])
line(1190, 430, 1190, 350, dashed=True, arrow=True)
box(940, 660, 500, 175, 'GreenhouseJobBoard | greenhouse.py', ['list_jobs(): constructs URL, parses feed', 'fetch_details_api(): parses exact API job', 'discovery_pages(): public board URL'])
line(1190, 660, 1190, 575, dashed=True, arrow=True)
box(60, 690, 520, 175, 'ReadSession | transport.py', ['read_text() / read_json() / open()', 'Configured backend, timeout and proxy', 'Redirect, size and deadline enforcement'], '#f0f7ed')
line(170, 350, 170, 690, arrow=True)
text(190, 635, 'creates and injects', 18)
line(940, 765, 580, 765, arrow=True); text(760, 745, 'uses for HTTP', 18, anchor='middle')
text(750, 945, 'Other providers implement the same contract; generic HTML parsing lives in parsing.py.', 21, anchor='middle')
finish('02-class-relationships', 2)


def participants(labels):
    xs = [100 + i*1300/(len(labels)-1) for i in range(len(labels))]
    for x, label in zip(xs, labels):
        box(x-95, 130, 190, 90, label[0], label[1:], '#edf4ff')
        line(x, 220, x, 920, dashed=True, color='#9bb3cc')
    return xs


def message(xs, source, target, y, label, returned=False):
    x1, x2 = xs[source], xs[target]
    line(x1, y, x2, y, dashed=returned, arrow=True)
    text((x1+x2)/2, y-12, label, 17, anchor='middle')


start('3. Bookmark retrieval sequence', 'A Greenhouse example. API and scraping attempts consume one shared operation deadline.')
xs = participants([('StageExecutor', 'run_agent()'), ('bookmark_read', 'read_discovered_posting'), ('Framework', 'fetch_details() / _read()'), ('Board adapter', 'GreenhouseJobBoard'), ('ReadSession', 'Shared HTTP transport')])
message(xs, 0, 1, 270, 'Read selected posting URL')
message(xs, 1, 2, 325, 'fetch_posting_details(url) -> fetch_details(context)')
text(xs[2]+12, 367, 'Resolve provider; merge policies; start deadline', 16)
message(xs, 2, 3, 410, 'fetch_details_api(context, API session)')
message(xs, 3, 4, 465, 'read_json(exact API URL)')
message(xs, 4, 3, 520, 'API response or retrieval error', True)
message(xs, 3, 2, 575, 'Normalized details or failure', True)
text(750, 620, 'API success: validate and return. On failure: try configured scraping routes below.', 19, '#25624a', anchor='middle', bold=True)
message(xs, 2, 3, 680, 'fetch_details_scrape(context, scraping session)')
message(xs, 3, 4, 735, 'read_text(posting URL), direct or proxy')
message(xs, 4, 3, 790, 'HTML to parse', True)
message(xs, 3, 2, 845, 'Normalized details; framework validates', True)
message(xs, 2, 1, 900, 'Validated result or final retrieval failure', True)
text(750, 960, 'If all HTTP reads fail, bookmark_read tries the configured model fallback before returning to the stage.', 19, anchor='middle')
finish('03-bookmark-sequence', 3)

start('4. Live native-browser submission sequence', 'The live ChatGPT host flow uses Sessions and ReadySubmission helpers; it bypasses framework.submit().')
xs = participants([('ChatGPT host', 'Stage orchestrator'), ('Sessions', 'chatgpt_tasks.py'), ('Application Service', 'State and receipts'), ('Native Browser', 'Employer website')])
message(xs, 0, 1, 280, 'Obtain task and prepared package')
message(xs, 1, 2, 345, 'Read package / validate current application')
message(xs, 1, 0, 410, 'Package and task context', True)
message(xs, 0, 1, 475, 'begin_submission()')
message(xs, 1, 2, 540, 'Persist pending receipt before final submit')
message(xs, 1, 0, 605, 'may_submit = true', True)
message(xs, 0, 3, 670, 'Perform final submit action once')
message(xs, 3, 0, 735, 'Observe confirmation, blocker or uncertainty', True)
message(xs, 0, 1, 800, 'complete_submission(observed outcome)')
message(xs, 1, 2, 865, 'Validate evidence; save receipt and transition')
text(750, 955, 'The callback-based ReadySubmission.execute() path separately calls framework.submit() once, with no retry.', 19, anchor='middle')
finish('04-native-submission-sequence', 4)
PDF.save()
print('Created four SVG diagrams and job-board-architecture.pdf in', ROOT)
