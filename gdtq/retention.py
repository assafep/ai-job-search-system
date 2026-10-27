"""Capacity and inactivity policies; human queues cannot block new work forever."""
from datetime import datetime,timezone,timedelta
from .queue import children,prop,events_from_tab

class RetentionManager:
    def __init__(self,queue,publisher,manual_limit=20,tab_soft_limit=90,
                 manual_days=30,applied_days=30,interview_days=21):
        self.queue,self.publisher=queue,publisher
        self.manual_limit=manual_limit
        self.tab_soft_limit=tab_soft_limit
        self.days={"Manual Apply Required":manual_days,"Applied":applied_days,"Interviewing":interview_days}

    def _items(self,state):
        doc=self.queue.snapshot()
        root=self.queue.queue_tab(doc,state)
        result=[]
        for tab in children(doc,prop(root,"tabId")):
            events=events_from_tab(tab)
            timestamp=(events[-1].get("created_at") if events else None)
            # Legacy tabs without structured timestamps must not be silently
            # marked as ancient and expired. Use current time and enforce
            # the queue-length cap independently of age.
            try: updated=datetime.fromisoformat(timestamp.replace("Z","+00:00")) if timestamp else datetime.now(timezone.utc)
            except (ValueError,TypeError): updated=datetime.now(timezone.utc)
            result.append((updated,tab,events))
        return sorted(result,key=lambda x:x[0])

    def sweep(self,identity,now=None):
        """identity(item_id) -> (company,role); only expire when metadata is known."""
        now=now or datetime.now(timezone.utc)
        results=[]
        for state in self.days:
            candidates=self._items(state)
            if state=="Manual Apply Required":
                excess=max(0,len(candidates)-self.manual_limit)
            else: excess=0
            for index,(updated,tab,_) in enumerate(candidates):
                doc_pressure=self.queue.total_tabs()>=self.tab_soft_limit and state=="Manual Apply Required"
                stale=now-updated>=timedelta(days=self.days[state])
                if not (stale or index<excess or doc_pressure): continue
                item_id=prop(tab,"tabId")
                metadata=identity(item_id)
                if not metadata: continue
                company,role=metadata
                final="Ghosted" if state=="Applied" and stale else "Expired"
                results.append(self.publisher.complete(item_id,company,role,final,
                  "inactivity" if stale else "queue_retention",source_state=state))
                if doc_pressure and self.queue.total_tabs()<self.tab_soft_limit: break
        return results
