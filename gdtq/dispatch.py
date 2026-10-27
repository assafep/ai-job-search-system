"""Round-robin dispatch, validated completion, and targeted external claims."""
from .contracts import Event
from .policy import RoutingError
from .queue import QueueFull

DEFAULT_QUEUES=("Discovered","Bookmarked","Validated","Prepared")
class WorkloadOrchestrator:
    def __init__(self,queue,engine,index,dispatch_agent,publisher=None,retention=None,
                 queues=DEFAULT_QUEUES,identity=None):
        self.queue,self.engine,self.index=queue,engine,index
        self.dispatch_agent=dispatch_agent
        self.publisher,self.retention=publisher,retention
        self.queues=tuple(queues)
        self.identity=identity

    def relieve_pressure(self,identity=None):
        identity=identity or self.identity
        if self.retention and identity and self.queue.total_tabs()>=self.retention.tab_soft_limit:
            return self.retention.sweep(identity)
        return []

    def dispatch_tick(self):
        if not self.queues:return None
        self.relieve_pressure()
        cursor=self.index.checkpoint("round_robin_cursor") or 0
        state=self.queues[cursor%len(self.queues)]
        self.index.checkpoint("round_robin_cursor",(cursor+1)%len(self.queues))
        claimed=self.queue.pop(state,priority=state=="Validated")
        if claimed is None:return None
        if not self.index.claim(claimed.item_id,state,claimed.attempt_id):
            raise RuntimeError("Concurrent in-flight claim conflict: "+claimed.item_id)
        self.dispatch_agent(state,claimed) # enqueue asynchronously, never block
        return claimed

    def handle_external(self,source_state,item_id,payload,identity=None):
        claimed=self.queue.claim_item(source_state,item_id)
        if not self.index.claim(item_id,source_state,claimed.attempt_id):
            raise RuntimeError("Another processor owns application "+item_id)
        return self.handle_outcome(source_state,dict(payload,attempt_id=claimed.attempt_id),identity)

    def handle_outcome(self,source_state,payload,identity=None):
        item_id=payload["item_id"]
        identity=identity or self.identity
        try:
            decision=self.engine.resolve(source_state,payload)
        except RoutingError as exc:
            decision=self.engine.quarantine(source_state)
            payload={"item_id":item_id,"agent":"outcome_validator","code":"INVALID_OUTCOME",
                     "execution_status":"PERMANENT_FAILURE","summary":str(exc),
                     "data":{"error":str(exc)},"attempt_id":payload.get("attempt_id")}
        event=Event(item_id=item_id,event_type="PROCESSING_COMPLETED",
                    previous_state=source_state,current_state=decision.next_state,
                    processor=payload["agent"],outcome=payload,
                    rules_version=decision.rules_version,attempt_id=payload.get("attempt_id")).as_dict()
        if decision.terminal:
            if not self.publisher or not identity:
                raise RuntimeError("Completion requires publisher and identity resolver")
            company,role=identity(item_id)
            result=self.publisher.complete(item_id,company,role,decision.next_state,
                                           payload.get("summary",""),source_state,payload.get("attempt_id"),
                                           final_event=event)
        else:
            self.relieve_pressure(identity)
            try:
                result=self.queue.push(source_state,decision.next_state,item_id,event)
            except QueueFull:
                self.relieve_pressure(identity)
                result=self.queue.push(source_state,decision.next_state,item_id,event)
        self.index.append_event(event)
        if payload.get("attempt_id"):
            self.index.release(item_id,payload["attempt_id"])
        return result
