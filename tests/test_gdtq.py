"""GDTQ deterministic contract tests; no Google account or live network required."""
import copy
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4
import pytest

from gdtq.queue import GDTQ, QueueConflict, find_tab, prop
from gdtq.contracts import Event
from gdtq.store import WorkIndex,posting_keys
from gdtq.policy import StateTransitionEngine,RoutingError
from gdtq.scoring import score_posting,decide_match
from gdtq.downstream import CompletionPublisher
from gdtq.stages import bookmark_discovered, submit_prepared
from gdtq.dispatch import WorkloadOrchestrator

def tab(tab_id,title,parent=None,index=0):
    p={"tabId":tab_id,"title":title,"index":index}
    if parent:p["parentTabId"]=parent
    return {"tabProperties":p,"documentTab":{"body":{"content":[]}},"childTabs":[]}

class FakeDocs:
    document_id="doc-example"
    def __init__(self):
        self.document={"revisionId":"0","tabs":[tab("q-discovered","Discovered"),
                       tab("q-bookmarked","Bookmarked"),tab("q-validated","Validated"),
                       tab("q-prepared","Prepared"),tab("q-dlq","Dead Letters Queue")]}
        self.document["tabs"][0]["childTabs"].append(tab("item-1","Company / Backend","q-discovered"))

    def invalidate_document(self):pass
    def get_document(self):return copy.deepcopy(self.document)
    def batch_update(self,requests,revision_id=None):
        if revision_id is not None and revision_id!=self.document["revisionId"]:
            raise ValueError("Concurrent revision")
        responses=[]
        for request in requests:
            if "addDocumentTab" in request:
                p=request["addDocumentTab"]["tabProperties"]
                obj=tab("tab-"+str(uuid4()),p["title"],p.get("parentTabId"))
                parent=find_tab(self.document,p.get("parentTabId")) if p.get("parentTabId") else None
                (parent["childTabs"] if parent else self.document["tabs"]).append(obj)
                responses.append({"addDocumentTab":{"tabProperties":obj["tabProperties"]}})
            elif "updateDocumentTabProperties" in request:
                p=request["updateDocumentTabProperties"]["tabProperties"]
                obj=find_tab(self.document,p["tabId"])
                for key in request["updateDocumentTabProperties"]["fields"].split(","):
                    obj["tabProperties"][key.strip()]=p[key.strip()]
                responses.append({})
            elif "insertText" in request:
                p=request["insertText"]
                tid=p.get("endOfSegmentLocation",p.get("location",{}))["tabId"]
                obj=find_tab(self.document,tid)
                obj["documentTab"]["body"]["content"].append(
                    {"paragraph":{"elements":[{"textRun":{"content":p["text"]}}]}})
                responses.append({})
            elif "deleteTab" in request:
                ident=request["deleteTab"]["tabId"]
                def prune(tabs):
                    tabs[:]=[t for t in tabs if prop(t,"tabId")!=ident]
                    for t in tabs:prune(t["childTabs"])
                prune(self.document["tabs"])
                responses.append({})
            else:raise AssertionError(request)
        self.document["revisionId"]=str(int(self.document["revisionId"])+1)
        return {"replies":responses}

class FakeArtifactStore:
    def __init__(self):self.files={}
    def save_path(self,path,content,mime_type=None,idempotency_key=None):
        self.files[path]=content
        return {"id":path}
    def read_path(self,path):
        return {"content":self.files[path]}

def engine():return StateTransitionEngine()

def test_all_routes_resolve_and_legal():
    e=engine()
    for agent,rule in e.routes.items():
        for source in rule["source_states"]:
            for code,expected in rule["outcomes"].items():
                payload={"agent":agent,"item_id":"item-1","code":code,
                         "execution_status":"SUCCESS","summary":"","data":{}}
                decision=e.resolve(source,payload)
                assert decision.next_state==(source if expected=="@same" else expected)
                assert decision.terminal==(decision.next_state in e.terminal)

def test_unregistered_code_and_mismatched_agent_rejected():
    e=engine()
    payload={"agent":"validate_bookmarked","item_id":"item-1","code":"WHATEVER",
             "execution_status":"SUCCESS","summary":"","data":{}}
    with pytest.raises(RoutingError):e.resolve("Bookmarked",payload)
    payload["code"]="MATCH"
    with pytest.raises(RoutingError):e.resolve("Discovered",payload)

