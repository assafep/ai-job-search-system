"""GDTQ backed by Google Docs tabs. All writes require a document revision.

Pop moves A -> A-onFlight. Push moves A-onFlight -> B, appending an event
in the SAME Docs batchUpdate request. No destination in-flight hop exists.
"""
from __future__ import annotations
import json
from uuid import uuid4
from .contracts import Claimed

EVENT_PREFIX = "AJSS_EVENT:"
class QueueConflict(RuntimeError): pass
class QueueFull(RuntimeError): pass

def all_tabs(tabs):
    for tab in tabs:
        yield tab
        yield from all_tabs(tab.get("childTabs") or [])

def prop(tab, key):
    return (tab.get("tabProperties") or {}).get(key)

def children(document, parent_id):
    return sorted((t for t in all_tabs(document.get("tabs") or [])
                   if prop(t,"parentTabId")==parent_id), key=lambda t: prop(t,"index") or 0)

def find_tab(document, tab_id):
    return next((t for t in all_tabs(document.get("tabs") or []) if prop(t,"tabId")==tab_id), None)

def flatten_text(tab):
    output = []
    for item in ((tab.get("documentTab") or {}).get("body") or {}).get("content",[]):
        for el in (item.get("paragraph") or {}).get("elements",[]):
            output.append(((el.get("textRun") or {}).get("content") or ""))
    return "".join(output)

def events_from_tab(tab):
    result=[]
    for line in flatten_text(tab).splitlines():
        if line.startswith(EVENT_PREFIX):
            try: result.append(json.loads(line[len(EVENT_PREFIX):]))
            except (ValueError, TypeError): raise QueueConflict("Malformed event JSON on tab " + str(prop(tab,"tabId")))
    return result

class GDTQ:
    def __init__(self, client, max_tabs=95):
        """client is application_service.google_docs_repository.GoogleDocsClient."""
        self.client=client
        self.max_tabs=max_tabs

    def snapshot(self):
        if hasattr(self.client,"invalidate_document"):
            self.client.invalidate_document()
        return self.client.get_document()

    def queue_tab(self, doc, queue_name):
        roots=[t for t in doc.get("tabs",[]) if prop(t,"title")==queue_name]
        if len(roots)!=1: raise QueueConflict(f"Expected exactly one root tab named {queue_name}: found {len(roots)}")
        return roots[0]

    def ensure_onflight(self, queue_name):
        doc=self.snapshot()
        candidates=[t for t in doc.get("tabs",[]) if prop(t,"title")==queue_name+"-onFlight"]
        if len(candidates)>1: raise QueueConflict("Ambiguous onFlight queue: "+queue_name)
        if candidates: return prop(candidates[0],"tabId")
        self.client.batch_update([{"addDocumentTab":{"tabProperties":{"title":queue_name+"-onFlight"}}}],doc.get("revisionId"))
        return prop(self.queue_tab(self.snapshot(),queue_name+"-onFlight"),"tabId")

    def size(self, queue_name):
        doc=self.snapshot()
        return len(children(doc,prop(self.queue_tab(doc,queue_name),"tabId")))

    def total_tabs(self, doc=None):
        return sum(1 for _ in all_tabs((doc or self.snapshot()).get("tabs",[])))

    def pop(self, queue_name, priority=False):
        onflight=self.ensure_onflight(queue_name)
        doc=self.snapshot()
        source=prop(self.queue_tab(doc,queue_name),"tabId")
        items=children(doc,source)
        if not items: return None
        if priority:
            def score(tab):
                evs=events_from_tab(tab)
                return (evs[-1].get("outcome") or {}).get("data",{}).get("score",0) if evs else 0
            items.sort(key=lambda x:-float(score(x)))
        tab=items[0]
        item_id=prop(tab,"tabId")
        events=events_from_tab(tab)
        attempt_id=str(uuid4())
        self.client.batch_update([{"updateDocumentTabProperties":{
            "tabProperties":{"tabId":item_id,"parentTabId":onflight},
            "fields":"parentTabId"
        }}],doc.get("revisionId"))
        return Claimed(item_id,queue_name,events[-1] if events else None,self.client.document_id,
                       f"https://docs.google.com/document/d/{self.client.document_id}/edit?tab={item_id}",attempt_id)

    def push(self, source_queue, destination_queue, item_id, event: dict):
        doc=self.snapshot()
        source_flight=prop(self.queue_tab(doc,source_queue+"-onFlight"),"tabId")
        dest=prop(self.queue_tab(doc,destination_queue),"tabId")
        item=find_tab(doc,item_id)
        if item is None or prop(item,"parentTabId")!=source_flight:
            raise QueueConflict("Item is not in expected source onFlight queue")
        if event.get("item_id")!=item_id or event.get("previous_state")!=source_queue or event.get("current_state")!=destination_queue:
            raise QueueConflict("Event state or item mismatch")
        prior=events_from_tab(item)
        if any(e.get("event_id")==event.get("event_id") for e in prior):
            raise QueueConflict("Event already exists while item remains in flight")
        if self.total_tabs(doc)>=self.max_tabs and destination_queue!=source_queue:
            # Reparenting does not add a tab; this is only a conservative guard
            # for planned additional tabs/artifacts, not a Google API limitation.
            pass
        line="\n"+EVENT_PREFIX+json.dumps(event,sort_keys=True,ensure_ascii=False)+"\n"
        self.client.batch_update([
          {"insertText":{"endOfSegmentLocation":{"tabId":item_id},"text":line}},
          {"updateDocumentTabProperties":{"tabProperties":{"tabId":item_id,"parentTabId":dest},"fields":"parentTabId"}}
        ],doc.get("revisionId"))
        return {"item_id":item_id,"queue":destination_queue,"event_id":event["event_id"]}

    def append_event(self,item_id,event:dict):
        doc=self.snapshot()
        item=find_tab(doc,item_id)
        if item is None: raise QueueConflict("Unknown tab")
        self.client.batch_update([{"insertText":{"endOfSegmentLocation":{"tabId":item_id},"text":
           "\n"+EVENT_PREFIX+json.dumps(event,sort_keys=True,ensure_ascii=False)+"\n"}}],doc.get("revisionId"))

    def application_events(self,item_id):
        tab=find_tab(self.snapshot(),item_id)
        if tab is None: raise QueueConflict("Unknown application")
        return events_from_tab(tab)

    def create_research_tab(self,item_id,title,content):
        doc=self.snapshot()
        if not find_tab(doc,item_id): raise QueueConflict("Unknown application")
        if self.total_tabs(doc)>=self.max_tabs: raise QueueFull("Document tab capacity budget exhausted")
        response=self.client.batch_update([{"addDocumentTab":{"tabProperties":{"title":title[:50],"parentTabId":item_id}}}],doc.get("revisionId"))
        tabid=((response.get("replies") or [{}])[0].get("addDocumentTab") or {}).get("tabProperties",{}).get("tabId")
        if not tabid: raise QueueConflict("Cannot identify created research tab")
        self.client.batch_update([{"insertText":{"endOfSegmentLocation":{"tabId":tabid},"text":content}}],self.snapshot().get("revisionId"))
        return tabid

    def delete_application(self,item_id):
        doc=self.snapshot()
        if not find_tab(doc,item_id): return False
        self.client.batch_update([{"deleteTab":{"tabId":item_id}}],doc.get("revisionId"))
        return True
