"""Google Doc Tab Queue. Pop: A -> A-onFlight. Push: A-onFlight -> B.

All queue moves use Google Docs document revision checks. Event append and
reparent happen in a single batchUpdate; retries reconcile by event ID.
"""
import json
from uuid import uuid4
from .contracts import Claimed

EVENT_PREFIX="AJSS_EVENT:"
class QueueConflict(RuntimeError): pass
class QueueFull(RuntimeError): pass

def all_tabs(tabs):
    for tab in tabs:
        yield tab
        yield from all_tabs(tab.get("childTabs") or [])

def prop(tab,key):
    return (tab.get("tabProperties") or {}).get(key)

def children(document,parent_id):
    return sorted((t for t in all_tabs(document.get("tabs") or [])
                   if prop(t,"parentTabId")==parent_id),key=lambda t:prop(t,"index") or 0)

def find_tab(document,tab_id):
    return next((t for t in all_tabs(document.get("tabs") or []) if prop(t,"tabId")==tab_id),None)

def flatten_text(tab):
    output=[]
    for item in ((tab.get("documentTab") or {}).get("body") or {}).get("content",[]):
        for element in (item.get("paragraph") or {}).get("elements",[]):
            output.append(((element.get("textRun") or {}).get("content") or ""))
    return "".join(output)

def events_from_tab(tab):
    result=[]
    for line in flatten_text(tab).splitlines():
        if line.startswith(EVENT_PREFIX):
            try: result.append(json.loads(line[len(EVENT_PREFIX):]))
            except (ValueError,TypeError) as exc:
                raise QueueConflict("Invalid event JSON in "+str(prop(tab,"tabId"))) from exc
    return result