def test_retry_same_state_and_fatal_download_terminal():
    e=engine()
    p={"agent":"bookmark_discovered","item_id":"item-1","code":"ERROR",
       "execution_status":"RETRYABLE_FAILURE","summary":"","data":{}}
    assert e.resolve("Discovered",p).next_state=="Discovered"
    p["execution_status"]="PERMANENT_FAILURE"
    result=e.resolve("Discovered",p)
    assert result.next_state=="Failed" and result.terminal

def test_queue_pop_moves_source_to_onflight_and_push_targets_next_queue():
    client=FakeDocs()
    q=GDTQ(client)
    claimed=q.pop("Discovered")
    assert claimed.item_id=="item-1"
    assert prop(find_tab(q.snapshot(),"item-1"),"parentTabId")!= "q-discovered"
    event=Event(item_id="item-1",event_type="PROCESSING_COMPLETED",
                previous_state="Discovered",current_state="Bookmarked",processor="bookmark_discovered",
                outcome={"data":{"url":"https://example.org/jobs/1"}}).as_dict()
    moved=q.push("Discovered","Bookmarked","item-1",event)
    assert moved["queue"]=="Bookmarked"
    assert prop(find_tab(q.snapshot(),"item-1"),"parentTabId")=="q-bookmarked"
    assert q.application_events("item-1")[0]["event_id"]==event["event_id"]
    assert q.push("Discovered","Bookmarked","item-1",event)["replayed"]

def test_wrong_queue_and_event_rejected():
    q=GDTQ(FakeDocs())
    q.pop("Discovered")
    e=Event("item-1","COMPLETE","Wrong","Bookmarked","agent").as_dict()
    with pytest.raises(QueueConflict):q.push("Discovered","Bookmarked","item-1",e)

def test_claim_specific_item_for_external_events():
    q=GDTQ(FakeDocs())
    claimed=q.claim_item("Discovered","item-1")
    assert claimed.source_state=="Discovered"
    with pytest.raises(QueueConflict):q.claim_item("Discovered","item-1")

