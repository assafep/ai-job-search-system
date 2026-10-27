"""Composition root and single-process worker runner.

The caller injects AI/browser/Gmail/Calendar providers. A deployed process
rather than a ChatGPT conversation performs polling. No network calls occur
until start() is invoked with configured credentials.
"""
from __future__ import annotations
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from .contracts import Event
from .dispatch import WorkloadOrchestrator
from .queue import GDTQ,children,find_tab,prop,QueueConflict
from .policy import StateTransitionEngine
from .store import WorkIndex
from .downstream import CompletionPublisher
from .retention import RetentionManager
from .observers import Scheduler

class ApplicationContextLoader:
    def __init__(self,queue,artifacts,profile_path="Applicant/applicant_profile.json"):
        self.queue,self.artifacts,self.profile_path=queue,artifacts,profile_path

    def identity(self,item_id):
        from adapters.google.docs import application_tab_title
        from adapters.google.journal import parse_application_title
        item=find_tab(self.queue.snapshot(),item_id)
        if item is None: raise QueueConflict("Cannot resolve identity for "+item_id)
        return parse_application_title(prop(item,"title"))

    def application(self,item_id):
        from adapters.google.docs import tab_paragraphs,first_posting_url
        from adapters.google.journal import parse_application_title,parse_events
        item=find_tab(self.queue.snapshot(),item_id)
        if not item:raise QueueConflict("No application "+item_id)
        company,role=parse_application_title(prop(item,"title"))
        paragraphs=tab_paragraphs(item)
        legacy=parse_events(paragraphs,item_id)
        url=first_posting_url(legacy,paragraphs)
        return {"item_id":item_id,"company":company,"role":role,"posting_url":url,
                "events":self.queue.application_events(item_id),
                "tab_url":f"https://docs.google.com/document/d/{self.queue.client.document_id}/edit?tab={item_id}"}

    def profile(self):
        return json.loads(self.artifacts.read_path(self.profile_path)["content"])

    def posting(self,application):
        from adapters.job_boards.posting_details import posting_artifact_name,parse_saved_posting
        from adapters.google.drive import application_folder_name
        reference=None
        for event in reversed(application["events"]):
            reference=((event.get("outcome") or {}).get("data") or {}).get("posting_artifact")
            if reference:break
        if not reference:
            reference="Applications/"+application_folder_name(application["company"],application["role"])+"/"+posting_artifact_name(
                application["company"],application["role"],application["posting_url"])
        data=parse_saved_posting(self.artifacts.read_path(reference)["content"])
        return data | {"posting_artifact":reference}

def build_google_runtime(document_id,drive_root_url,db_path,*,max_tabs=100,
                         tab_soft_limit=90,manual_limit=20):
    """Use existing Google OAuth and Drive adapters; credential provision is external."""
    from adapters.google.auth import token_from_environment
    from adapters.google.docs import GoogleDocsClient
    from adapters.google.drive import GoogleDriveArtifactStore
    token=token_from_environment()
    if token is None: raise RuntimeError("Google OAuth credentials not configured")
    queue=GDTQ(GoogleDocsClient(token,document_id),max_tabs=max_tabs)
    store=GoogleDriveArtifactStore(token,drive_root_url)
    Path(db_path).parent.mkdir(parents=True,exist_ok=True)
    index=WorkIndex(db_path)
    engine=StateTransitionEngine()
    publisher=CompletionPublisher(queue,store,index)
    loader=ApplicationContextLoader(queue,store)
    retention=RetentionManager(queue,publisher,manual_limit=manual_limit,tab_soft_limit=tab_soft_limit)
    return {"queue":queue,"store":store,"index":index,"engine":engine,
            "publisher":publisher,"loader":loader,"retention":retention}

class AsyncAgentDispatcher:
    """Bounded workers; one submitted callback owns each claimed item."""
    def __init__(self,workers,*,max_workers=4,max_pending=16):
        self.workers=dict(workers)
        self.pool=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix="gdtq")
        self.slots=threading.BoundedSemaphore(max_pending)
        self.orchestrator=None
        self.identity=None
        self.errors=[]

    def __call__(self,state,claimed):
        worker=self.workers.get(state)
        if worker is None:raise ValueError("Missing agent handler for queue "+state)
        if not self.slots.acquire(blocking=False):
            raise RuntimeError("Agent dispatcher is saturated; item remains in flight")
        def execute():
            try:
                payload=worker(claimed)
                if not isinstance(payload,dict):raise TypeError("Stage must return dict outcome")
                payload=dict(payload,attempt_id=claimed.attempt_id)
                self.orchestrator.handle_outcome(state,payload,self.identity)
            except Exception as exc:
                # Do NOT fabricate an agent outcome; preserve in-flight item
                # and make the failure visible for reconciliation.
                self.errors.append({"item_id":claimed.item_id,"error":repr(exc)})
            finally:
                self.slots.release()
        try:self.pool.submit(execute)
        except BaseException:
            self.slots.release()
            raise

    def close(self):
        self.pool.shutdown(wait=True)

def wire_orchestrator(runtime,handlers,queues=("Discovered","Bookmarked","Validated","Prepared"),max_workers=4):
    missing=set(queues)-set(handlers)
    if missing:raise ValueError("Missing worker handlers: "+", ".join(sorted(missing)))
    dispatcher=AsyncAgentDispatcher(handlers,max_workers=max_workers)
    identity=runtime["loader"].identity
    orchestrator=WorkloadOrchestrator(runtime["queue"],runtime["engine"],runtime["index"],
            dispatcher,runtime["publisher"],runtime["retention"],queues,identity)
    dispatcher.orchestrator=orchestrator
    dispatcher.identity=identity
    return orchestrator,dispatcher

def recover_orphaned_inflight(runtime,queues,*,exclusive_instance=False):
    """Operator recovery after ensuring there is no other active AJSS runner.

    Requeues orphaned in-flight items using explicit recovery events, preserving
    history. Do NOT use during concurrent processing or with another replica.
    """
    if not exclusive_instance:raise ValueError("Requires proven exclusive instance ownership")
    q=runtime["queue"];index=runtime["index"]
    results=[]
    for state in queues:
        doc=q.snapshot()
        try:flight=q.queue_tab(doc,state+"-onFlight")
        except QueueConflict:continue
        for tab in children(doc,prop(flight,"tabId")):
            item_id=prop(tab,"tabId")
            event=Event(item_id=item_id,event_type="RECOVERY",
                        previous_state=state,current_state=state,processor="reconciler",
                        outcome={"reason":"exclusive startup recovery"}).as_dict()
            q.push(state,state,item_id,event)
            index.append_event(event)
            results.append(item_id)
    return results

def run_forever(scheduler,*,tick_seconds=1.0,stop_event=None):
    """Single-process scheduler. Use systemd/Docker and exclusive leader election."""
    stop=stop_event or threading.Event()
    while not stop.is_set():
        scheduler.tick()
        stop.wait(tick_seconds)
