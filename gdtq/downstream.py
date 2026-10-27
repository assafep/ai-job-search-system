"""Archive completed applications as JSON and delete source only after verification."""
import json
from .queue import QueueConflict

def safe_name(value):
    from application_service.google_drive_artifacts import safe_folder_name
    return safe_folder_name(value).replace("/","-")

class CompletionPublisher:
    def __init__(self,tabs,store,index):
        self.tabs,self.store,self.index=tabs,store,index

    def directory(self,company,role,state):
        label=safe_name(company+" - "+role)
        return ("Failed/" if state=="Failed" else "Applications/")+label

    def complete(self,item_id,company,role,final_state,reason,source_state=None,attempt_id=None,final_event=None):
        if final_state not in {"Rejected","Offer","Duplicate","Invalidated","Failed","Expired","Ghosted"}:
            raise ValueError("Not terminal "+final_state)
        document=self.tabs.snapshot()
        from .queue import find_tab
        if find_tab(document,item_id) is None:
            raise QueueConflict("Missing application tab: cannot archive")
        events=self.tabs.application_events(item_id)
        if final_event is None:
            from .contracts import Event
            final_event=Event(item_id=item_id,event_type="COMPLETED",
                 previous_state=source_state or "",current_state=final_state,
                 processor="completion_publisher",outcome={"reason":reason},
                 attempt_id=attempt_id).as_dict()
        if not any(e.get("event_id")==final_event["event_id"] for e in events):
            events.append(final_event)
        path=self.directory(company,role,final_state)+"/final-event.json"
        record={"application_id":item_id,"company":company,"role":role,
                "final_state":final_state,"document_id":self.tabs.client.document_id,
                "events":events}
        data=json.dumps(record,sort_keys=True,indent=2,ensure_ascii=False).encode("utf-8")
        saved=self.store.save_path(path,data,"application/json", "gdtq-final-"+item_id)
        persisted=self.store.read_path(path)["content"]
        if persisted!=data:
            raise RuntimeError("Archive contents differ; tab preserved")
        self.tabs.delete_application(item_id)
        self.index.append_event(final_event)
        return {"archive_path":path,"drive_file":saved.get("id"),"state":final_state}
