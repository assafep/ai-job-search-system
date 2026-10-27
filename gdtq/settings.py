"""Local runtime settings used by the single GDTQ worker."""
import json,os,re
from pathlib import Path
from urllib.parse import urlsplit
DEFAULT=Path(__file__).resolve().parents[1]/"config"/"runtime.json"
def load(require=True,path=None):
    file=Path(path or os.environ.get("RUNTIME_CONFIG_PATH") or DEFAULT)
    if not file.is_file():
        if require:raise FileNotFoundError("Missing config/runtime.json; configure Google Docs/Drive and applicant profile")
        return {}
    data=json.loads(file.read_text(encoding="utf-8"))
    drive=data.get("google_drive") or {}
    for name in ("interview_journal_url","artifact_root_url"):
        if not drive.get(name):raise ValueError("Missing google_drive."+name)
    if not re.search(r"^https://docs\.google\.com/document/(?:u/\d+/)?d/[^/]+",drive["interview_journal_url"]):
        raise ValueError("Invalid Google Docs journal URL")
    if "drive.google.com/drive/" not in drive["artifact_root_url"]:raise ValueError("Invalid Google Drive root URL")
    return data
def calendly_url(data):
    return (data.get("scheduling") or {}).get("calendly_url")
