"""Mail, calendar, interview research, reporting and orchestration scheduling."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta,date
from .contracts import Event
from .stages import classify_correspondence
from services.external_event_polling import matching_application

class EmailCorrespondenceObserver:
    def __init__(self,fetch_mail,applications,report_outcome,reply=None):
        self.fetch_mail,self.applications,self.report_outcome,self.reply=fetch_mail,applications,report_outcome,reply

    def scan(self):
        active=[a for a in self.applications() if a["status"] in {"Applied","Interviewing"}]
        processed=[]
        for email in self.fetch_mail():
            text=" ".join(str(email.get(k,"")) for k in ("sender","subject","body"))
            match=matching_application(text,active)
            if not match: continue
            result=classify_correspondence(match["application_id"],text)
            self.report_outcome(match["status"],result,match)
            if result["code"]=="FOLLOW_UP" and self.reply:
                self.reply(email,match)  # approved mail adapter controls send or draft
            processed.append(result)
        return processed

class ResearchAgent:
    def __init__(self,application_events,researcher,create_research_tab):
        self.application_events,self.researcher,self.create_research_tab=application_events,researcher,create_research_tab

    def prepare(self,item_id,meeting):
        history=self.application_events(item_id)
        report=self.researcher({"meeting":meeting,"application_events":history})
        if not isinstance(report,str) or not report.strip():
            raise ValueError("Researcher returned empty report")
        title="Interview research "+str(meeting.get("date") or date.today())
        return self.create_research_tab(item_id,title,report)

class CalendarObserver:
    def __init__(self,fetch_calendar,applications,research,report_outcome,index):
        self.fetch_calendar,self.applications=fetch_calendar,applications
        self.research,self.report_outcome,self.index=research,report_outcome,index

    def scan(self):
        active=[a for a in self.applications() if a["status"] in {"Applied","Interviewing"}]
        processed=[]
        for meeting in self.fetch_calendar():
            text=" ".join(str(meeting.get(k,"")) for k in ("title","description","organizer"))
            if not any(w in text.lower() for w in ("interview","recruit","hiring","ראיון")): continue
            match=matching_application(text,active)
            if not match: continue
            key="calendar:"+str(meeting.get("id",""))
            if self.index.checkpoint(key): continue
            research_tab=self.research.prepare(match["application_id"],meeting)
            payload={"agent":"calendar_observer","item_id":match["application_id"],
                     "code":"INTERVIEW_FOUND","execution_status":"SUCCESS",
                     "summary":"Interview scheduled and research saved",
                     "data":{"research_tab_id":research_tab,"meeting_id":meeting.get("id")}}
            self.report_outcome(match["status"],payload,match)
            self.index.checkpoint(key,{"item_id":match["application_id"],"research_tab":research_tab})
            processed.append(payload)
        return processed

def daily_report(index,applications,now=None):
    now=now or datetime.now(timezone.utc)
    yesterday=(now-timedelta(days=1)).date()
    apps=list(applications())
    applied_count=0
    for app in apps:
        for event in index.events(app["application_id"]):
            if event.get("current_state")=="Applied":
                try:
                    if datetime.fromisoformat(event["created_at"]).date()==yesterday:
                        applied_count+=1;break
                except (ValueError,KeyError): pass
    manual=sum(1 for a in apps if a["status"]=="Manual Apply Required")
    interviews=sorted((a for a in apps if a.get("next_interview") and a["status"]=="Interviewing"),
                       key=lambda a:a["next_interview"])
    unscheduled=sum(1 for a in apps if a.get("schedule_required"))
    return {"date":now.date().isoformat(),"applied_yesterday":applied_count,
            "awaiting_manual_application":manual,"meetings_to_schedule":unscheduled,
            "next_interview":interviews[0] if interviews else None}

class Scheduler:
    """Call tick() from a process timer every second; not a ChatGPT background task."""
    def __init__(self,index,orchestrator,email=None,calendar=None,report=None):
        self.index,self.orchestrator=index,orchestrator
        self.email,self.calendar,self.report=email,calendar,report

    def tick(self,now=None):
        now=now or datetime.now(timezone.utc)
        completed={"dispatch":self.orchestrator.dispatch_tick()}
        # Durable window admission; callbacks should be idempotent.
        slot=now.strftime("%Y-%m-%dT%H")
        for name,handler in (("email",self.email),("calendar",self.calendar)):
            if handler and self.index.checkpoint("schedule:"+name)!=slot:
                handler()
                self.index.checkpoint("schedule:"+name,slot)
                completed[name]=True
        day=now.date().isoformat()
        if self.report and self.index.checkpoint("schedule:daily_report")!=day:
            self.report()
            self.index.checkpoint("schedule:daily_report",day)
            completed["report"]=True
        return completed
