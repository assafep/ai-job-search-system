PYTHON ?= python3

.PHONY: test verify list test-agent test-workload journal-status journal-repair dispatch-event approval-list dry-run-bookmark dry-run-validate dry-run-discovery dry-run-interview dry-run-correspondence dry-run-reconcile dry-run-prepare dry-run-submit run-workload run-worker api compose-config

test:
	@env -u GOOGLE_ACCESS_TOKEN -u GOOGLE_REFRESH_TOKEN -u GOOGLE_CLIENT_ID -u GOOGLE_CLIENT_SECRET $(PYTHON) -m pytest -q

verify:
	@env -u GOOGLE_ACCESS_TOKEN -u GOOGLE_REFRESH_TOKEN -u GOOGLE_CLIENT_ID -u GOOGLE_CLIENT_SECRET $(PYTHON) -m pytest -q
	@$(PYTHON) -m py_compile application_service/*.py services/*.py services/onboarding/*.py adapters/llm/*.py
	@$(PYTHON) -m services.test_runner list >/dev/null
	@$(MAKE) dry-run-bookmark >/dev/null
	@$(MAKE) dry-run-validate >/dev/null
	@$(MAKE) dry-run-prepare >/dev/null
	@$(MAKE) dry-run-submit >/dev/null
	@$(MAKE) dry-run-discovery >/dev/null
	@$(MAKE) dry-run-interview >/dev/null
	@$(MAKE) dry-run-correspondence >/dev/null
	@$(MAKE) dry-run-reconcile >/dev/null
	@$(MAKE) compose-config >/dev/null
	@echo "verification passed"

list:
	@$(PYTHON) -m services.test_runner list

test-agent:
	@test -n "$(AGENT)" || (echo "usage: make test-agent AGENT=interview-prep FIXTURE=tests/fixtures/agents/interview-prep/input.json [OUTPUT=...]" && exit 2)
	@test -n "$(FIXTURE)" || (echo "usage: make test-agent AGENT=... FIXTURE=... [OUTPUT=...]" && exit 2)
	@$(PYTHON) -m services.test_runner agent "$(AGENT)" --fixture "$(FIXTURE)" $(if $(OUTPUT),--output "$(OUTPUT)",)

test-workload:
	@test -n "$(WORKFLOW)" || (echo "usage: make test-workload WORKFLOW=prepare-interview FIXTURE=tests/fixtures/workloads/prepare-interview.json [AGENT_RESULTS=...]" && exit 2)
	@test -n "$(FIXTURE)" || (echo "usage: make test-workload WORKFLOW=... FIXTURE=... [AGENT_RESULTS=...]" && exit 2)
	@$(PYTHON) -m services.workload_runner "$(WORKFLOW)" --fixture "$(FIXTURE)" $(if $(AGENT_RESULTS),--agent-results "$(AGENT_RESULTS)",)

journal-status:
	@$(PYTHON) -m services.journal_cli status

journal-repair:
	@$(PYTHON) -m services.journal_cli repair --apply

dispatch-event:
	@$(PYTHON) -m services.event_dispatcher --event-file "$(EVENT_FILE)" --enqueue

approval-list:
	@$(PYTHON) -m services.approval_cli list

dry-run-bookmark:
	@$(PYTHON) -m services.workload_runner bookmark-discovered \
		--fixture tests/fixtures/workloads/bookmark.json \
		--agent-results tests/fixtures/workloads/bookmark-agent-results.json

dry-run-validate:
	@$(PYTHON) -m services.workload_runner validate-bookmarked \
		--fixture tests/fixtures/workloads/validate.json \
		--agent-results tests/fixtures/workloads/validate-agent-results.json

dry-run-prepare:
	@$(PYTHON) -m services.workload_runner prepare-validated \
		--fixture tests/fixtures/workloads/prepare.json \
		--agent-results tests/fixtures/workloads/prepare-agent-results.json

dry-run-submit:
	@$(PYTHON) -m services.workload_runner submit-prepared \
		--fixture tests/fixtures/workloads/submit.json

dry-run-interview:
	@$(PYTHON) -m services.workload_runner prepare-interview \
		--fixture tests/fixtures/workloads/prepare-interview.json \
		--agent-results tests/fixtures/workloads/prepare-interview-agent-results.json

dry-run-discovery:
	@$(PYTHON) -m services.workload_runner discover \
		--fixture tests/fixtures/workloads/discover.json

dry-run-correspondence:
	@$(PYTHON) -m services.workload_runner handle-incoming-email \
		--fixture tests/fixtures/workloads/handle-incoming-email.json \
		--agent-results tests/fixtures/workloads/handle-incoming-email-agent-results.json

dry-run-reconcile:
	@$(PYTHON) -m services.workload_runner reconcile \
		--fixture tests/fixtures/workloads/reconcile.json

run-workload:
	@$(PYTHON) -m services.workflow_runtime \
		--workload-id "$(WORKLOAD_ID)" \
		--agent-results "$(AGENT_RESULTS)"

run-worker:
	@$(PYTHON) -m services.worker_main --provider "$(AGENT_PROVIDER)" $(WORKER_ARGS)

api:
	@$(PYTHON) -m application_service.main

compose-config:
	@docker compose config
