# PgBouncer impact playground. Run on the Linux (or WSL2 Ubuntu) test host.
# Full walkthrough: docs/PANDUAN.md (Indonesian) or docs/RUNBOOK.md (English).
#
#   make sync probe secrets render build up      # one-time setup
#   make init-data smoke                         # dataset + full pipeline test
#   make memcheck                                # DEV-019: memory limits at the highest c
#   make calibrate lock                          # RM1 (Subbab 3.3)
#   make campaign CAMPAIGN=C1                    # RM2: 150 runs, about 2.5 h
#   make analyze export-thesis CAMPAIGN=C1       # Bab 5 tables and figures

SHELL := /bin/bash
UV ?= uv
PG := $(UV) run playground
ENV_FILES := --env-file config/versions.env --env-file config/host.env --env-file secrets/secrets.env
COMPOSE := docker compose $(ENV_FILES)
EXEC := $(COMPOSE) exec client playground
EXEC_TTY := $(COMPOSE) exec -it client playground
CAMPAIGN ?=

define need_campaign
	@if [ -z "$(CAMPAIGN)" ]; then echo "set CAMPAIGN=<id>, for example: make $@ CAMPAIGN=C1" >&2; exit 2; fi
endef

.PHONY: help sync validate lint test probe secrets render prep build pin up down ps logs \
        reload init-data init-data-smoke check-metrics smoke memcheck calibrate adjust lock plan \
        campaign resume status evidence analyze export-thesis clean-data clean-results

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# --- development / checks (host) ---------------------------------------------
sync: ## install the locked Python environment (uv)
	$(UV) sync --locked

validate: ## check experiment.yaml against the research design
	$(PG) validate

lint: validate ## ruff + session-feature linter (Tabel 2.2)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run python tools/lint_session_features.py --allow-unchecked

test: ## unit tests (synthetic data only)
	$(UV) run pytest

# --- M1 host + infra -------------------------------------------------------------
probe: ## inspect host, write config/host.env (cpusets, memory) and docs/HOST.md
	$(PG) probe

secrets: ## generate passwords into secrets/ (gitignored)
	$(PG) secrets

render: ## write config/active/{postgresql.conf,pg_hba.conf,pgbouncer.ini}
	$(PG) render

prep: ## show (and with APPLY=1 apply) host tuning: governor, THP, swappiness
	bash scripts/host_prep.sh $(if $(APPLY),--apply,)

build: render ## build the pgbouncer and client images, pull the others
	$(COMPOSE) pull --ignore-buildable
	$(COMPOSE) build

pin: ## record image digests -> config/images.lock.json
	bash scripts/pin_images.sh

up: render ## start the whole stack (core waits until healthy, then monitoring)
	$(COMPOSE) up -d --wait postgres pgbouncer client
	$(COMPOSE) up -d
	$(COMPOSE) ps

down: ## stop the stack (data stays on disk)
	$(COMPOSE) down

ps: ## container status
	$(COMPOSE) ps

logs: ## follow PostgreSQL and PgBouncer logs
	$(COMPOSE) logs -f --tail=50 postgres pgbouncer

reload: render ## make PgBouncer re-read config/active/pgbouncer.ini (no restart)
	$(COMPOSE) exec -T pgbouncer sh -c 'kill -HUP 1'

# --- M2-M4 data and monitoring ---------------------------------------------------
init-data: ## pgbench -i -s 100 (experiment.yaml) + R5 seed + template
	$(EXEC) init-data

init-data-smoke: ## small smoke dataset (scale 10)
	$(EXEC) init-data --smoke

check-metrics: ## verify every Prometheus series of Tabel 3.8 (run under load)
	$(EXEC) check-metrics

# --- M7 smoke --------------------------------------------------------------------
smoke: ## full pipeline on the smoke dataset (labeled SMOKE, never for the thesis)
	$(EXEC) smoke

memcheck: ## DEV-019: memory limits under the heaviest load (R3, highest c, both paths)
	$(EXEC) memcheck

# --- M8 calibration and lock (RM1) -----------------------------------------------
calibrate: ## RM1: step A + the four checks of "pengamatan awal"
	$(EXEC) calibrate

adjust: ## one documented adjustment: make adjust PARAM=default_pool_size VALUE=8 REASON="..."
	@if [ -z "$(PARAM)" ] || [ -z "$(VALUE)" ] || [ -z "$(REASON)" ]; then \
	  echo 'usage: make adjust PARAM=... VALUE=... REASON="..."' >&2; exit 2; fi
	$(PG) adjust "$(PARAM)" "$(VALUE)" "$(REASON)"
	@echo "now: make reload && make calibrate"

lock: pin ## freeze config + workloads + versions into config/locked/, git tag
	$(EXEC) lock
	git add config/locked config/calibration_overrides.yaml 2>/dev/null || git add config/locked
	git commit -m "Lock PgBouncer configuration after calibration"
	git tag "config-locked-$$(date +%Y%m%d)"

# --- M5 campaign (RM2) -----------------------------------------------------------
plan: ## write results/$(CAMPAIGN)/plan.csv without running
	$(call need_campaign)
	$(EXEC) plan $(CAMPAIGN)

campaign: ## run the 150-run campaign, about 2.5 h (asks for confirmation)
	$(call need_campaign)
	$(EXEC_TTY) campaign $(CAMPAIGN)

resume: ## continue an interrupted campaign
	$(call need_campaign)
	$(EXEC_TTY) resume $(CAMPAIGN)

status: ## progress of a campaign
	$(call need_campaign)
	$(EXEC) status $(CAMPAIGN)

evidence: ## pooling and load-generator validation checks of a campaign
	$(call need_campaign)
	$(EXEC) evidence $(CAMPAIGN)

# --- M6 analysis (host) ------------------------------------------------------------
analyze: ## statistics, tables, figures, REPORT.md (Subbab 3.6)
	$(call need_campaign)
	$(PG) analyze $(CAMPAIGN)

export-thesis: ## thesis-ready tables/figures with Indonesian captions
	$(call need_campaign)
	$(PG) export-thesis $(CAMPAIGN)

# --- destructive (require typing the name) ---------------------------------------
clean-data: ## delete the PostgreSQL data directory (asks you to type DELETE)
	@read -p "Type DELETE to remove the PostgreSQL data directory: " ans; [ "$$ans" = "DELETE" ]
	$(COMPOSE) down
	source config/host.env && sudo rm -rf "$$PGDATA_HOST"

clean-results: ## delete one campaign's results (asks you to type its id)
	$(call need_campaign)
	@read -p "Type $(CAMPAIGN) to delete its results: " ans; [ "$$ans" = "$(CAMPAIGN)" ]
	rm -rf "results/$(CAMPAIGN)"
