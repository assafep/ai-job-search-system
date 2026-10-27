"""Stage-level deterministic contracts and injectable external adapters.

AI providers and browser workers are passed as explicit capabilities. Nothing in
this module silently sends mail, uploads private documents, or submits forms.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from .contracts import ProcessingOutcome
from .scoring import decide_match
from .store import posting_keys
from adapters.job_boards.submission import validate_submission_outcome

def outcome(agent,item_id,code,summary="",data=None,status="SUCCESS",attempt_id=None):
    return ProcessingOutcome(agent,item_id,code,status,summary,data or {},attempt_id).as_dict()

def bookmark_discovered(item,reader,index,*,smart_reader=None,recipe_proposals=None,store=None):
    """reader(url) -> {description, ...}; smart_reader is an opt-in expensive fallback."""
    agent="bookmark_discovered"
    item_id=item["item_id"]; url=item["posting_url"]
    duplicate=index.reserve_identity(item_id,item.get("company",""),item.get("role",""),url,item.get("source_id",""))
    if duplicate:
        return outcome(agent,item_id,"DUPLICATE","Known posting",{"original_item_id":duplicate})
    failures=[]
    for method in [reader,smart_reader]:
        if method is None: continue
        try:
            posting=method(url)
            if not posting or len(str(posting.get("description","")).strip())<500:
                raise ValueError("Posting description missing or too short")
            if recipe_proposals and posting.get("deterministic_recipe"):
                # A proposed recipe is data, never executable without security review.
                recipe_proposals.propose(url,posting["deterministic_recipe"],"read")
            if store is not None:
                from adapters.job_boards.posting_details import posting_content_bytes, posting_artifact_name
                from application_service.google_drive_artifacts import application_folder_name
                base="Applications/"+application_folder_name(item["company"],item["role"])
                path=base+"/"+posting_artifact_name(item["company"],item["role"],url)
                store.save_path(path,posting_content_bytes({
                    "posting_url":url,"source_url":posting.get("resolved_url") or url,
                    "description":posting["description"],"application_id":item_id}),
                    "application/json","posting-"+item_id)
                return outcome(agent,item_id,"BOOKMARKED","Posting downloaded and archived",
                    {"posting_artifact":path,"description_length":len(posting["description"])})
            return outcome(agent,item_id,"BOOKMARKED","Posting downloaded",{"posting":posting})
        except Exception as exc:
            failures.append({"error":str(exc),"kind":type(exc).__name__,
                             "status":getattr(exc,"code",None) or getattr(exc,"status",None)})
    transient=any(f["status"] in (408,429,500,502,503,504) or f["kind"] in
                  ("TimeoutError","ConnectionError") for f in failures)
    status="RETRYABLE_FAILURE" if transient else "PERMANENT_FAILURE"
    return outcome(agent,item_id,"ERROR","Posting retrieval failed",{"failures":failures},status)

def validate_bookmarked(item,profile,requested_skills,threshold):
    agent="validate_bookmarked"
    score=decide_match(item["description"],requested_skills,profile,threshold)
    code="MATCH" if score["is_match"] else "BELOW_THRESHOLD"
    return outcome(agent,item["item_id"],code,"Deterministic skill occurrence score",score)

def prepare_validated(item,profile,resume_pdf,writer,store,*,calendly_url=None):
    """writer({profile,role,posting,resume}) -> cover-letter text; returns error, never fabricates."""
    agent="prepare_validated"
    try:
        cover=writer({"profile":profile,"role":item["role"],"company":item["company"],
                      "description":item["description"],"resume_pdf":resume_pdf})
        if not isinstance(cover,str) or len(cover.strip())<50:
            raise ValueError("Cover letter is missing or incomplete")
        from services.document_generator import generate_application_pdf
        payload=generate_application_pdf(cover,resume_pdf=resume_pdf,applicant_profile=profile,
                                         calendly_url=calendly_url,application=item)
        from application_service.google_drive_artifacts import application_folder_name
        base="Applications/"+application_folder_name(item["company"],item["role"])
        saved=store.save_path(base+"/submission-package.pdf",payload,"application/pdf",
                              "submission-package-"+item["item_id"])
        store.save_path(base+"/cover-letter.txt",cover.encode("utf-8"),"text/plain",
                        "cover-letter-"+item["item_id"])
        return outcome(agent,item["item_id"],"PREPARED","Submission package saved",
                       {"package":saved.get("id"),"path":base+"/submission-package.pdf"})
    except Exception as e:
        return outcome(agent,item["item_id"],"ERROR",str(e),{"error_type":type(e).__name__},
                       "RETRYABLE_FAILURE" if isinstance(e,(TimeoutError,ConnectionError)) else "PERMANENT_FAILURE")

def submit_prepared(item,index,strategies,*,strong_agent=None,recipe_proposals=None):
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
                if recipe_proposals and result.get("deterministic_recipe"):
                    recipe_proposals.propose(item["posting_url"],result["deterministic_recipe"],"submit")
                return outcome(agent,ident,"SUBMITTED","Submission confirmed",{"receipt":result})
            # A completed non-submission is safely eligible for the next strategy.
            if result["status"] in {"Prepared but not submitted","Blocked"}:
                index.checkpoint("submission:"+ident,{"status":"not_submitted"})
                errors.append(result)
                continue
            return outcome(agent,ident,"SUBMISSION_UNCONFIRMED","Confirmation unavailable",{"receipt":result})
        except Exception as e:
            # Caller cannot determine whether the external side effect happened.
            return outcome(agent,ident,"SUBMISSION_UNCONFIRMED","Unknown outcome: "+str(e),
                           {"error_type":type(e).__name__})
    index.checkpoint("submission:"+ident,{"status":"not_submitted","errors":errors})
    return outcome(agent,ident,"MANUAL_REQUIRED","All submission strategies exhausted",{"attempts":errors})

def classify_correspondence(item_id,text,*,source="Applied"):
    """Conservative classification; ambiguous text never triggers email sending."""
    import re
    value=text.casefold()
    if re.search(r"unfortunately|regret to inform|not moving forward|not selected|rejected",value):
        code="REJECTED"
    elif re.search(r"offer of employment|pleased to offer|job offer",value):
        code="OFFER"
    elif re.search(r"interview|schedule a call|next round|meet the team",value):
        code="FOLLOW_UP"
    else: code="AMBIGUOUS"
    return outcome("email_correspondence",item_id,code,"Email intent classified",{"text":text[:10000]})

def reply_to_interview_request(item,recipient,calendly_url,phone_number,mail_adapter,auto_send=False):
    """mail_adapter must enforce authorized recipient, Gmail thread, and send policy."""
    if not recipient or not calendly_url: raise ValueError("Recipient and scheduling URL required")
    body=f"Thank you for reaching out. You can schedule a time at {calendly_url}. My phone number is {phone_number}."
    return mail_adapter(item,recipient,body,send=bool(auto_send))
