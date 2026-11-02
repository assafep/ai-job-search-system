from unittest.mock import Mock
import pytest
from ajss.worker.effects import SaveResearch, apply_effects
from ajss.worker.observers import EmailCorrespondenceObserver, CalendarObserver, ResearchAgent
from ajss.worker.stages import classify_correspondence, reply_to_interview_request
from ajss.worker.reporting import DailyReportAgent


@pytest.mark.parametrize("text,code", [
    ("Unfortunately not selected","REJECTED"),
    ("Pleased to offer a job offer","OFFER"),
    ("Schedule an interview","FOLLOW_UP"),
    ("General newsletter","AMBIGUOUS"),
])
def test_correspondence_classification_is_repeatable(text,code):
    first = classify_correspondence("item-1",text)
    assert first == classify_correspondence("item-1",text)
    assert first["agent"] == "email_correspondence"
    assert first["code"] == code


def mail_observer(index,applications,code="FOLLOW_UP"):
    message = {"message_id":"mail-1", "sender":"Example Labs", "subject":"Fixture", "body":"Interview"}
    fetch = Mock(return_value=[message])
    apps = Mock(return_value=applications)
    classify = Mock(return_value={"agent":"email_correspondence", "item_id":"item-1", "code":code})
    report, reply = Mock(), Mock()
    return EmailCorrespondenceObserver(fetch,apps,report,reply,index,classify), classify, report, reply, message


@pytest.mark.parametrize("code,replies", [("FOLLOW_UP",1),("REJECTED",0),("AMBIGUOUS",0)])
def test_email_reports_once_and_only_replies_to_followup(index,applications,code,replies):
    observer,classifier,report,reply,message = mail_observer(index,applications,code)
    processed = observer.scan()
    assert len(processed) == 1
    classifier.assert_called_once_with("item-1","Example Labs Fixture Interview")
    report.assert_called_once_with("Applied",processed[0],applications[0])
    reply.assert_not_called()
    assert index.checkpoint("mail:mail-1") is None
    apply_effects(processed[0], {"index": index})
    assert observer.scan() == []
    assert reply.call_count == replies
    if replies:reply.assert_called_once_with(message,applications[0])
    assert index.checkpoint("mail:mail-1") == {"item_id":"item-1"}


@pytest.mark.parametrize("mode", ["unmatched","ambiguous","inactive"])
def test_email_without_unique_active_match_has_no_effect(index,applications,mode):
    if mode=="unmatched":applications[0]["company"]="Other Labs"
    elif mode=="ambiguous":applications.append(dict(applications[0],application_id="item-2"))
    else:applications[0]["status"]="Rejected"
    observer,classifier,report,reply,_ = mail_observer(index,applications)
    assert observer.scan() == []
    classifier.assert_not_called(); report.assert_not_called(); reply.assert_not_called()
    assert index.checkpoint("mail:mail-1") is None


def test_email_report_failure_does_not_reply_or_mark_complete(index,applications):
    observer,_,report,reply,_ = mail_observer(index,applications)
    report.side_effect = RuntimeError("fixture failure")
    with pytest.raises(RuntimeError):observer.scan()
    reply.assert_not_called()
    assert index.checkpoint("mail:mail-1") is None


def test_reply_defaults_to_draft(item):
    mail = Mock(return_value="draft-id")
    assert reply_to_interview_request(item,"recruiter@example.test","https://schedule.example.test", "",mail)=="draft-id"
    assert mail.call_args.kwargs == {"send":False}
    with pytest.raises(ValueError):reply_to_interview_request(item,"","", "",mail)
    mail.assert_called_once()


def calendar_observer(index,applications):
    meeting = {"event_id":"meeting-1","title":"Example Labs interview","start":"2026-11-03T12:00:00Z"}
    research = Mock(spec_set=["prepare", "create_research_tab"])
    research.prepare.return_value = {"title":"Interview research 2026-11-03","report":"Fixed briefing"}
    research.create_research_tab.return_value = "research-tab"
    report = Mock()
    observer = CalendarObserver(Mock(return_value=[meeting]),Mock(return_value=applications),research,report,index)
    return observer,research,report,meeting


