"""Non-legacy integration boundaries and pure extracted adapter behavior."""
import ast
import json
from pathlib import Path
from datetime import datetime,timezone
import pytest

from adapters.google.docs import first_posting_url,tab_paragraphs,GoogleDocsClient
from adapters.google.drive import application_folder_name,GoogleDriveArtifactStore
from adapters.google.auth import token_from_environment
from gdtq.settings import load
from gdtq.agent_runner import run,AgentContractError
from gdtq.matching import matching_application

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
    for directory in ("application_service","services","provisioning","api","stages"):
        assert not (root/directory).exists(),directory
    assert (root/"sql"/"gdtq"/"001_neon_index.sql").is_file()
