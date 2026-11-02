"""Local runtime settings used by the single GDTQ worker."""
import json,os,re
from pathlib import Path
from urllib.parse import urlsplit
DEFAULT=Path(__file__).resolve().parents[1]/"config"/"runtime.json"
# The résumé path sits with the other Google Drive settings. The older AJSS_ name still fills it when the GOOGLE_ name is empty.
RESUME_DRIVE_PATH="GOOGLE_RESUME_DRIVE_PATH"
_RESUME_DRIVE_PATH_FALLBACK="AJSS_RESUME_DRIVE_PATH"

def resume_drive_path(env=None):
    source=os.environ if env is None else env
    for name in (RESUME_DRIVE_PATH,_RESUME_DRIVE_PATH_FALLBACK):
        value=(source.get(name) or "").strip()
        if value:
            return value
    return ""
def database_url(env=None):
    """The worker reads DATABASE_URL, then AJSS_DATABASE_URL."""
    source=os.environ if env is None else env
    for name in ("DATABASE_URL","AJSS_DATABASE_URL"):
        value=(source.get(name) or "").strip()
        if value:
            return value
    return ""
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
    from adapters.google.drive import is_drive_folder_url
    if not is_drive_folder_url(drive["artifact_root_url"]):
        raise ValueError("Drive folder URL must be the address bar of an open folder, with /drive/folders/")
    return data
def calendly_url(data):
    return (data.get("scheduling") or {}).get("calendly_url")
