"""Stateless GDTQ service operations used by the remote HTTP/MCP gateway.

Read requests open short-lived PostgreSQL connections. Dispatch requires a
separate deployment gate, Google credentials, and a database advisory lock.
No implicit application submission or outgoing mail is permitted.
"""
from __future__ import annotations
import os
from contextlib import contextmanager
from .postgres_index import PostgresWorkIndex,historical_keys
from .policy import StateTransitionEngine

LOCK_KEY=56944218331217

def _dsn():
    result=os.environ.get("DATABASE_URL") or os.environ.get("AJSS_DATABASE_URL")
    if not result:raise RuntimeError("DATABASE_URL is not configured")
    return result

def _index(index_factory=None):
    return index_factory() if index_factory is not None else PostgresWorkIndex(_dsn())

def identity_lookup(company="",role="",url="",source_id="",*,index_factory=None):
    """Read-only exact lookup using the same normalization as reserve_identity."""
    keys=historical_keys(company,role,url,source_id)
    if not keys:raise ValueError("Provide posting URL or company and role or source ID")
    index=_index(index_factory)
    try:
        matches=[]
        with index._tx() as conn:
            with conn.cursor() as cur:
                for kind,key in keys:
                    cur.execute("""SELECT application_id,origin FROM public.gdtq_identities
                                   WHERE key_type=%s AND lookup_key=%s""",(kind,key))
                    row=cur.fetchone()
                    if row:matches.append({"key_type":kind,"application_id":row[0],"origin":row[1]})
        return {"duplicate":bool(matches),"matches":matches,
                "conflicting_identity":len({m["application_id"] for m in matches})>1}
    finally:index.close()

def database_status(*,index_factory=None):
    """Aggregates only; avoid returning applicant identity or posting URL."""
    index=_index(index_factory)
    try:
        with index._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT
                  (SELECT count(*) FROM public.gdtq_identities) AS identities,
                  (SELECT count(*) FROM public.gdtq_items WHERE attempt_id IS NOT NULL
                     AND lease_until>now()) AS active_leases,
                  (SELECT count(*) FROM public.gdtq_event_log) AS events,
                  (SELECT count(*) FROM public.gdtq_checkpoints) AS checkpoints""")
                row=cur.fetchone()
        return dict(zip(("identities","active_leases","events","checkpoints"),row))
    finally:index.close()

def runtime_queue():
    """Google credentials and identifiers come only from trusted environment."""
    from .runtime import build_google_runtime
    from .cli import doc_id
    document=os.environ.get("AJSS_DOCUMENT_ID")
    url=os.environ.get("AJSS_DOCUMENT_URL")
    drive=os.environ.get("AJSS_DRIVE_ROOT_URL")
    if not document and url:document=doc_id(url)
    if not document or not drive:
        raise RuntimeError("AJSS_DOCUMENT_ID (or URL) and AJSS_DRIVE_ROOT_URL are required")
    return build_google_runtime(document,drive,db_path="/tmp/unused.sqlite3")

def queue_summary(*,runtime_factory=None):
    factory=runtime_factory or runtime_queue
    resources=factory()
    try:
        queue=resources["queue"]
        doc=queue.snapshot()
        from .queue import children,prop
        result={}
        for root in doc.get("tabs",[]):
            title=prop(root,"title")
            if not title:continue
            result[title]=len(children(doc,prop(root,"tabId")))
        return {"document_id":queue.client.document_id,"tab_count":queue.total_tabs(doc),
                "queue_sizes":result}
    finally:resources["index"].close()

def application_events(item_id,*,runtime_factory=None):
    if not item_id or len(item_id)>200:raise ValueError("Invalid application tab ID")
    resources=(runtime_factory or runtime_queue)()
    try:return {"item_id":item_id,"events":resources["queue"].application_events(item_id)}
    finally:resources["index"].close()

@contextmanager
def dispatch_lock(dsn):
    """Hold a Postgres session advisory lock across ONE synchronous invocation.

    Allows at most one dispatch request against a single configured database.
    Both the cron and MCP endpoints share this lock. Lease remains separate.
    """
    import psycopg
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_KEY,))
            if not cur.fetchone()[0]:raise RuntimeError("Another dispatch invocation is active")
            try:yield
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_KEY,))

def dispatch_once(*,runtime_factory=None,handler_factory=None,writer_factory=None,
                  lock_factory=None):
    """Process at most one queued application synchronously; NEVER fork background work.

    Submission strategies intentionally remain disabled. To enable browser
    submission requires separate authorization, implementation and testing.
    """
    if os.environ.get("AJSS_ENABLE_DISPATCH")!="1":
        raise PermissionError("Dispatch disabled until live Google integration is approved")
    dsn=_dsn()
    lock=lock_factory(dsn) if lock_factory else dispatch_lock(dsn)
    with lock:
        runtime=(runtime_factory or runtime_queue)()
        try:
            from .dispatch import WorkloadOrchestrator
            from .handlers import build_handlers
            resume=os.environ.get("AJSS_RESUME_DRIVE_PATH")
            writer=(writer_factory or (lambda: None))()
            handlers=(handler_factory or build_handlers)(
                runtime,writer=writer,resume_path=resume,
                submission_strategies=(),
                match_threshold=float(os.environ.get("AJSS_SKILL_THRESHOLD","0")))
            completed=[]
            def handle(state,claimed):
                try:
                    result=handlers[state](claimed)
                    result=dict(result,attempt_id=claimed.attempt_id)
                    completed.append(runtime_orch.handle_outcome(
                        state,result,identity=runtime["loader"].identity))
                except Exception:
                    # Preserve the onFlight tab for explicit recovery.
                    raise
            runtime_orch=WorkloadOrchestrator(
                runtime["queue"],runtime["engine"],runtime["index"],handle,
                publisher=runtime["publisher"],retention=runtime["retention"],
                identity=runtime["loader"].identity)
            claimed=runtime_orch.dispatch_tick()
            return {"processed":bool(claimed),
                    "source_queue":claimed.source_state if claimed else None,
                    "item_id":claimed.item_id if claimed else None,
                    "completion":completed[0] if completed else None}
        finally:runtime["index"].close()

def check_policy():
    e=StateTransitionEngine()
    return {"rules_version":e.version,"states":len(e.transitions),"agents":len(e.routes)}
