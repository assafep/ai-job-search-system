"""The development contract has one vocabulary, one registry and no aliases."""
from dataclasses import asdict
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
import yaml

from application_service.mcp_adapter import Catalog, _resolve
from application_service.stage_tasks import StageTasks, validate_request
from provisioning.stage_task_cutover import convert, transform
from services.workload_registry import WorkloadRegistry
from tests.test_stage_tasks import MemoryStore


def test_stage_request_contract_rejects_unrecognized_fields():
    with pytest.raises(ValueError, match='Unknown stage request fields'):
        validate_request({'workflow': 'discover', 'idempotency_key': 'a'})
    request = {'stage': 'discover', 'idempotency_key': 'a'}
    assert validate_request(request) == {'stage': 'discover'}
    assert Catalog(profile='stages').request('startStageTask', {'body': request})[:2] == ('POST', '/stage-tasks')
    names = Catalog(profile='stages').operations
    assert {'listStages', 'listStageTasks', 'getStageTask', 'getStageTaskReport', 'nextChatGPTStageTask'} <= names.keys()
    assert not any('workflow' in name.lower() for name in names)


def test_task_list_matches_the_published_response_schema():
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / 'openapi.yaml').read_text())
    response_schema = document['paths']['/stage-tasks']['get']['responses']['200']['content']['application/json']['schema']
    assert not any('workflow' in path for path in document['paths'])
    assert Draft202012Validator(_resolve(response_schema, document)).is_valid(StageTasks(MemoryStore()).list())


def test_registry_persists_only_stage_identity(tmp_path):
    path = tmp_path / 'registry.json'
    registry = WorkloadRegistry(storage_path=str(path))
    record = registry.enqueue({'workload_id': 'one', 'stage': 'discover', 'event': {}})
    assert asdict(record)['stage'] == 'discover'
    assert json.loads(path.read_text())['one']['stage'] == 'discover'
    assert 'workflow' not in json.loads(path.read_text())['one']
    assert WorkloadRegistry(storage_path=str(path)).get('one').stage == 'discover'


def test_offline_cutover_preserves_nested_state_and_is_repeatable(tmp_path):
    path = tmp_path / 'registry.json'
    original = {'id': {'workflow': 'discover', 'run_id': 'opaque', 'status': 'running',
                       'claim_token': 'private', 'result': [{'workflow': 'discover', 'count': 2}]}}
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    assert convert(path)
    assert json.loads(path.read_text()) == transform(original)
    backup = path.with_name(path.name + '.before-stage-cutover')
    assert backup.read_bytes() == before
    assert backup.stat().st_mode & 0o777 == 0o600
    assert not convert(path)
    with pytest.raises(ValueError, match='Conflicting'):
        transform({'workflow': 'discover', 'stage': 'submit-prepared'})


def test_unmigrated_store_cannot_silently_start_an_empty_queue(monkeypatch):
    from application_service.stage_tasks import Store
    from application_service import initialization
    monkeypatch.setattr(initialization, 'resource', lambda connection, key: None)
    with pytest.raises(RuntimeError, match='005_stage_task_names.sql'):
        Store._records(object())
    monkeypatch.setattr(initialization, 'resource', lambda connection, key: {})
    assert Store._records(object()) == {}