def test_index_deduplicates_by_url_and_company_role(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    assert index.reserve_identity("a","Example Co","Backend Engineer",
                                  "https://foo.com/job?utm_source=abc") is None
    assert index.reserve_identity("b","Elsewhere","Other","https://foo.com/job")=="a"
    assert index.reserve_identity("c","Example Co","Backend Engineer","https://other.com")=="a"
    assert index.reserve_identity("a","Example Co","Backend Engineer","https://foo.com/job") is None
    assert index.claim("a","Discovered","attempt1")
    assert not index.claim("a","Discovered","attempt2")
    assert index.release("a","attempt1")
    assert index.claim("a","Discovered","attempt2")

def test_scoring_unnormalized_and_threshold():
    profile={"skills":["Java","Kafka"]}
    posting="Java Java Kafka Kubernetes Kubernetes"
    result=score_posting(posting,["Java","Kafka","Kubernetes"],profile)
    assert result["score"]==1 and result["matched_occurrences"]==3
    assert result["unmatched_occurrences"]==2
    assert not decide_match(posting,["Java","Kafka","Kubernetes"],profile,2)["is_match"]

def test_completion_writes_then_deletes_and_preserves_identity(tmp_path):
    client=FakeDocs(); q=GDTQ(client); store=FakeArtifactStore();index=WorkIndex(tmp_path/"index.db")
    publisher=CompletionPublisher(q,store,index)
    result=publisher.complete("item-1","Example","Backend","Failed","404",source_state="Discovered")
    assert result["archive_path"].startswith("Failed/")
    content=json.loads(store.files[result["archive_path"]])
    assert content["final_state"]=="Failed"
    assert any(e["event_type"]=="COMPLETED" for e in content["events"])
    assert find_tab(q.snapshot(),"item-1") is None

def test_archive_failure_does_not_delete(tmp_path):
    q=GDTQ(FakeDocs());index=WorkIndex(tmp_path/"index.db")
    class BadStore(FakeArtifactStore):
        def read_path(self,path):raise IOError("failed to verify upload")
    with pytest.raises(IOError):
        CompletionPublisher(q,BadStore(),index).complete("item-1","A","B","Expired","time")
    assert find_tab(q.snapshot(),"item-1") is not None

def test_orchestrator_round_robin_skip_empty_and_terminal(tmp_path):
    client=FakeDocs();q=GDTQ(client);idx=WorkIndex(tmp_path/"index.db")
    dispatched=[]
    o=WorkloadOrchestrator(q,engine(),idx,lambda state,claimed:dispatched.append((state,claimed)),
                           queues=("Discovered","Bookmarked"))
    claimed=o.dispatch_tick()
    assert claimed.item_id=="item-1"
    assert dispatched[0][0]=="Discovered"
    assert o.dispatch_tick() is None
    assert o.dispatch_tick() is None

def test_bookmark_permanent_vs_transient_error(tmp_path):
    index=WorkIndex(tmp_path/"idx.db")
    item={"item_id":"i1","company":"A","role":"Backend","posting_url":"https://example.com/1"}
    def failure(url):raise ValueError("HTTP 404")
    permanent=bookmark_discovered(item,failure,index)
    assert permanent["execution_status"]=="PERMANENT_FAILURE"
    item2=dict(item,item_id="i2",role="Frontend",posting_url="https://example.com/2")
    def temporary(url):raise TimeoutError("timeout")
    retry=bookmark_discovered(item2,temporary,index)
    assert retry["execution_status"]=="RETRYABLE_FAILURE"

def test_submission_requires_confirmation_and_never_retries_unknown(tmp_path):
    idx=WorkIndex(tmp_path/"idx.db")
    item={"item_id":"i1","posting_url":"https://example.com"}
    attempts=[]
    def uncertain(item):
        attempts.append(True)
        raise TimeoutError("clicked submit but connection lost")
    result=submit_prepared(item,idx,[uncertain])
    assert result["code"]=="SUBMISSION_UNCONFIRMED"
    result=submit_prepared(item,idx,[uncertain])
    assert result["code"]=="SUBMISSION_UNCONFIRMED"
    assert len(attempts)==1

def test_all_modules_compile_and_import():
    import compileall
    assert compileall.compile_dir("gdtq",quiet=1,force=True)
    import gdtq.cli,gdtq.runtime,gdtq.manual,gdtq.integrations,gdtq.gmail
    from gdtq.cli import doc_id
    assert doc_id("https://docs.google.com/document/d/abcDEF/edit?tab=t.0")=="abcDEF"

def test_provision_missing_queues_and_research_tab():
    q=GDTQ(FakeDocs())
    assert q.ensure_state_tabs(["Applied","Interviewing","Manual Apply Required"])==[
        "Applied","Interviewing","Manual Apply Required"]
    assert q.ensure_state_tabs(["Applied"])==[]
    tab_id=q.create_research_tab("item-1","Interview research 2026-11-03","Notes for interview")
    assert q.create_research_tab("item-1","Interview research 2026-11-03","Notes for interview")==tab_id
    journal=q.application_events("item-1")
    assert any(e.get("event_type")=="RESEARCH_TAB" and "Notes" in e["content"] for e in journal)

def test_retry_backoff_blocks_repoll():
    q=GDTQ(FakeDocs())
    q.pop("Discovered")
    future=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    event=Event("item-1","PROCESSING_COMPLETED","Discovered","Discovered",
                "bookmark_discovered",outcome={"data":{"next_attempt_at":future}}).as_dict()
    q.push("Discovered","Discovered","item-1",event)
    assert q.pop("Discovered") is None
    past=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    q.append_event("item-1",Event("item-1","PROCESSING_COMPLETED","Discovered",
               "Discovered","bookmark_discovered",outcome={"data":{"next_attempt_at":past}}).as_dict())
    assert q.pop("Discovered").item_id=="item-1"

def test_external_manual_claim_and_transition(tmp_path):
    from gdtq.manual import manual_transition
    docs=FakeDocs()
    q=GDTQ(docs)
    q.ensure_state_tabs(["Manual Apply Required","Applied"])
    docs.document["tabs"][0]["childTabs"][0]["tabProperties"]["parentTabId"]=prop(
        q.queue_tab(q.snapshot(),"Manual Apply Required"),"tabId")
    idx=WorkIndex(tmp_path/"idx.db")
    o=WorkloadOrchestrator(q,engine(),idx,lambda *_:None,
                           identity=lambda item:("Example","Engineer"))
    result=manual_transition(o,"Manual Apply Required","item-1","Applied",reason="Submitted by human")
    assert result["queue"]=="Applied"
    assert q.application_events("item-1")[0]["processor"]=="manual_update"

def test_calendar_and_email_idempotency(tmp_path):
    from gdtq.observers import CalendarObserver,EmailCorrespondenceObserver,ResearchAgent
    idx=WorkIndex(tmp_path/"idx.db")
    apps=lambda:[{"application_id":"app-1","company":"Acme","role":"Backend","status":"Applied"}]
    results=[]
    mails=lambda:[{"message_id":"mail-1","sender":"Acme recruiter","subject":"Schedule interview","body":"Please schedule an interview"}]
    email=EmailCorrespondenceObserver(mails,apps,lambda *x:results.append(x),index=idx)
    assert len(email.scan())==1
    assert len(email.scan())==0
    meetings=lambda:[{"event_id":"event-1","meeting_id":"meet-1","title":"Acme interview","start":"2026-11-01T09:00:00Z"}]
    research=ResearchAgent(lambda _:[],lambda c:"Company interview research",
                           lambda item,title,body:"research-tab-1")
    calendar=CalendarObserver(meetings,apps,research,lambda *x:results.append(x),idx)
    assert len(calendar.scan())==1
    assert len(calendar.scan())==0
    assert len(results)==2

def test_utc_scheduler_respects_israel_local_report_hour(tmp_path):
    from gdtq.observers import Scheduler
    idx=WorkIndex(tmp_path/"idx.db")
    class FakeOrchestrator:
        def dispatch_tick(self):return None
    reports=[]
    scheduler=Scheduler(idx,FakeOrchestrator(),report=lambda:reports.append(True),
                        report_timezone="Asia/Jerusalem",report_hour=8)
    assert "report" not in scheduler.tick(datetime(2026,10,27,4,0,tzinfo=timezone.utc))
    assert "report" in scheduler.tick(datetime(2026,10,27,6,0,tzinfo=timezone.utc))
    assert "report" not in scheduler.tick(datetime(2026,10,27,6,1,tzinfo=timezone.utc))
    assert len(reports)==1

def test_gmail_sender_drafts_but_will_not_send_without_policy():
    from gdtq.gmail import GmailActions
    import io
    sent=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"id":"draft-1"}'
    def opener(request,timeout=None):
        sent.append((request.full_url,request.data))
        return Response()
    client=GmailActions("fake-token",allow_send=False,opener=opener)
    with pytest.raises(PermissionError):
        client.compose("recruiter@example.com","Re: Interview","My Calendly link",send=True)
    assert client.compose("recruiter@example.com","Re: Interview","My Calendly link")=={"id":"draft-1"}
    assert len(sent)==1 and sent[0][0].endswith("/drafts")
    with pytest.raises(ValueError):client.compose("bad\nrecipient@example.com","x","y")

