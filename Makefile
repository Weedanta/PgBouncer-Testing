# PgBouncer impact playground. Run on the Linux test host (see docs/RUNBOOK.md).
# Targets for later milestones fail loudly until they are implemented.

SHELL := /bin/bash
UV ?= uv
RUN := $(UV) run
PLAYGROUND := $(RUN) playground

define todo
	@echo "make $@: not implemented yet (milestone $(1))" >&2; exit 2
endef

.PHONY: help sync validate lint test \
        probe prep build up down init-data smoke calibrate lock \
        plan campaign resume status analyze export-thesis \
        clean-data clean-results

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

sync: ## install the locked Python environment
	$(UV) sync --locked

validate: ## check experiment.yaml against the research design
	$(PLAYGROUND) validate

lint: validate ## static checks (ruff; session-feature linter from M3)
	$(RUN) ruff check .
	$(RUN) ruff format --check .

test: ## unit tests (synthetic fixtures only)
	$(RUN) pytest

# --- M1 host + infra -------------------------------------------------------
probe: ## inspect host, propose cpusets -> config/host.env, docs/HOST.md
	$(call todo,M1)
prep: ## print (and optionally apply) sudo host tuning steps
	$(call todo,M1)
build: ## build pinned images
	$(call todo,M1)
up: ## start the stack
	$(call todo,M1)
down: ## stop the stack (keeps data)
	$(call todo,M1)

# --- M2 data ---------------------------------------------------------------
init-data: ## pgbench init + R5 seed + template
	$(call todo,M2)

# --- M7 / M8 ---------------------------------------------------------------
smoke: ## full pipeline on a tiny dataset (labeled SMOKE)
	$(call todo,M7)
calibrate: ## RM1 calibration ("pengamatan awal")
	$(call todo,M8)
lock: ## lock PgBouncer/PostgreSQL config and workloads
	$(call todo,M8)

# --- M5 runner -------------------------------------------------------------
plan: ## write results/<campaign_id>/plan.csv
	$(call todo,M5)
campaign: ## run the 400-run campaign (asks for confirmation)
	$(call todo,M5)
resume: ## continue an interrupted campaign
	$(call todo,M5)
status: ## campaign progress
	$(call todo,M5)

# --- M6 analysis -----------------------------------------------------------
analyze: ## statistics, tables, figures, REPORT.md
	$(call todo,M6)
export-thesis: ## copy thesis-ready tables and figures with Indonesian captions
	$(call todo,M6)

# --- destructive (require typing the campaign id) --------------------------
clean-data: ## drop PostgreSQL data directory
	$(call todo,M2)
clean-results: ## delete a campaign's results
	$(call todo,M5)
