"""Non-legacy integration boundaries and pure extracted adapter behavior."""
import ast
import json
from pathlib import Path
from datetime import datetime,timezone
import pytest

from adapters.google.docs import first_posting_url,tab_paragraphs,GoogleDocsClient
from adapters.google.drive import application_folder_name,GoogleDriveArtifactStore
from adapters.google.auth import token_from_environment
from ajss.settings import load
from ajss.documents.prompts import run,AgentContractError
from ajss.worker.matching import matching_application

def test_url_extraction_including_https_scheme():
    paragraph={"text":"Posting: https://jobs.example.com/jobs/123?source=test","isListItem":False}
    assert first_posting_url([],[paragraph])=="https://jobs.example.com/jobs/123?source=test"
    assert first_posting_url([], [{"text":"https://drive.google.com/file/d/foo"}])==""

def test_journal_reader_preserves_hyperlinks():
    tab={"documentTab":{"body":{"content":[{"startIndex":1,"endIndex":15,
         "paragraph":{"elements":[{"textRun":{"content":"Backend engineer\n",
          "textStyle":{"link":{"url":"https://jobs.example.com/123"}}}}]}}]}}}
    pars=tab_paragraphs(tab)
    assert pars[0]["text"]=="Backend engineer"
    assert pars[0]["links"]==["https://jobs.example.com/123"]

def test_folder_name_is_single_component():
    name=application_folder_name("Acme","Senior / Backend")
    assert "/" not in name
    assert "Acme" in name and "Backend" in name

def test_token_source_uses_injected_environment():
    assert token_from_environment({"GOOGLE_ACCESS_TOKEN":"test-token"})=="test-token"
    with pytest.raises(RuntimeError):
        token_from_environment({"GOOGLE_REFRESH_TOKEN":"incomplete"})

def test_runtime_config_is_explicit(tmp_path):
    config=tmp_path/"runtime.json"
    config.write_text(json.dumps({"google_drive":{
        "interview_journal_url":"https://docs.google.com/document/d/abc123/edit",
        "artifact_root_url":"https://drive.google.com/drive/folders/root"}}))
    assert load(path=config)["google_drive"]["artifact_root_url"].endswith("root")
    config.write_text("{}")
    with pytest.raises(ValueError):load(path=config)

def test_extracted_agent_runner_schema_and_no_writes():
    def good(prompt,context):
        return {"cover_letter":"Dear Recruiting Team","proposed_action":"prepare"}
    assert run("application",{"role":"Backend"},good)["cover_letter"]=="Dear Recruiting Team"
    with pytest.raises(AgentContractError):
        run("application",{},lambda p,c:{"cover_letter":"hi"})
    with pytest.raises(ValueError, match="Unknown AI agent"):
        run("nonexistent-agent",{},lambda p,c:{})

def test_conservative_application_matching_ambiguity():
    apps=[{"company":"Acme Labs","application_id":"app-1"},
          {"company":"Cloud Moon","application_id":"app-2"}]
    assert matching_application("Your Acme Labs interview",apps)["application_id"]=="app-1"
    assert matching_application("Acme Labs and Cloud Moon",apps) is None
    assert matching_application("No recognizable company",apps) is None

