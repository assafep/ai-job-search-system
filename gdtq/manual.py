"""Explicit human progress updates and DLQ redrive.

All external changes are recorded with the same GDTQ queue semantics as agents.
"""
from .contracts import Event
from .policy import RoutingError

def manual_transition(orchestrator,source_state,item_id,target_state,*,reason="Manual update"):
    e=orchestrator.engine
    if target_state not in e.transitions.get(source_state,set()) and target_state!=source_state:
        raise RoutingError("Forbidden manual transition: "+source_state+" -> "+target_state)
    if source_state=="Manual Apply Required":
        codes={"Applied":"APPLIED","Interviewing":"INTERVIEWING","Rejected":"REJECTED",
               "Offer":"OFFER","Prepared":"PREPARED","Duplicate":"DUPLICATE"}
        agent="manual_update"
    elif source_state in {"Applied","Interviewing"}:
        codes={"Interviewing":"INTERVIEWING","Rejected":"REJECTED","Offer":"OFFER"}
        agent="phone_followup"
    else:raise RoutingError("Manual change unsupported for "+source_state)
    if target_state not in codes:
        raise RoutingError("Unsupported manual target "+target_state)
    payload={"agent":agent,"item_id":item_id,"code":codes[target_state],
             "execution_status":"SUCCESS","summary":reason,"data":{"manual":True}}
    return orchestrator.handle_external(source_state,item_id,payload)

def redrive(orchestrator,item_id,target_state,*,reason,approved_by):
    src="Dead Letters Queue"
    if target_state not in orchestrator.engine.transitions[src]:
        raise RoutingError("Invalid DLQ redrive target "+target_state)
    if not reason.strip() or not approved_by.strip():
        raise ValueError("Redrive requires approval and explanation")
    claimed=orchestrator.queue.claim_item(src,item_id)
    if not orchestrator.index.claim(item_id,src,claimed.attempt_id):
        raise RuntimeError("DLQ claim conflict")
    event=Event(item_id=item_id,event_type="ADMINISTRATIVE_REDRIVE",
                previous_state=src,current_state=target_state,processor=approved_by,
                outcome={"reason":reason},rules_version=orchestrator.engine.version,
                attempt_id=claimed.attempt_id).as_dict()
    result=orchestrator.queue.push(src,target_state,item_id,event)
    orchestrator.index.append_event(event)
    orchestrator.index.release(item_id,claimed.attempt_id)
    return result
