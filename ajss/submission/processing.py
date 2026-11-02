"""Submission attempt and receipt lifecycle, called by ajss.worker.handlers.submit."""
from ajss.queue.contracts import ProcessingOutcome
from adapters.job_boards.submission import validate_submission_outcome


def outcome(agent, item_id, code, summary, data):
    return ProcessingOutcome(agent, item_id, code, "SUCCESS", summary, data).as_dict()


def submit_prepared(item,index,strategies,*,strong_agent=None):
    """Each strategy(item) returns existing adapters' standard submission result.

    Unknown/timeout results are uncertain, never blindly retried. Caller must
    provide an actual authorized API/browser action as a strategy.
    """
    agent="submit_prepared"; ident=item["item_id"]
    receipt=index.checkpoint("submission:"+ident)
    if receipt:
        if receipt.get("status")=="confirmed":
            return outcome(agent,ident,"ALREADY_SUBMITTED","Prior confirmed submission",receipt)
        return outcome(agent,ident,"SUBMISSION_UNCONFIRMED","Attempt must be reconciled",receipt)
    errors=[]
    for worker in [*strategies,*([strong_agent] if strong_agent else [])]:
        if worker is None: continue
        # Durably mark attempt as begun BEFORE calling any external side effect.
        index.checkpoint("submission:"+ident,{"status":"begun","worker":getattr(worker,"__name__","agent")})
        try:
            result=validate_submission_outcome(worker(item))
            if result["status"] in {"Submitted successfully","Already submitted"}:
                index.checkpoint("submission:"+ident,{"status":"confirmed","evidence":result})
                return outcome(agent,ident,"SUBMITTED","Submission confirmed",{"receipt":result})
            # A completed non-submission is safely eligible for the next strategy.
            if result["status"] in {"Prepared but not submitted","Blocked"}:
                index.checkpoint("submission:"+ident,{"status":"not_submitted"})
                errors.append(result)
                continue
            return outcome(agent,ident,"SUBMISSION_UNCONFIRMED","Confirmation unavailable",{"receipt":result})
        except Exception as e:
            # Caller cannot determine whether the external side effect happened.
            return outcome(agent,ident,"SUBMISSION_UNCONFIRMED","Submission interrupted; reconcile before retrying",
                           {"error_type":type(e).__name__})
    index.checkpoint("submission:"+ident,{"status":"not_submitted","errors":errors})
    return outcome(agent,ident,"MANUAL_REQUIRED","All submission strategies exhausted",{"attempts":errors})

