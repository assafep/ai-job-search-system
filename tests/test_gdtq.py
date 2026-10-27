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
