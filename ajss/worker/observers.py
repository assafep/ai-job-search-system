"""Mail, calendar, interview research, reporting and orchestration scheduling."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta,date
from ajss.queue.contracts import Event
from ajss.worker.stages import classify_correspondence
from ajss.worker.matching import matching_application
from ajss.worker.effects import RememberCheckpoint, SaveResearch, SendReply, attach

class EmailCorrespondenceObserver:
    def __init__(self,fetch_mail,applications,report_outcome,reply=None,index=None,classifier=None):
        self.fetch_mail,self.applications,self.report_outcome,self.reply=fetch_mail,applications,report_outcome,reply
        self.index,self.classifier=index,classifier or classify_correspondence

    def scan(self):
        active=[a for a in self.applications() if a["status"] in {"Applied","Interviewing"}]
        processed=[]
        for email in self.fetch_mail():
            message_id=email.get("message_id") or email.get("id")
            if message_id and self.index and self.index.checkpoint("mail:"+message_id):
                continue
            text=" ".join(str(email.get(k,"")) for k in ("sender","subject","body"))
            match=matching_application(text,active)
            if not match: continue
            result=self.classifier(match["application_id"],text)
            effects=[]
            if result["code"]=="FOLLOW_UP" and self.reply:
                effects.append(SendReply(self.reply,email,match))
            if message_id and self.index:
                effects.append(RememberCheckpoint("mail:"+message_id,{"item_id":match["application_id"]}))
            if effects:
                result=attach(result,*effects)
            self.report_outcome(match["status"],result,match)
            processed.append(result)
        return processed

class ResearchAgent:
    def __init__(self,application_events,researcher,create_research_tab):
        self.application_events,self.researcher,self.create_research_tab=application_events,researcher,create_research_tab

    def prepare(self,item_id,meeting):
        """Compute the briefing. The orchestrator creates the research tab."""
        history=self.application_events(item_id)
        report=self.researcher({"meeting":meeting,"application_events":history})
        if not isinstance(report,str) or not report.strip():
            raise ValueError("Researcher returned empty report")
        title="Interview research "+str(meeting.get("start") or meeting.get("date") or date.today())[:10]
        return {"title":title,"report":report}

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
            import hashlib,json
            unique=meeting.get("event_id") or meeting.get("meeting_id") or meeting.get("id") or hashlib.sha256(json.dumps(meeting,sort_keys=True).encode()).hexdigest()[:24]
            key="calendar:"+str(unique)
            if self.index.checkpoint(key): continue
            plan=self.research.prepare(match["application_id"],meeting)
            item_id=match["application_id"]
            payload={"agent":"calendar_observer","item_id":item_id,
                     "code":"INTERVIEW_FOUND","execution_status":"SUCCESS",
                     "summary":"Interview scheduled and research saved",
                     "data":{"meeting_id":unique}}
            payload=attach(payload,
                SaveResearch(item_id,plan["title"],plan["report"],self.research.create_research_tab),
                RememberCheckpoint(key,lambda data,owner=item_id:{
                    "item_id":owner,"research_tab":data.get("research_tab_id")}))
            self.report_outcome(match["status"],payload,match)
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
    def __init__(self,index,orchestrator,email=None,calendar=None,report=None,
                 report_timezone="Asia/Jerusalem",report_hour=8):
        self.index,self.orchestrator=index,orchestrator
        self.email,self.calendar,self.report=email,calendar,report
        from zoneinfo import ZoneInfo
        self.report_zone=ZoneInfo(report_timezone)
        self.report_hour=report_hour
        if not 0<=report_hour<=23:raise ValueError("report_hour must be 0..23")

    def tick(self,now=None):
        now=now or datetime.now(timezone.utc)
        completed={}
        try:completed["dispatch"]=self.orchestrator.dispatch_tick()
        except Exception as exc:completed["dispatch_error"]=str(exc)
        # Durable window admission; callbacks should be idempotent.
        slot=now.strftime("%Y-%m-%dT%H")
        for name,handler in (("email",self.email),("calendar",self.calendar)):
            if handler and self.index.checkpoint("schedule:"+name)!=slot:
                try:
                    handler()
                except Exception as exc:
                    completed[name+"_error"]=str(exc)
                else:
                    self.index.checkpoint("schedule:"+name,slot)
                    completed[name]=True
        local=now.astimezone(self.report_zone)
        day=local.date().isoformat()
        if self.report and local.hour>=self.report_hour and self.index.checkpoint("schedule:daily_report")!=day:
            try:self.report()
            except Exception as exc:completed["report_error"]=str(exc)
            else:
                self.index.checkpoint("schedule:daily_report",day)
                completed["report"]=True
        return completed
