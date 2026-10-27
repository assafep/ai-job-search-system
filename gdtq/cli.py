"""GDTQ operator commands.

  python -m gdtq.cli doctor
  python -m gdtq.cli run --database ./var/ajss-gdtq.sqlite3

Requires existing config/runtime.json and Google credentials. Dry-run doctor
needs neither. The production worker uses the existing application agent for
cover-letter composition; browser submission adapters must be explicitly wired.
"""
from __future__ import annotations
import argparse
import json
import os
from io import BytesIO
from urllib.parse import urlsplit
from .policy import StateTransitionEngine
from .runtime import build_google_runtime,wire_orchestrator,run_forever
from .handlers import build_handlers
from .observers import Scheduler

def doc_id(value):
    import re
    match=re.search(r"/document/(?:u/\d+/)?d/([^/?]+)",value)
    if not match: raise ValueError("Not a Google Docs document URL")
    return match.group(1)

def provider_writer():
    """Reuses the repository's stateless application agent and verified schema."""
    from adapters.llm.openai_provider import OpenAIProvider
    from services.agent_runner import run
    from pypdf import PdfReader
    provider=OpenAIProvider()
    if not provider.api_key: raise RuntimeError("OPENAI_API_KEY required for preparation")
    def write(context):
        resume=context["resume_pdf"]
        reader=PdfReader(BytesIO(resume))
        text="\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():raise ValueError("Resume PDF contains no extractable text")
        result=run("application",{"application":{
             "company":context["company"],"role":context["role"],
             "posting_content":{"description":context["description"]}},
             "applicant_profile":context["profile"],"resume_text":text},provider)
        return result["cover_letter"]
    return write

def main(argv=None):
    parser=argparse.ArgumentParser(description="Google Doc Tab Queue runtime")
    sub=parser.add_subparsers(dest="action",required=True)
    sub.add_parser("doctor")
    run=sub.add_parser("run")
    run.add_argument("--database",default=os.environ.get("AJSS_GDTQ_DB","var/ajss-gdtq.sqlite3"))
    run.add_argument("--resume-path",default=os.environ.get("AJSS_RESUME_DRIVE_PATH"))
    run.add_argument("--tick-seconds",type=float,default=1.0)
    args=parser.parse_args(argv)
    engine=StateTransitionEngine()
    if args.action=="doctor":
        print(json.dumps({"status":"ok","rules_version":engine.version,
             "states":len(engine.transitions),"agents":list(engine.routes)},indent=2))
        return 0
    from services.runtime_config import load
    cfg=load(require=True)
    drive=cfg["google_drive"]
    runtime=build_google_runtime(doc_id(drive["interview_journal_url"]),
               drive["artifact_root_url"],args.database)
    runtime["queue"].ensure_state_tabs(
        list(runtime["engine"].transitions)+[name+"-onFlight" for name in
         ("Discovered","Bookmarked","Validated","Prepared","Applied","Interviewing",
          "Manual Apply Required","Dead Letters Queue")])
    if not args.resume_path:
        raise RuntimeError("Set AJSS_RESUME_DRIVE_PATH to the verified resume PDF in Drive")
    writer=provider_writer()
    # Explicitly configured worker; no browser or email send capability is
    # granted to this process by default.
    handlers=build_handlers(runtime,writer=writer,resume_path=args.resume_path,
        match_threshold=float(os.environ.get("AJSS_SKILL_THRESHOLD","0")),
        calendly_url=(cfg.get("scheduling") or {}).get("calendly_url"))
    orch,dispatcher=wire_orchestrator(runtime,handlers)
    email_scan=calendar_scan=daily=None
    if os.environ.get("AJSS_GDTQ_ENABLE_OBSERVERS")=="1":
        from .integrations import build_observers
        from .observers import Scheduler
        from adapters.llm.openai_provider import OpenAIProvider
        # Discovery and validation still run deterministically. Research
        # requires an explicitly configured provider and authorized scopes.
        from services.runtime_config import calendly_url as configured_calendly
        recipient=os.environ.get("AJSS_REPORT_RECIPIENT","")
        if not recipient:raise RuntimeError("Set AJSS_REPORT_RECIPIENT before enabling observers")
        from .integrations import research_provider
        mail,calendar,report=build_observers(runtime,orch,
            researcher=OpenAIProvider(),report_recipient=recipient,
            calendly_url=configured_calendly(cfg) or "",
            phone_number=os.environ.get("AJSS_CONTACT_PHONE",""),
            allow_send=os.environ.get("AJSS_GMAIL_ALLOW_SEND")=="1")
        email_scan,calendar_scan,daily=mail.scan,calendar.scan,report.run
    scheduler=Scheduler(runtime["index"],orch,email=email_scan,calendar=calendar_scan,
                        report=daily,report_timezone="Asia/Jerusalem",report_hour=8)
    try:run_forever(scheduler,tick_seconds=args.tick_seconds)
    finally:
        dispatcher.close()
        runtime["index"].close()
    return 0

if __name__=="__main__":
    raise SystemExit(main())
