"""Synchronous deterministic dispatch and completion coordination.

The scheduler invokes dispatch_tick once per second; submitted agent work
runs outside the tick and reports completion via handle_outcome.
"""
from __future__ import annotations
from .contracts import Event
from .policy import RoutingError

DEFAULT_QUEUES=("Discovered","Bookmarked","Validated","Prepared")
class WorkloadOrchestrator:
    def __init__(self,queue,engine,index,dispatch_agent,publisher=None,retention=None,
                 queues=DEFAULT_QUEUES):
        self.queue,self.engine,self.index=queue,engine,index
        self.dispatch_agent=dispatch_agent
        self.publisher,self.retention=publisher,retention
        self.queues=tuple(queues)

    def dispatch_tick(self):
        if not self.queues: return None
        cursor=self.index.checkpoint("round_robin_cursor") or 0
        name=self.queues[cursor%len(self.queues)]
        self.index.checkpoint("round_robin_cursor",(cursor+1)%len(self.queues))
        claimed=self.queue.pop(name,priority=name=="Validated")
        if not claimed:return None
        if not self.index.claim(claimed.item_id,name,claimed.attempt_id):
            # Fail closed: keep in-flight for recovery if claim failed.
            raise RuntimeError("Existing active claim; reconcile onFlight item "+claimed.item_id)
        self.dispatch_agent(name,claimed)
        return claimed

    def handle_outcome(self,source_state,payload,identity=None):
        """Completion always validates the outcome before state mutation."""
        item_id=payload.get("item_id")
        try:
            decision=self.engine.resolve(source_state,payload)
        except RoutingError as error:
            decision=self.engine.quarantine(source_state)
            payload={"item_id":item_id,"agent":"outcome_validator",
                     "execution_status":"PERMANENT_FAILURE","code":"INVALID_OUTCOME",
                     "summary":str(error),"data":{"error":str(error)},
                     "attempt_id":payload.get("attempt_id")}
        event=Event(item_id=item_id,event_type="PROCESSING_COMPLETED",
                    previous_state=source_state,current_state=decision.next_state,
                    processor=payload["agent"],outcome=payload,
                    rules_version=decision.rules_version,attempt_id=payload.get("attempt_id")).as_dict()
        if decision.terminal:
            if not self.publisher or not identity: raise RuntimeError("Completion publisher and application identity required")
            company,role=identity(item_id)
            result=self.publisher.complete(item_id,company,role,decision.next_state,
                                            payload.get("summary",""),source_state,payload.get("attempt_id"))
        else:
            if self.retention and self.queue.total_tabs()>=self.retention.tab_soft_limit and identity:
                self.retention.sweep(identity)
            result=self.queue.push(source_state,decision.next_state,item_id,event)
        self.index.append_event(event)
        if payload.get("attempt_id"):
            self.index.release(item_id,payload["attempt_id"])
        return result
