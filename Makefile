.PHONY: test verify list test-agent test-workload journal-status journal-repair dispatch-event approval-list dry-run-interview dry-run-discovery dry-run-correspondence dry-run-reconcile run-workload run-worker api compose-config

test:
	@env -u GOOGLE_ACCESS_TOKEN -u GOOGLE_REFRESH_TOKEN -u GOOGLE_CLIENT_ID -u GOOGLE_CLIENT_SECRET python3 -m pytest -q

verify:
	@env -u GOOGLE_ACCESS_TOKEN -u GOOGLE_REFRESH_TOKEN -u GOOGLE_CLIENT_ID -u GOOGLE_CLIENT_SECRET python3 -m pytest -q
	@python3 -m py_compile persistence_api/*.py services/*.py services/onboarding/*.py adapters/llm/*.py
	@python3 -m services.test_runner list >/dev/null
	@$(MAKE) dry-run-interview >/dev/null
	@$(MAKE) dry-run-discovery >/dev/null
	@$(MAKE) dry-run-correspondence >/dev/null
	@$(MAKE) dry-run-reconcile >/dev/null
	@$(MAKE) compose-config >/dev/null
	@echo "verification passed"

list:
	@python3 -m services.test_runner list

test-agent:
	@test -n "$(AGENT)" || (echo "usage: make test-agent AGENT=interview-prep FIXTURE=tests/fixtures/agents/interview-prep/input.json [OUTPUT=...]" && exit 2)
	@test -n "$(FIXTURE)" || (echo "usage: make test-agent AGENT=... FIXTURE=... [OUTPUT=...]" && exit 2)
	@python3 -m services.test_runner agent "$(AGENT)" --fixture "$(FIXTURE)" $(if $(OUTPUT),--output "$(OUTPUT)",)

test-workload:
	@test -n "$(WORKFLOW)" || (echo "usage: make test-workload WORKFLOW=prepare-interview FIXTURE=tests/fixtures/workloads/prepare-interview.json [AGENT_RESULTS=...]" && exit 2)
	@test -n "$(FIXTURE)" || (echo "usage: make test-workload WORKFLOW=... FIXTURE=... [AGENT_RESULTS=...]" && exit 2)
	@python3 -m services.workload_runner "$(WORKFLOW)" --fixture "$(FIXTURE)" $(if $(AGENT_RESULTS),--agent-results "$(AGENT_RESULTS)",)

journal-status:
	@python3 -m services.journal_cli status

journal-repair:
	@python3 -m services.journal_cli repair --apply

dispatch-event:
	@python3 -m services.event_dispatcher --event-file "$(EVENT_FILE)" --enqueue

approval-list:
	@python3 -m services.approval_cli list

dry-run-interview:
	@python3 -m services.workload_runner prepare-interview \
		--fixture tests/fixtures/workloads/prepare-interview.json \
		--agent-results tests/fixtures/workloads/prepare-interview-agent-results.json

dry-run-discovery:
	@python3 -m services.workload_runner discover-and-apply \
		--fixture tests/fixtures/workloads/discover-and-apply.json \
		--agent-results tests/fixtures/workloads/discover-and-apply-agent-results.json

dry-run-correspondence:
	@python3 -m services.workload_runner handle-incoming-email \
		--fixture tests/fixtures/workloads/handle-incoming-email.json \
		--agent-results tests/fixtures/workloads/handle-incoming-email-agent-results.json

dry-run-reconcile:
	@python3 -m services.workload_runner reconcile \
		--fixture tests/fixtures/workloads/reconcile.json

run-workload:
	@python3 -m services.workflow_runtime \
		--workload-id "$(WORKLOAD_ID)" \
		--agent-results "$(AGENT_RESULTS)"

run-worker:
	@python3 -m services.worker_main --provider "$(AGENT_PROVIDER)" $(WORKER_ARGS)

api:
	@python3 -m persistence_api.main

compose-config:
	@docker compose config
