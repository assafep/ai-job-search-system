"""Connect prepared GDTQ artifacts to the job-board submission framework."""
import os
from adapters.job_boards.framework import AdapterFramework
from adapters.job_boards.interface import SubmissionContext


def configured_strategies(runtime):
    backend=os.environ.get("AJSS_SUBMISSION_BACKEND","disabled")
    if backend=="disabled":return ()
    if backend!="comeet":raise ValueError("Unknown AJSS_SUBMISSION_BACKEND")
    from adapters.job_boards.submission.comeet.adapter import submit
    return (strategy(runtime,submit),)


def strategy(runtime,submitter):
    def submit(item):
        prepared=next((e.get("outcome",{}).get("data",{}) for e in reversed(item["events"])
                       if e.get("outcome",{}).get("code")=="PREPARED"),None)
        if not prepared or not prepared.get("path"):
            return {"status":"Blocked","blockers":["Verified prepared package reference is missing"]}
        package=runtime["store"].read_path(prepared["path"])["content"]
        if not isinstance(package,bytes) or not package.startswith(b"%PDF-"):
            return {"status":"Blocked","blockers":["Prepared artifact is not a PDF"]}
        context=SubmissionContext(application=item,posting_url=item["posting_url"],
            profile=runtime["loader"].profile(),package_bytes=package,package_name="submission-package.pdf")
        return AdapterFramework(submitter=submitter).submit(context).data
    return submit
