PYTHON ?= python3
.PHONY: test doctor run verify docker-build

test:
	@$(PYTHON) -m pytest -q tests/

doctor:
	@$(PYTHON) -m gdtq.cli doctor

verify: doctor test
	@$(PYTHON) -m compileall -q gdtq adapters

run:
	@$(PYTHON) -m gdtq.cli run --database "$${AJSS_GDTQ_DB:-var/ajss-gdtq.sqlite3}"

docker-build:
	@docker compose build gdtq-worker