def test_calendar_research_and_report_are_once_per_meeting(index,applications):
    observer,research,report,meeting = calendar_observer(index,applications)
    result = observer.scan()
    assert result[0]["code"] == "INTERVIEW_FOUND"
    assert result[0]["data"] == {"meeting_id":"meeting-1"}
    research.create_research_tab.assert_not_called()
    assert index.checkpoint("calendar:meeting-1") is None
    applied = apply_effects(result[0], {"index": index})
    assert applied["data"] == {"research_tab_id":"research-tab","meeting_id":"meeting-1"}
    assert observer.scan() == []
    research.prepare.assert_called_once_with("item-1",meeting)
    research.create_research_tab.assert_called_once_with("item-1","Interview research 2026-11-03","Fixed briefing")
    report.assert_called_once_with("Applied",result[0],applications[0])


def test_calendar_research_failure_does_not_commit(index,applications):
    observer,research,report,_ = calendar_observer(index,applications)
    research.prepare.side_effect=TimeoutError("fixture")
    with pytest.raises(TimeoutError):observer.scan()
    report.assert_not_called()
    assert index.checkpoint("calendar:meeting-1") is None


@pytest.mark.parametrize("mode", ["not_interview","unmatched","ambiguous","inactive"])
def test_calendar_irrelevant_or_ambiguous_event_is_ignored(index,applications,mode):
    observer,research,report,meeting = calendar_observer(index,applications)
    if mode=="not_interview":meeting["title"]="Example Labs newsletter"
    elif mode=="unmatched":meeting["title"]="Other Labs interview"
    elif mode=="ambiguous":applications.append(dict(applications[0],application_id="item-2"))
    else:applications[0]["status"]="Rejected"
    assert observer.scan() == []
    research.prepare.assert_not_called(); report.assert_not_called()


def test_research_uses_history_and_fixed_date():
    history = [{"event_type":"FIXTURE"}]
    events = Mock(return_value=history)
    researcher = Mock(return_value="Fixed briefing")
    save = Mock(return_value="research-tab")
    agent = ResearchAgent(events,researcher,save)
    plan = agent.prepare("item-1",{})
    assert plan == {"title":"Interview research 2026-11-02","report":"Fixed briefing"}
    save.assert_not_called()
    assert SaveResearch("item-1",plan["title"],plan["report"],save).run({}) == {"research_tab_id":"research-tab"}
    events.assert_called_once_with("item-1")
    researcher.assert_called_once_with({"meeting":{},"application_events":history})
    save.assert_called_once_with("item-1","Interview research 2026-11-02","Fixed briefing")


@pytest.mark.parametrize("answer", [None,"", "   ",42])
def test_empty_research_never_saves(answer):
    save = Mock()
    agent = ResearchAgent(Mock(return_value=[]),Mock(return_value=answer),save)
    with pytest.raises(ValueError):agent.prepare("item-1",{})
    save.assert_not_called()


def test_research_timeout_never_saves():
    save = Mock()
    agent = ResearchAgent(Mock(return_value=[]),Mock(side_effect=TimeoutError("fixture")),save)
    with pytest.raises(TimeoutError):agent.prepare("item-1",{})
    save.assert_not_called()


def test_daily_report_uses_fixed_summary_and_pdf(monkeypatch,index):
    summary = {"date":"2026-11-02","applied_yesterday":2,"awaiting_manual_application":1,"meetings_to_schedule":0,"next_interview":None}
    summarize = Mock(return_value=summary)
    render = Mock(return_value=b"%PDF-fixed")
    monkeypatch.setattr("ajss.worker.reporting.daily_report",summarize)
    monkeypatch.setattr("ajss.worker.reporting.render_pdf",render)
    apps,deliver = Mock(),Mock()
    agent = DailyReportAgent(index,apps,deliver)
    assert agent.run() == summary
    summarize.assert_called_once_with(index,apps)
    render.assert_called_once_with(summary)
    deliver.assert_called_once_with("AJSS daily report 2026-11-02","ajss-daily-report.pdf",b"%PDF-fixed",summary)


def test_daily_report_render_failure_never_delivers(monkeypatch,index):
    monkeypatch.setattr("ajss.worker.reporting.daily_report",Mock(return_value={"date":"2026-11-02"}))
    monkeypatch.setattr("ajss.worker.reporting.render_pdf",Mock(side_effect=ValueError("fixture")))
    deliver=Mock()
    with pytest.raises(ValueError):DailyReportAgent(index,Mock(),deliver).run()
    deliver.assert_not_called()
