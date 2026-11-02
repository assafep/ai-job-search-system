"""Explicitly enabled Comeet browser adapter; invoked by ajss.submission.wiring."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
from ..contracts import validate_submission_outcome


def submit(context):
    if os.environ.get("AJSS_SUBMISSION_BACKEND") != "comeet":
        raise PermissionError("Comeet submission is not enabled")
    app=context["application"]
    parsed=urlsplit(app["posting_url"])
    parts=[part for part in parsed.path.split("/") if part]
    if parsed.scheme!="https" or parsed.netloc!="www.comeet.com" or len(parts)<4 or parts[0]!="jobs":
        return {"status":"Blocked","blockers":["Unsupported Comeet posting URL"]}
    node=shutil.which("node")
    chrome=os.environ.get("BROWSER_EXECUTABLE") or shutil.which("google-chrome") or shutil.which("chromium")
    if not node or not chrome:
        return {"status":"Blocked","blockers":["Node, Playwright and Chrome must be installed"]}
    with tempfile.TemporaryDirectory(prefix="ajss-submission-") as directory:
        package=Path(directory)/"submission-package.pdf"
        package.write_bytes(context["package_bytes"])
        payload={"application":app,"profile":context["applicant_profile"],"answers":[],
            "package_path":str(package),"package_name":package.name,"chrome":chrome,
            "destination":{"application_url":app["posting_url"],
                "verification_url":urlunsplit(("https",parsed.netloc,"/"+"/".join(parts[:3]),"",""))}}
        result=subprocess.run([node,str(Path(__file__).with_name("browser.cjs"))],
            input=json.dumps(payload),capture_output=True,text=True,timeout=240)
        if result.returncode:
            raise RuntimeError("Browser interrupted; reconcile submission before retrying")
        return validate_submission_outcome(json.loads(result.stdout))
