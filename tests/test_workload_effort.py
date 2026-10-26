import json
import pytest
from services.workload_effort import effort_for_stage
from adapters.llm.codex_provider import CodexProvider


def test_effort_presets_override_and_reload(tmp_path):
    path = tmp_path / 'effort.json'
    path.write_text(json.dumps({'default': 'low', 'stages': {'prepare-validated': 'medium'}}))
    assert effort_for_stage('prepare-validated', str(path)) == 'medium'
    assert effort_for_stage('prepare-interview', str(path)) == 'low'
    path.write_text(json.dumps({'default': 'high'}))
    assert effort_for_stage('prepare-validated', str(path)) == 'high'


def test_invalid_presets_fail(tmp_path):
    path = tmp_path / 'effort.json'
    path.write_text(json.dumps({'default': 'medium', 'stages': {'prepare-validated': 'typo'}}))
    with pytest.raises(ValueError, match='invalid reasoning effort'):
        effort_for_stage('prepare-validated', str(path))


def test_workload_provider_does_not_mutate_shared_provider():
    shared = CodexProvider(reasoning_effort='low')
    configured = shared.with_reasoning_effort('medium')
    assert configured.reasoning_effort == 'medium'
    assert shared.reasoning_effort == 'low'
