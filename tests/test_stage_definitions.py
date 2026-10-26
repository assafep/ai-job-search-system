"""The stage rename preserves admission and outcome routing contracts."""
from services import stage_definitions
from services.event_dispatcher import dispatch
from services.test_runner import discover_stages, parse_stage
from services.workload_registry import WorkloadRegistry


def test_all_stages_are_discovered_without_order_prefixes():
    stages = stage_definitions.discover_stages()
    assert len(stages) == 11
    for name, path in stages.items():
        assert path == 'stages/' + name + '.yaml'
        definition = stage_definitions.parse_stage(path)
        assert definition['name'] == name
        assert definition['outcomes']
    assert discover_stages() == stages
    assert parse_stage(stages['validate-bookmarked']) == stage_definitions.parse_stage(stages['validate-bookmarked'])


def test_stage_trigger_still_dispatches_to_existing_stage_identity():
    definition = stage_definitions.parse_stage('stages/validate-bookmarked.yaml')
    record = dispatch(definition['trigger']['event_type'], {'application_id': 'example'},
                      'stage-rename-test', WorkloadRegistry())
    assert record['stage'] == 'validate-bookmarked'
    assert definition['input_states'] == ['Bookmarked']
    assert definition['agent'] == 'validate'
    assert definition['outcomes']['validated'] == 'Validated'
