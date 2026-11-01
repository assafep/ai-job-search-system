"""Optional Gmail/Calendar wiring for the existing event source, LLM agent and report tools."""
from __future__ import annotations
from email.utils import parseaddr
from adapters.google_events import GoogleEventSource
from adapters.google.auth import token_from_environment
from .gmail import GmailActions
from .observers import EmailCorrespondenceObserver,CalendarObserver,ResearchAgent,daily_report
from .reporting import DailyReportAgent
from .stages import reply_to_interview_request
from .runtime import ApplicationContextLoader

def research_provider(provider):
    from gdtq.agent_runner import run
    def invoke(context):
        output=run("interview-prep",context,provider)
        return output["briefing"]
    return invoke

def build_observers(runtime,orchestrator,*,researcher,report_recipient,
                    calendly_url,phone_number,allow_send=False,provider=None):
    """Connect only with explicit Gmail send consent and a configured recipient.

    Gmail and Calendar readers use the existing OAuth-scoped adapters.
    """
    queue=runtime["queue"];index=runtime["index"];loader=runtime["loader"]
    source=GoogleEventSource()
    gmail=GmailActions(token_from_environment(),allow_send=allow_send)
    def active_applications():
        states=("Applied","Interviewing","Manual Apply Required")
        items=[]
        from .queue import children,prop
        doc=queue.snapshot()
        for state in states:
            try:root=queue.queue_tab(doc,state)
            except Exception:continue
            for tab in children(doc,prop(root,"tabId")):
                app=loader.application(prop(tab,"tabId"))
                app["application_id"]=app["item_id"];app["status"]=state
                items.append(app)
        return items
    def identity(item_id):return loader.identity(item_id)
    def report_outcome(source_state,payload,item):
        return orchestrator.handle_external(source_state,payload["item_id"],payload,identity)
    def reply(mail,item):
        recipient=parseaddr(mail["sender"])[1]
        # Safe default is a Gmail draft. Sending requires explicit grant/policy.
        return reply_to_interview_request(item,recipient,calendly_url,phone_number,
             lambda item,recipient,body,send=False: gmail.compose(recipient,
               "Re: "+mail.get("subject","Interview scheduling"),body,
               thread_id=mail.get("thread_id"),send=send),allow_send)
    mail=EmailCorrespondenceObserver(
         lambda:source.gmail([a["company"] for a in active_applications()])[0],
         active_applications,report_outcome,reply,index)
    research=ResearchAgent(queue.application_events,research_provider(researcher),
                           queue.create_research_tab)
    calendar=CalendarObserver(lambda:source.calendar()[0],active_applications,
                              research,report_outcome,index)
    report=DailyReportAgent(index,active_applications,
        lambda subject,filename,pdf,summary: gmail.compose(report_recipient,subject,
            "Attached: today's AJSS activity report.",send=allow_send,
            attachment=(filename,pdf)))
    return mail,calendar,report
