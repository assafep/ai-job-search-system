"""Idempotent final event journal written to Drive before deleting its source tab."""
from __future__ import annotations
import json
from .contracts import Event
from .queue import QueueConflict

def safe_name(value):
    from application_service.google_drive_artifacts import safe_folder_name
    return safe_folder_name(value).replace("/","-")

class CompletionPublisher:
    """store is GoogleDriveArtifactStore; tabs is GDTQ.

    Never delete the source tab unless the completed event journal has been
    persisted and read back. Retain identity keys forever to avoid reapplying.
    """
    def __init__(self,tabs,store,index):
        self.tabs,self.store,self.index=tabs,store,index

    def directory(self,company,role,state):
        label=safe_name(company+" - "+role)
        if state=="Failed": return "Failed/"+label
        return "Applications/"+label

    def complete(self,item_id,company,role,final_state,reason,source_state=None,attempt_id=None):
        if final_state not in {"Rejected","Offer","Duplicate","Invalidated","Failed","Expired","Ghosted"}:
            raise ValueError("Not a terminal state: "+final_state)
        document=self.tabs.snapshot()
        from .queue import find_tab,prop,events_from_tab
        tab=find_tab(document,item_id)
        if tab is None:
            # Caller can treat missing tab as reconciled only after verifying receipt.
            raise QueueConflict("Cannot archive missing application tab")
        events=self.tabs.application_events(item_id)
        final=Event(item_id=item_id,event_type="COMPLETED",previous_state=source_state or "",
                    current_state=final_state,processor="completion_publisher",
                    outcome={"reason":reason},attempt_id=attempt_id).as_dict()
        if not any(e.get("event_id")==final["event_id"] for e in events):
            events.append(final)
        folder=self.directory(company,role,final_state)
        path=folder+"/final-event.json"
        record={"application_id":item_id,"company":company,"role":role,
                "final_state":final_state,"document_id":self.tabs.client.document_id,
                "events":events}
        content=json.dumps(record,ensure_ascii=False,indent=2,sort_keys=True)
        result=self.store.save_path(path,content.encode("utf-8"),"application/json",
                                    "gdtq-final-"+item_id)
        saved=self.store.read_path(path)["content"]
        if json.loads(saved.decode("utf-8"))["application_id"]!=item_id:
            raise RuntimeError("Archive verification failed; original tab preserved")
        self.tabs.delete_application(item_id)
        self.index.append_event(final)
        return {"archive_path":path,"final_state":final_state,"drive_file":result.get("id")}
