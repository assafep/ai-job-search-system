"""Tests for workflow outcome validation against the state machine."""

import pytest
from services.workload_runner import validate_workflow_outcomes


def test_valid_discover():
    definition = {
        "input_states": ["Discovered"],
        "outcomes": {
            "discovered": "Discovered",
            "duplicate": "Duplicate",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("discover", definition)


def test_valid_read_posting():
    definition = {
        "input_states": ["Discovered"],
        "outcomes": {
            "read": None,
            "read_failure": "Manual Apply Required",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("bookmark-discovered", definition)


def test_valid_validate():
    definition = {
        "input_states": ["Bookmarked"],
        "outcomes": {
            "validated": "Validated",
            "invalidated": "Invalidated",
            "duplicate": "Duplicate",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("validate-bookmarked", definition)


def test_valid_prepare():
    definition = {
        "input_states": ["Validated"],
        "outcomes": {
            "prepared": "Prepared",
            "failed": "Manual Apply Required",
            "duplicate": "Duplicate",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("prepare-validated", definition)


def test_valid_submit_ready():
    definition = {
        "input_states": ["Prepared"],
        "outcomes": {
            "submitted": "Applied",
            "failed": "Manual Apply Required",
            "unconfirmed": "Manual Apply Required",
            "waiting": None,
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("submit-prepared", definition)


def test_valid_handle_incoming_email():
    definition = {
        "input_states": ["Applied", "Interviewing"],
        "outcomes": {
            "processed": None,
            "interview": "Interviewing",
            "rejected": "Rejected",
            "offered": "Offer",
            "failed": None,
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("handle-incoming-email", definition)


def test_valid_prepare_interview():
    definition = {
        "input_states": ["Applied", "Interviewing"],
        "outcomes": {
            "prepared": "Interviewing",
            "failed": None,
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("prepare-interview", definition)


def test_missing_outcomes_raises():
    with pytest.raises(ValueError, match="must declare outcomes"):
        validate_workflow_outcomes("missing-outcomes", {"input_states": ["Bookmarked"]})


def test_invalid_target_state_raises():
    definition = {
        "input_states": ["Bookmarked"],
        "outcomes": {"accepted": "NonExistentState", "system_error": "Dead Letters Queue"},
    }
    with pytest.raises(ValueError, match="invalid state"):
        validate_workflow_outcomes("bad-target", definition)


def test_system_error_must_route_to_dead_letters():
    definition = {
        "input_states": ["Bookmarked"],
        "outcomes": {"prepared": "Prepared", "system_error": "Manual Apply Required"},
    }
    with pytest.raises(ValueError, match="system_error must route to Dead Letters Queue"):
        validate_workflow_outcomes("bad-system-error", definition)


def test_invalid_transition_from_input_state_raises():
    # From Bookmarked, you cannot go directly to Applied
    definition = {
        "input_states": ["Bookmarked"],
        "outcomes": {"accepted": "Applied", "system_error": "Dead Letters Queue"},
    }
    with pytest.raises(ValueError, match="not allowed from input state"):
        validate_workflow_outcomes("bad-transition", definition)


def test_null_outcome_is_skipped_during_transition_check():
    # null means no state change — should not be validated against transitions
    definition = {
        "input_states": ["Bookmarked"],
        "outcomes": {
            "no_change": None,
            "accepted": "Prepared",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("null-outcome", definition)


def test_empty_input_states_skips_transition_validation():
    # Workflows like reconcile have no single input queue
    definition = {
        "input_states": [],
        "outcomes": {
            "accepted": "Bookmarked",
            "submitted": "Applied",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("no-input-states", definition)


def test_multiple_input_states_all_validated():
    # Both Applied and Interviewing must allow the transition
    definition = {
        "input_states": ["Applied", "Interviewing"],
        "outcomes": {
            "interview": "Interviewing",
            "rejected": "Rejected",
            "system_error": "Dead Letters Queue",
        },
    }
    validate_workflow_outcomes("multi-state", definition)


def test_multiple_input_states_one_invalid_raises():
    # Rejected cannot transition to Interviewing
    definition = {
        "input_states": ["Applied", "Rejected"],
        "outcomes": {
            "interview": "Interviewing",
            "system_error": "Dead Letters Queue",
        },
    }
    with pytest.raises(ValueError, match="not allowed from input state 'Rejected'"):
        validate_workflow_outcomes("one-invalid-state", definition)
