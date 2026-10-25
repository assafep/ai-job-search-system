from services.completion_workflow import run_completion_workflow


def test_rejected_and_invalidated_use_completion_and_duplicates_are_removed():
    apps = [
        {'application_id': 'r', 'status': 'Rejected'},
        {'application_id': 'i', 'status': 'Invalidated'},
        {'application_id': 'd', 'status': 'Duplicate'},
        {'application_id': 'a', 'status': 'Applied'},
    ]
    completed = []
    removed = []
    results = run_completion_workflow(
        apps,
        lambda application_id, reason: completed.append((application_id, reason)),
        lambda application_id: removed.append(application_id),
    )
    assert completed == [('r', 'rejected'), ('i', 'invalidated')]
    assert removed == ['d']
    assert [item['action'] for item in results] == ['completed', 'completed', 'removed']


if __name__ == '__main__':
    test_rejected_and_invalidated_use_completion_and_duplicates_are_removed()
    print('ok')


def test_terminal_workflow_failure_does_not_regress_terminal_application_states():
    from services.test_runner import discover_workflows, parse_workflow
    from services.workload_runner import validate_workflow_outcomes
    definition = parse_workflow(discover_workflows()['complete-journal'])
    validate_workflow_outcomes('complete-journal', definition)
    assert definition['outcomes'].get('system_error') is None
