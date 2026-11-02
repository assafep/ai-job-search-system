"""AJSS operator commands.

  python -m ajss.cli doctor
  python -m ajss.cli run --database ./var/ajss-gdtq.sqlite3

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
from ajss.queue.policy import StateTransitionEngine
from ajss.worker.runtime import build_google_runtime,wire_orchestrator,run_forever
from ajss.worker.handlers import build_handlers
from ajss.worker.observers import Scheduler

def doc_id(value):
    import re
    match=re.search(r"/document/(?:u/\d+/)?d/([^/?]+)",value)
    if not match: raise ValueError("Not a Google Docs document URL")
    return match.group(1)

def _profile_personal(runtime):
    try:
        return runtime["loader"].profile().get("personal") or {}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}

def _contact_phone(runtime):
    """Prefer an explicit env number, then the phone saved on the applicant profile."""
    configured = os.environ.get("AJSS_CONTACT_PHONE", "").strip()
    if configured:
        return configured
    return str(_profile_personal(runtime).get("phone") or "").strip()

def _scheduling_url(runtime, cfg):
    """Prefer the Calendly link stored on the applicant profile."""
    saved = str(_profile_personal(runtime).get("calendly_url") or "").strip()
    if saved:
        return saved
    from ajss.settings import calendly_url as configured_calendly
    return str(configured_calendly(cfg) or "").strip()

def provider_writer():
    """Reuses the repository's stateless application agent and verified schema."""
    from adapters.llm.openai_provider import OpenAIProvider
    from ajss.documents.prompts import run
    from pypdf import PdfReader
    provider=OpenAIProvider()
    if not provider.configured:
        if provider.key_required and not provider.api_key:
            raise RuntimeError("Set AJSS_LLM_API_KEY for preparation")
        if not provider.model:
            raise RuntimeError("Set AJSS_LLM_MODEL for preparation")
        raise RuntimeError("Set AJSS_LLM_PROVIDER for preparation")
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
    sub.add_parser("bootstrap", help="Prepare the local workspace for mocked integration tests")
    onboard=sub.add_parser("onboard", help="Show the next idempotent local-run setup step")
    onboard.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    onboard.add_argument("--stage", default="", metavar="STEP")
    onboard.add_argument("--listen", action="store_true")
    onboard.add_argument("--check", action="store_true")
    onboard.add_argument("--root", default=None)
    sub.add_parser("onboard-serve", help="Keep the local-run checklist process running")
    sub.add_parser("sanity", help="Check that a completed local setup is valid and working")
    run=sub.add_parser("run")
    run.add_argument("--database",default=os.environ.get("AJSS_GDTQ_DB","var/ajss-gdtq.sqlite3"))
    run.add_argument("--resume-path",default=None)
    run.add_argument("--tick-seconds",type=float,default=1.0)
    args=parser.parse_args(argv)
    engine=StateTransitionEngine()
    if args.action=="doctor":
        print(json.dumps({"status":"ok","rules_version":engine.version,
             "states":len(engine.transitions),"agents":list(engine.routes)},indent=2))
        return 0
    if args.action=="bootstrap":
        from ajss.onboard.bootstrap import main as bootstrap_main
        return bootstrap_main()
    if args.action=="onboard":
        from ajss.onboard.checklist import run_onboard
        return run_onboard(args)
    if args.action=="onboard-serve":
        from ajss.onboard.session import serve
        serve()
        return 0
    if args.action=="sanity":
        from ajss.onboard.sanity import main as sanity_main
        return sanity_main()
    from ajss.onboard.checklist import load_project_env
    load_project_env()
    from ajss.settings import load
    cfg=load(require=True)
    drive=cfg["google_drive"]
    runtime=build_google_runtime(doc_id(drive["interview_journal_url"]),
               drive["artifact_root_url"],args.database)
    runtime["queue"].ensure_state_tabs(
        list(runtime["engine"].transitions)+[name+"-onFlight" for name in
         ("Discovered","Bookmarked","Validated","Prepared","Applied","Interviewing",
          "Manual Apply Required","Dead Letters Queue")
          if runtime["queue"].flight_index is None])
    from ajss.settings import resume_drive_path
    resume_path=args.resume_path or resume_drive_path()
    if not resume_path:
        raise RuntimeError("Set GOOGLE_RESUME_DRIVE_PATH to the verified resume PDF in Drive")
    writer=provider_writer()
    # Explicitly configured worker; no browser or email send capability is
    # granted to this process by default.
    from ajss.submission import configured_strategies
    handlers=build_handlers(runtime,writer=writer,resume_path=resume_path,
        submission_strategies=configured_strategies(runtime),
        match_threshold=float(os.environ.get("AJSS_SKILL_THRESHOLD","0")),
        calendly_url=_scheduling_url(runtime, cfg) or None)
    orch,dispatcher=wire_orchestrator(runtime,handlers)
    email_scan=calendar_scan=daily=None
    if os.environ.get("AJSS_GDTQ_ENABLE_OBSERVERS")=="1":
        from ajss.worker.integrations import build_observers
        from ajss.worker.observers import Scheduler
        from adapters.llm.openai_provider import OpenAIProvider
        # Discovery and validation still run deterministically. Research
        # requires an explicitly configured provider and authorized scopes.
        recipient=os.environ.get("AJSS_REPORT_RECIPIENT","")
        if not recipient:raise RuntimeError("Set AJSS_REPORT_RECIPIENT before enabling observers")
        from ajss.worker.integrations import research_provider
        mail,calendar,report=build_observers(runtime,orch,
            researcher=OpenAIProvider(),report_recipient=recipient,
            calendly_url=_scheduling_url(runtime, cfg),
            phone_number=_contact_phone(runtime),
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