class GDTQ:
    def __init__(self,client,max_tabs=100):
        self.client=client
        self.max_tabs=max_tabs

    def snapshot(self):
        if hasattr(self.client,"invalidate_document"): self.client.invalidate_document()
        return self.client.get_document()

    def queue_tab(self,doc,name):
        roots=[t for t in doc.get("tabs",[]) if prop(t,"title")==name and not prop(t,"parentTabId")]
        if len(roots)!=1: raise QueueConflict(f"Expected exactly one root tab named {name}; found {len(roots)}")
        return roots[0]

    def ensure_onflight(self,name):
        doc=self.snapshot()
        matches=[t for t in doc.get("tabs",[]) if prop(t,"title")==name+"-onFlight" and not prop(t,"parentTabId")]
        if len(matches)>1: raise QueueConflict("Ambiguous in-flight queue "+name)
        if matches:return prop(matches[0],"tabId")
        if self.total_tabs(doc)>=self.max_tabs: raise QueueFull("Cannot create in-flight tab")
        self.client.batch_update([{"addDocumentTab":{"tabProperties":{"title":name+"-onFlight"}}}],doc.get("revisionId"))
        return prop(self.queue_tab(self.snapshot(),name+"-onFlight"),"tabId")

    def total_tabs(self,doc=None):
        return sum(1 for _ in all_tabs((doc or self.snapshot()).get("tabs") or []))

    def ensure_state_tabs(self,names):
        """Explicit provisioning; call before activating the scheduler."""
        result=[]
        for name in names:
            doc=self.snapshot()
            if any(prop(t,"title")==name for t in doc.get("tabs",[])):
                self.queue_tab(doc,name)
                continue
            if self.total_tabs(doc)>=self.max_tabs:
                raise QueueFull("Cannot provision state tab "+name)
            self.client.batch_update([{"addDocumentTab":{"tabProperties":{"title":name}}}],doc.get("revisionId"))
            result.append(name)
        return result

    def size(self,name):
        doc=self.snapshot()
        return len(children(doc,prop(self.queue_tab(doc,name),"tabId")))

    def claim_item(self,source_queue,item_id):
        onflight=self.ensure_onflight(source_queue)
        doc=self.snapshot()
        queue_id=prop(self.queue_tab(doc,source_queue),"tabId")
        item=find_tab(doc,item_id)
        if not item or prop(item,"parentTabId")!=queue_id:
            raise QueueConflict("Item is not waiting in expected source queue")
        old=events_from_tab(item)
        attempt=str(uuid4())
        self.client.batch_update([{"updateDocumentTabProperties":{
            "tabProperties":{"tabId":item_id,"parentTabId":onflight},
            "fields":"parentTabId"}}],doc.get("revisionId"))
        return Claimed(item_id,source_queue,old[-1] if old else None,self.client.document_id,
                       f"https://docs.google.com/document/d/{self.client.document_id}/edit?tab={item_id}",attempt)

    def pop(self,source_queue,priority=False):
        doc=self.snapshot()
        queue_id=prop(self.queue_tab(doc,source_queue),"tabId")
        items=children(doc,queue_id)
        if not items:return None
        from datetime import datetime,timezone
        def eligible(tab):
            events=events_from_tab(tab)
            if not events:return True
            outcome=events[-1].get("outcome") or {}
            due=(outcome.get("data") or {}).get("next_attempt_at")
            if not due:return True
            try:return datetime.fromisoformat(due.replace("Z","+00:00"))<=datetime.now(timezone.utc)
            except (TypeError,ValueError):raise QueueConflict("Invalid retry due timestamp")
        items=[tab for tab in items if eligible(tab)]
        if not items:return None
        if priority:
            def score(tab):
                events=events_from_tab(tab)
                return (events[-1].get("outcome") or {}).get("data",{}).get("score",0) if events else 0
            items.sort(key=lambda t:-float(score(t)))
        return self.claim_item(source_queue,prop(items[0],"tabId"))

    def push(self,source_queue,destination_queue,item_id,event):
        doc=self.snapshot()
        flight=prop(self.queue_tab(doc,source_queue+"-onFlight"),"tabId")
        target=prop(self.queue_tab(doc,destination_queue),"tabId")
        tab=find_tab(doc,item_id)
        if not tab: raise QueueConflict("Application tab missing")
        existing=events_from_tab(tab)
        matching=any(e.get("event_id")==event.get("event_id") for e in existing)
        if matching and prop(tab,"parentTabId")==target:
            return {"item_id":item_id,"queue":destination_queue,"event_id":event["event_id"],"replayed":True}
        if prop(tab,"parentTabId")!=flight:
            raise QueueConflict("Item must be in source-onFlight before push")
        if event.get("item_id")!=item_id or event.get("previous_state")!=source_queue or event.get("current_state")!=destination_queue:
            raise QueueConflict("Event item/state mismatch")
        if matching: raise QueueConflict("Event already appended; reconcile interrupted transfer")
        line="\n"+EVENT_PREFIX+json.dumps(event,ensure_ascii=False,sort_keys=True)+"\n"
        self.client.batch_update([
          {"insertText":{"endOfSegmentLocation":{"tabId":item_id},"text":line}},
          {"updateDocumentTabProperties":{"tabProperties":{"tabId":item_id,"parentTabId":target},
                                          "fields":"parentTabId"}}
        ],doc.get("revisionId"))
        return {"item_id":item_id,"queue":destination_queue,"event_id":event["event_id"]}

    def append_event(self,item_id,event):
        doc=self.snapshot()
        tab=find_tab(doc,item_id)
        if not tab:raise QueueConflict("Unknown tab")
        if any(e.get("event_id")==event["event_id"] for e in events_from_tab(tab)):return
        self.client.batch_update([{"insertText":{"endOfSegmentLocation":{"tabId":item_id},
                            "text":"\n"+EVENT_PREFIX+json.dumps(event,sort_keys=True,ensure_ascii=False)+"\n"}}],
                            doc.get("revisionId"))

    def application_events(self,item_id):
        tab=find_tab(self.snapshot(),item_id)
        if tab is None:raise QueueConflict("Unknown application")
        from adapters.google.docs import tab_paragraphs
        from adapters.google.journal import parse_events
        legacy=[{"event_type":"LEGACY_JOURNAL","event_id":e.get("event_id"),
                 "date":e.get("date"),"notes":e.get("bullets",[])}
                for e in parse_events(tab_paragraphs(tab),item_id)]
        structured=events_from_tab(tab)
        notes={"event_type":"APPLICATION_NOTES","content":flatten_text(tab)}
        research=[{"event_type":"RESEARCH_TAB","tab_id":prop(child,"tabId"),
                   "title":prop(child,"title"),"content":flatten_text(child)}
                  for child in tab.get("childTabs") or []]
        return legacy+structured+[notes]+research

    def create_research_tab(self,item_id,title,content):
        doc=self.snapshot()
        if not find_tab(doc,item_id):raise QueueConflict("Unknown application")
        if self.total_tabs(doc)>=self.max_tabs:raise QueueFull("Document tab capacity exhausted")
        existing=[c for c in children(doc,item_id) if prop(c,"title")==title[:50]]
        if existing: return prop(existing[0],"tabId")
        self.client.batch_update([{"addDocumentTab":{
          "tabProperties":{"title":title[:50],"parentTabId":item_id}}}],doc.get("revisionId"))
        new_doc=self.snapshot()
        created=[c for c in children(new_doc,item_id) if prop(c,"title")==title[:50]]
        if len(created)!=1:raise QueueConflict("Research tab creation ambiguous")
        tab_id=prop(created[0],"tabId")
        self.client.batch_update([{"insertText":{"endOfSegmentLocation":{"tabId":tab_id},"text":content}}],
                                new_doc.get("revisionId"))
        return tab_id

    def delete_application(self,item_id):
        doc=self.snapshot()
        if not find_tab(doc,item_id):return False
        self.client.batch_update([{"deleteTab":{"tabId":item_id}}],doc.get("revisionId"))
        return True
