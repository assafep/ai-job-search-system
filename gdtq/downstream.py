"""Idempotent completion archiving; no tab deletion before upload verification."""
import json
from application_service.google_drive_artifacts import application_folder_name
from .queue import QueueConflict,find_tab

class CompletionPublisher:
    def __init__(self,tabs,store,index):
        self.tabs,self.store,self.index=tabs,store,index

    def directory(self,company,role,state):
        name=application_folder_name(company,role)
        return ("Failed/" if state=="Failed" else "Applications/")+name

    def complete(self,item_id,company,role,final_state,reason,source_state=None,attempt_id=None,final_event=None):
        if final_state not in {"Rejected","Offer","Duplicate","Invalidated","Failed","Expired","Ghosted"}:
            raise ValueError("Not terminal: "+final_state)
        path=self.directory(company,role,final_state)+"/final-event-"+item_id+".json"
        doc=self.tabs.snapshot()
        if find_tab(doc,item_id) is None:
            try: archived=json.loads(self.store.read_path(path)["content"])
            except (KeyError,FileNotFoundError):
                raise QueueConflict("Missing application and archive: "+item_id)
            if archived.get("application_id")!=item_id or archived.get("final_state")!=final_state:
                raise QueueConflict("Archive identity mismatch")
            return {"archive_path":path,"state":final_state,"replayed":True}
        events=self.tabs.application_events(item_id)
        if final_event is None:
            from .contracts import Event
            final_event=Event(item_id=item_id,event_type="COMPLETED",
                 previous_state=source_state or "",current_state=final_state,
                 processor="completion_publisher",outcome={"reason":reason},
                 attempt_id=attempt_id).as_dict()
        if not any(e.get("event_id")==final_event["event_id"] for e in events):
            events.append(final_event)
        data=json.dumps({"application_id":item_id,"company":company,"role":role,
                "final_state":final_state,"document_id":self.tabs.client.document_id,
                "events":events},sort_keys=True,indent=2,ensure_ascii=False).encode("utf-8")
        saved=self.store.save_path(path,data,"application/json","gdtq-final-"+item_id)
        if self.store.read_path(path)["content"]!=data:
            raise RuntimeError("Archive content mismatch: original tab retained")
        self.tabs.delete_application(item_id)
        self.index.append_event(final_event)
        return {"archive_path":path,"drive_file":saved.get("id"),"state":final_state}
