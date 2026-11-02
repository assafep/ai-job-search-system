"""Stage-level deterministic contracts and injectable external adapters.

AI providers and browser workers are passed as explicit capabilities. A worker
returns a typed outcome. Identity reservation, Drive uploads, and submission
are effects on that outcome. The orchestrator applies them.
"""
from __future__ import annotations
from ajss.queue.contracts import ProcessingOutcome
from ajss.worker.scoring import decide_match
from ajss.worker.effects import ReserveIdentity, SaveArtifact, SubmitPrepared, attach
from ajss.submission.processing import submit_prepared  # Public compatibility import

def outcome(agent,item_id,code,summary="",data=None,status="SUCCESS",attempt_id=None):
    return ProcessingOutcome(agent,item_id,code,status,summary,data or {},attempt_id).as_dict()

def _known_owner(index,item_id,company,role,url,source_id=""):
    """Read-only duplicate check. Missing lookup support leaves the decision to reserve."""
    if not hasattr(index,"lookup_identity"):
        return None
    owner=index.lookup_identity(company,role,url,source_id)
    if owner and owner!=item_id:
        return owner
    return None

def bookmark_discovered(item,reader,index,*,smart_reader=None,store=None):
    """reader(url) -> {description, ...}; smart_reader is an opt-in expensive fallback.

    The reader and lookup_identity are reads. Reservation and the archive are effects.
    """
    agent="bookmark_discovered"
    item_id=item["item_id"]; url=item["posting_url"]
    company=item.get("company",""); role=item.get("role",""); source_id=item.get("source_id","")
    duplicate=_known_owner(index,item_id,company,role,url,source_id)
    if duplicate:
        return outcome(agent,item_id,"DUPLICATE","Known posting",{"original_item_id":duplicate})
    failures=[]
    for method in [reader,smart_reader]:
        if method is None: continue
        try:
            posting=method(url)
            if not posting or len(str(posting.get("description","")).strip())<500:
                raise ValueError("Posting description missing or too short")
            verified_company=posting.get("company") or company
            verified_role=posting.get("role") or role
            resolved_url=posting.get("resolved_url") or url
            duplicate=_known_owner(index,item_id,verified_company,verified_role,resolved_url)
            if duplicate:
                return outcome(agent,item_id,"DUPLICATE","Known posting after extraction",
                               {"original_item_id":duplicate})
            if not verified_company or not verified_role:
                raise ValueError("Posting company and role could not be verified")
            effects=[
                ReserveIdentity(item_id,company,role,url,source_id),
                ReserveIdentity(item_id,verified_company,verified_role,resolved_url),
            ]
            data={"description_length":len(posting["description"]),
                  "company":verified_company,"role":verified_role}
            summary="Posting downloaded"
            if store is not None:
                from adapters.job_boards.posting_details import posting_content_bytes, posting_artifact_name
                from adapters.google.drive import application_folder_name
                base="Applications/"+application_folder_name(verified_company,verified_role)
                path=base+"/"+posting_artifact_name(verified_company,verified_role,url)
                effects.append(SaveArtifact(path,posting_content_bytes({
                    "posting_url":url,"source_url":posting.get("resolved_url") or url,
                    "description":posting["description"],"application_id":item_id}),
                    "application/json","posting-"+item_id))
                data["posting_artifact"]=path
                summary="Posting downloaded and archived"
            else:
                data["posting"]=posting
            return attach(outcome(agent,item_id,"BOOKMARKED",summary,data),*effects)
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
    """writer({profile,role,posting,resume}) -> cover-letter text; returns error, never fabricates.

    PDF bytes are computed here. store is only a capability check; the orchestrator writes.
    """
    agent="prepare_validated"
    try:
        if store is None:
            raise RuntimeError("Artifact store is not configured")
        cover=writer({"profile":profile,"role":item["role"],"company":item["company"],
                      "description":item["description"],"resume_pdf":resume_pdf})
        if not isinstance(cover,str) or len(cover.strip())<50:
            raise ValueError("Cover letter is missing or incomplete")
        from ajss.documents.generator import generate_application_pdf
        payload=generate_application_pdf(cover,resume_pdf=resume_pdf,applicant_profile=profile,
                                         calendly_url=calendly_url,application=item)
        from adapters.google.drive import application_folder_name
        base="Applications/"+application_folder_name(item["company"],item["role"])
        path=base+"/submission-package.pdf"
        return attach(outcome(agent,item["item_id"],"PREPARED","Submission package saved",{"path":path}),
                      SaveArtifact(path,payload,"application/pdf","submission-package-"+item["item_id"],into="package"),
                      SaveArtifact(base+"/cover-letter.txt",cover.encode("utf-8"),"text/plain",
                                   "cover-letter-"+item["item_id"]))
    except Exception as e:
        return outcome(agent,item["item_id"],"ERROR",str(e),{"error_type":type(e).__name__},
                       "RETRYABLE_FAILURE" if isinstance(e,(TimeoutError,ConnectionError)) else "PERMANENT_FAILURE")

def submission_plan(item,strategies,strong_agent=None):
    """Shell outcome. The orchestrator runs the submission attempt and replaces this shell."""
    shell=outcome("submit_prepared",item["item_id"],"ERROR","Submission awaits the orchestrator",
                  {},"PERMANENT_FAILURE")
    return attach(shell,SubmitPrepared(item,strategies,strong_agent))

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
