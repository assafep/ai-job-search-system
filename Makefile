COMPOSE ?= docker compose
DEV_SERVICE ?= ajss-dev
.PHONY: setup test doctor run verify docker-build bootstrap

setup:
	@$(COMPOSE) --profile dev build $(DEV_SERVICE)

test:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m pytest -q tests/

doctor:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m ajss.cli doctor

verify: doctor test
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m compileall -q ajss adapters

run:
	@$(COMPOSE) up ajss-worker

bootstrap:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m ajss.cli bootstrap

onboard:
	@touch .env && chmod 600 .env
	@COMPOSE="$(COMPOSE)" python3 ajss/onboard/session.py $(ONBOARD_ARGS)

onboard-listen:
	@touch .env && chmod 600 .env
	@mkdir -p .onboarding && chmod 700 .onboarding
	@$(COMPOSE) --profile dev run --rm -p 127.0.0.1:8765:8765 -v "$(CURDIR)/.env:/app/.env" -v "$(CURDIR)/.onboarding:/app/.onboarding" $(DEV_SERVICE) python -m ajss.cli onboard --listen $(ONBOARD_ARGS)

onboard-stop:
	@$(COMPOSE) --profile dev stop ajss-onboard

connect:
	@$(COMPOSE) --profile dev run --rm $(DEV_SERVICE) python -m ajss.onboard.google $(BOOTSTRAP_ARGS)

docker-build:
	@$(COMPOSE) build ajss-worker