def test_posting_reader_persists_artifact_and_event_reference(tmp_path):
    index=WorkIndex(tmp_path/"idx.db")
    store=FakeArtifactStore()
    item={"item_id":"i1","company":"Acme","role":"Engineer",
          "posting_url":"https://example.com/position/1"}
    details={"description":"Java Python Kubernetes "*40}
    output=bookmark_discovered(item,lambda url:details,index,store=store)
    assert output["code"]=="BOOKMARKED"
    artifact_path=output["data"]["posting_artifact"]
    assert artifact_path in store.files
    saved=json.loads(store.files[artifact_path])
    assert saved["application_id"]=="i1"

def test_duplicate_directory_does_not_replace_first_application(tmp_path):
    index=WorkIndex(tmp_path/"idx.db")
    a=index.reserve_identity("i1","Acme","Backend","https://one.example/jobs/1")
    b=index.reserve_identity("i2","Acme","Backend","https://two.example/jobs/2")
    assert a is None and b=="i1"

def test_terminal_archive_replay_does_not_recreate_or_delete_again(tmp_path):
    q=GDTQ(FakeDocs());idx=WorkIndex(tmp_path/"idx.db");store=FakeArtifactStore()
    publisher=CompletionPublisher(q,store,idx)
    first=publisher.complete("item-1","Acme","Backend","Rejected","declined")
    second=publisher.complete("item-1","Acme","Backend","Rejected","declined")
    assert second["replayed"] and second["archive_path"]==first["archive_path"]

def test_legacy_tabs_do_not_expire_as_year_one(tmp_path):
    from gdtq.retention import RetentionManager
    q=GDTQ(FakeDocs());idx=WorkIndex(tmp_path/"idx.db")
    store=FakeArtifactStore();publisher=CompletionPublisher(q,store,idx)
    manager=RetentionManager(q,publisher,manual_limit=20)
    q.ensure_state_tabs(["Applied","Interviewing","Manual Apply Required"])
    q.client.document["tabs"][0]["childTabs"][0]["tabProperties"]["parentTabId"] = "q-discovered"
    assert manager.sweep(lambda item:("Acme","Engineer"))==[]
