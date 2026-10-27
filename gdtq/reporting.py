"""Daily report rendering and explicit output adapter."""
from io import BytesIO
from .observers import daily_report

def render_pdf(summary):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    from xml.sax.saxutils import escape
    result=BytesIO()
    styles=getSampleStyleSheet()
    story=[Paragraph("AJSS Daily Report — "+escape(summary["date"]),styles["Title"]),Spacer(1,16)]
    for label,key in (("Applications submitted yesterday","applied_yesterday"),
                      ("Awaiting manual application","awaiting_manual_application"),
                      ("Interviews to schedule","meetings_to_schedule")):
        story.append(Paragraph(label+": "+str(summary[key]),styles["Normal"]))
        story.append(Spacer(1,10))
    interview=summary.get("next_interview")
    story.append(Paragraph("Next interview: "+escape(str(interview or "Not scheduled")),styles["Normal"]))
    SimpleDocTemplate(result,pagesize=A4).build(story)
    return result.getvalue()

class DailyReportAgent:
    def __init__(self,index,applications,deliver):
        self.index,self.applications,self.deliver=index,applications,deliver

    def run(self):
        summary=daily_report(self.index,self.applications)
        pdf=render_pdf(summary)
        # deliver accepts subject, attachment filename, attachment bytes,
        # metadata. This component never infers or invents email recipients.
        self.deliver("AJSS daily report "+summary["date"],
                     "ajss-daily-report.pdf",pdf,summary)
        return summary
