"""Real orchestration and storage; fixed stage outcomes, no model/browser actions."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from ajss.worker.dispatch import WorkloadOrchestrator
from ajss.queue.policy import StateTransitionEngine
from ajss.queue import prop, find_tab, events_from_tab, QueueConflict


def payload(item_id,agent,code,attempt_id):
    return {"item_id":item_id,"agent":agent,"code":code,"execution_status":"SUCCESS",
            "summary":"Deterministic integration fixture","data":{},"attempt_id":attempt_id}


@pytest.mark.parametrize("flight_mode", ["database","document"])
def test_real_document_and_database_pipeline(live_resources,flight_mode):
    r=live_resources
    q,index=r.queue,r.index
    if flight_mode=="database":q.flight_index=index
    item_id=r.create_item()
    company="Fixture "+r.namespace
    url="https://jobs.example.test/"+r.namespace
    assert index.reserve_identity(item_id,company,"Engineer",url) is None
    other=r.namespace+"-duplicate"
    r.item_ids.append(other)
    assert index.reserve_identity(other,"","",url+"?utm_source=fixture")==item_id
    assert index.reserve_identity(other,company.upper()," Engineer ","https://jobs.example.test/other")==item_id
    engine=StateTransitionEngine()
    stages=[("Discovered","bookmark_discovered","BOOKMARKED","Bookmarked"),
            ("Bookmarked","validate_bookmarked","MATCH","Validated"),
            ("Validated","prepare_validated","PREPARED","Prepared"),
            ("Prepared","submit_prepared","SUBMITTED","Applied")]
    for source,agent,code,destination in stages:
        dispatched=[]
        orchestrator=WorkloadOrchestrator(q,engine,index,
            lambda state,claim:dispatched.append((state,claim)),queues=(source,))
        claim=orchestrator.dispatch_tick()
        assert claim.item_id==item_id
        assert dispatched==[(source,claim)]
        assert index.current_claim(item_id)=={"source_state":source,"attempt_id":claim.attempt_id}
        assert not index.claim(item_id,source,"competing-attempt")
        assert orchestrator.dispatch_tick() is None
        expected=source if flight_mode=="database" else source+"-onFlight"
        doc=q.snapshot()
        assert prop(find_tab(doc,item_id),"parentTabId")==prop(q.queue_tab(doc,expected),"tabId")
        if flight_mode=="database":
            with pytest.raises(QueueConflict):
                orchestrator.handle_outcome(source,payload(item_id,agent,code,"stale-attempt"))
        result=orchestrator.handle_outcome(source,payload(item_id,agent,code,claim.attempt_id))
        assert result["queue"]==destination
        assert index.current_claim(item_id)["attempt_id"] is None
        doc=q.snapshot()
        assert prop(find_tab(doc,item_id),"parentTabId")==prop(q.queue_tab(doc,destination),"tabId")
        stored=index.events(item_id)
        assert any(e["event_id"]==result["event_id"] and e["current_state"]==destination for e in stored)
    doc_events=events_from_tab(find_tab(q.snapshot(),item_id))
    db_events=index.events(item_id)
    assert len(doc_events)==len(db_events)==4
    assert {e["event_id"] for e in doc_events}=={e["event_id"] for e in db_events}
    # Database event replay is idempotent even across new connections.
    index.append_event(db_events[-1])
    assert len(index.events(item_id))==4


def test_real_retry_stays_in_state_and_is_not_immediately_dispatched(live_resources):
    r=live_resources
    r.queue.flight_index=r.index
    item_id=r.create_item()
    orch=WorkloadOrchestrator(r.queue,StateTransitionEngine(),r.index,lambda *_:None,queues=("Discovered",))
    claim=orch.dispatch_tick()
    result=payload(item_id,"bookmark_discovered","ERROR",claim.attempt_id)
    result["execution_status"]="RETRYABLE_FAILURE"
    orch.handle_outcome("Discovered",result)
    assert r.index.current_claim(item_id)["attempt_id"] is None
    assert r.index.events(item_id)[0]["outcome"]["data"]["retry_count"]==1
    assert orch.dispatch_tick() is None


def test_real_concurrent_identity_reservation_has_one_owner(live_resources):
    r=live_resources
    candidates=[r.namespace+"-a",r.namespace+"-b"]
    r.item_ids.extend(candidates)
    url="https://jobs.example.test/"+r.namespace
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda item:r.index.reserve_identity(item,"","",url),candidates))
    assert results.count(None)==1
    winner=candidates[results.index(None)]
    assert next(value for value in results if value is not None)==winner