def test_current_python_sources_do_not_depend_on_retired_runtime():
    root=Path(__file__).resolve().parents[1]
    for directory in ("gdtq","adapters"):
        for path in (root/directory).rglob("*.py"):
            source=path.read_text(encoding="utf-8")
            tree=ast.parse(source,filename=str(path))
            modules=[node.module or "" for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
            modules += [alias.name for node in ast.walk(tree)
                        if isinstance(node,ast.Import) for alias in node.names]
            for imported in modules:
                assert not imported.startswith(("application_service","services","provisioning","api")),(
                    str(path),imported)

def test_no_old_runtime_directories():
    root=Path(__file__).resolve().parents[1]
    for directory in ("application_service","services","provisioning","stages"):
        assert not (root/directory).exists(),directory
    assert (root/"sql"/"gdtq"/"001_neon_index.sql").is_file()
    assert (root/"api"/"index.py").is_file()


def test_google_onboarding_reuses_selected_doc_and_provisions_tabs():
    from ajss.onboard.google import bootstrap_google_workspace

    class FakeDrive:
        def __init__(self, token, root):
            self.files=[{"id":"existing","name":"Jobs","mimeType":"application/vnd.google-apps.document"}]
            self.created=False
            self.application_directory=False
        def list_root_files(self): return self.files
        def create_google_doc(self, name):
            self.created=True
            raise AssertionError("selected document should be reused")
        def ensure_application_directory(self):
            self.application_directory=True

    class FakeQueue:
        def __init__(self, client, max_tabs):
            self.client=client
            self.max_tabs=max_tabs
        def snapshot(self):
            return {"tabs":[{"tabProperties":{"title":name,"tabId":name}}
                            for name in ["Discovered","Dead Letters Queue"]]}
        def total_tabs(self, doc): return len(doc["tabs"])
        def ensure_state_tabs(self, names):
            assert "Discovered" in names
            assert "Dead Letters Queue" in names
            assert "Discovered-onFlight" in names
            return ["Discovered", "Discovered-onFlight"]

    drive=None
    def drive_factory(*args):
        nonlocal drive
        drive=FakeDrive(*args)
        return drive
    result=bootstrap_google_workspace(
        "token", "https://drive.google.com/drive/folders/root",
        queue_document_url="https://docs.google.com/document/d/existing/edit",
        drive_factory=drive_factory,
        docs_factory=lambda token, ident: type("Client", (), {"document_id":ident})(),
        queue_factory=FakeQueue,
    )
    assert result["document_id"] == "existing"
    assert result["document_url"].endswith("/existing/edit")
    assert drive.application_directory and not drive.created


def test_google_onboarding_creates_queue_doc_when_not_found():
    from ajss.onboard.google import bootstrap_google_workspace

    class FakeDrive:
        def __init__(self, token, root): self.files=[]
        def list_root_files(self): return self.files
        def create_google_doc(self, name):
            assert name == "AJSS Main Queue"
            return {"id":"new-doc","name":name}
        def ensure_application_directory(self): pass
    class FakeQueue:
        def __init__(self, client, max_tabs): self.client=client
        def snapshot(self): return {"tabs":[]}
        def total_tabs(self, doc): return len(doc["tabs"])
        def ensure_state_tabs(self, names): return list(names)

    result=bootstrap_google_workspace(
        "token", "https://drive.google.com/drive/folders/root",
        drive_factory=FakeDrive,
        docs_factory=lambda token, ident: type("Client", (), {"document_id":ident})(),
        queue_factory=FakeQueue,
    )
    assert result["document_id"] == "new-doc"
    assert "Discovered" in result["created_tabs"]


def test_google_onboarding_stops_before_writes_above_soft_tab_limit():
    from ajss.onboard.google import bootstrap_google_workspace
    from ajss.queue import QueueFull

    class FakeDrive:
        application_directory=False
        def __init__(self, token, root): pass
        def list_root_files(self):
            return [{"id":"existing","name":"Queues",
                     "mimeType":"application/vnd.google-apps.document"}]
        def ensure_application_directory(self): self.application_directory=True
        def create_google_doc(self, name): raise AssertionError("must reuse existing doc")
    class FakeQueue:
        ensured=False
        def __init__(self, client, max_tabs): pass
        def snapshot(self):
            return {"tabs":[{"tabProperties":{"title":"existing-{}".format(i),"tabId":str(i)}}
                             for i in range(91)]}
        def total_tabs(self, doc): return len(doc["tabs"])
        def ensure_state_tabs(self, names):
            self.ensured=True
            raise AssertionError("must stop before provisioning")

    drive=None
    def drive_factory(*args):
        nonlocal drive
        drive=FakeDrive(*args)
        return drive
    with pytest.raises(QueueFull, match="soft limit"):
        bootstrap_google_workspace(
            "token", "https://drive.google.com/drive/folders/root",
            queue_document_url="https://docs.google.com/document/d/existing/edit",
            drive_factory=drive_factory,
            docs_factory=lambda token, ident: object(),
            queue_factory=FakeQueue,
        )
    assert drive.application_directory is False


def test_google_onboarding_runtime_config_and_directory_guard(tmp_path):
    from ajss.onboard.google import write_runtime_config

    result={"document_url":"https://docs.google.com/document/d/abc/edit",
            "drive_root_url":"https://drive.google.com/drive/folders/root"}
    config=tmp_path/"runtime.json"
    config.write_text('{"scheduling":{"calendly_url":"https://calendly.com/test"}}')
    write_runtime_config(result,config)
    data=json.loads(config.read_text())
    assert data["google_drive"]["interview_journal_url"] == result["document_url"]
    assert data["scheduling"]["calendly_url"] == "https://calendly.com/test"
    with pytest.raises(IsADirectoryError):
        write_runtime_config(result,tmp_path)
