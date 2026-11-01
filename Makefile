COMPOSE ?= docker compose
DEV_SERVICE ?= gdtq-dev
.PHONY: setup test doctor run verify docker-build

setup:
	@$(COMPOSE) --profile dev build $(DEV_SERVICE)

test:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m pytest -q tests/

doctor:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m gdtq.cli doctor

verify: doctor test
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m compileall -q gdtq adapters

run:
	@$(COMPOSE) up gdtq-worker

docker-build:
	@$(COMPOSE) build gdtq-worker
