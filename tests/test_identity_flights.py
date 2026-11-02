"""Offline regression coverage for identity and database flight ownership."""
from datetime import datetime, timezone, timedelta
import pytest
from ajss.queue.identity import canonical_legacy_url, historical_keys
from ajss.queue.store import WorkIndex
from ajss.queue import GDTQ, QueueConflict, prop, find_tab
from ajss.queue.contracts import Event
from ajss.worker.effects import apply_effects
from ajss.worker.stages import bookmark_discovered
from test_gdtq import FakeArtifactStore, FakeDocs


def test_normalization_preserves_job_parameters_and_case_sensitive_path():
    assert canonical_legacy_url(" HTTPS://Jobs.Example/Role/?id=3&fbclid=x&empty=#section ")=="https://jobs.example/Role?id=3&empty="
    assert historical_keys(" ACME  Ltd ","Senior\nEngineer")==[("company_role","acme ltd / senior engineer")]
    assert canonical_legacy_url("https://jobs.example/Role?id=4")!=canonical_legacy_url("https://jobs.example/Role?id=3")


def test_old_sqlite_identity_remains_visible(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    index.db.execute("INSERT INTO identities VALUES(?,?,?)",("company_role:acme|engineer","old","today"))
    assert index.reserve_identity("new","Acme","Engineer")=="old"


def test_duplicate_found_after_reading_url_only(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    index.reserve_identity("old","Acme","Engineer","https://example.com/old")
    result=bookmark_discovered({"item_id":"new","posting_url":"https://example.com/new"},
        lambda url:{"company":"Acme","role":"Engineer","description":"Posting "*100},index)
    assert result["code"]=="DUPLICATE"
    assert result["data"]["original_item_id"]=="old"


def test_concurrent_bookmark_workers_leave_writes_to_the_orchestrator(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    store=FakeArtifactStore()
    details={"company":"Acme","role":"Engineer","description":"Posting "*100}
    def propose(item_id,url):
        return bookmark_discovered(
            {"item_id":item_id,"company":"Acme","role":"Engineer","posting_url":url},
            lambda _url:details,index,store=store)
    first=propose("a","https://example.com/job")
    second=propose("b","https://example.com/job?utm_source=mail")
    assert store.files=={}
    assert index.lookup_identity("Acme","Engineer","https://example.com/job") is None
    assert apply_effects(first,{"index":index,"store":store})["code"]=="BOOKMARKED"
    duplicate=apply_effects(second,{"index":index,"store":store})
    assert duplicate["code"]=="DUPLICATE"
    assert duplicate["data"]["original_item_id"]=="a"
    assert len(store.files)==1
    assert index.lookup_identity("Acme","Engineer","https://example.com/job?utm_source=mail")=="a"
    index.close()


def test_url_duplicate_skips_reader(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    index.reserve_identity("old","Acme","Engineer","https://example.com/job")
    def reader(url):raise AssertionError("Duplicate must not fetch")
    result=bookmark_discovered({"item_id":"new","posting_url":"https://example.com/job?utm_source=mail"},reader,index)
    assert result["code"]=="DUPLICATE"


def test_database_flight_keeps_document_stable_and_rejects_stale_outcome(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    queue=GDTQ(FakeDocs(),flight_index=index)
    claimed=queue.pop("Discovered")
    assert prop(find_tab(queue.snapshot(),"item-1"),"parentTabId")=="q-discovered"
    assert queue.pop("Discovered") is None
    assert not any(prop(t,"title").endswith("-onFlight") for t in queue.snapshot()["tabs"])
    event=Event("item-1","DONE","Discovered","Bookmarked","bookmark_discovered",attempt_id="stale").as_dict()
    with pytest.raises(QueueConflict):queue.push("Discovered","Bookmarked","item-1",event)
    event["attempt_id"]=claimed.attempt_id
    queue.push("Discovered","Bookmarked","item-1",event)
    assert queue.push("Discovered","Bookmarked","item-1",event)["replayed"]
    assert queue.pop("Bookmarked") is None
    index.release("item-1",claimed.attempt_id)
    assert queue.pop("Bookmarked") is not None


def test_expired_submission_claim_is_not_reassigned(tmp_path):
    index=WorkIndex(tmp_path/"index.db")
    assert index.claim("item","Prepared","original",lease_seconds=-1)
    assert not index.claim("item","Prepared","replacement")
    assert index.expired_claims()[0]["attempt_id"]=="original"


def test_restored_agents_enforce_schema():
    from ajss.documents.prompts import run, AgentContractError
    output=run("correspondence",{},lambda prompt,context:{"intent":"rejection","proposed_action":"record"})
    assert output["intent"]=="rejection"
    for name in ("read-posting","validate","applicant-profile","correspondence"):
        with pytest.raises(AgentContractError):
            run(name,{},lambda prompt,context:{})


def test_postgres_checks_historical_index_before_reserving():
    from ajss.queue.postgres import PostgresWorkIndex
    class Connection:
        def __init__(self):self.calls=[];self.row=None
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def close(self):pass
        def cursor(self):return self
        def execute(self,sql,params):
            self.calls.append((sql,params))
            self.row=("historical",) if "FROM public.posting_keys" in sql else None
        def fetchone(self):return self.row
    conn=Connection()
    index=PostgresWorkIndex(connect=lambda:conn)
    assert index.reserve_identity("new","","","https://example.com/job?utm_source=x")=="historical"
    assert not any("INSERT INTO" in sql for sql,_ in conn.calls)
    assert any(params==("url","https://example.com/job") for _,params in conn.calls)
