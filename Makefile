PY ?= python3
export CRRO_DEMO_MODE ?= 1

.PHONY: install model test test-pg e2e serve serve-fastapi seed dashboard bench bench-all clean-demo-db

install:            ## Python deps (+ dev tools)
	$(PY) -m pip install -r requirements-dev.txt
model:              ## Train the live model (synthetic data) -> outputs/model/
	$(PY) scripts/train_model.py
test:               ## Unit + integration tests (SQLite). Fails on unexpected skips in CI.
	$(PY) -m unittest discover -s tests -t . -v
test-pg:            ## Same suite against PostgreSQL (CRRO_TEST_DATABASE_URL must be set)
	CRRO_REQUIRE_POSTGRES=1 $(PY) -m unittest discover -s tests -t . -v
dashboard:          ## Build the dashboard into crro-dashboard/dist
	cd crro-dashboard && npm install && npm run build
serve: model dashboard  ## Local demo: stdlib server + dashboard on http://127.0.0.1:8000
	$(PY) scripts/serve.py --port 8000 --dashboard crro-dashboard/dist
serve-fastapi:      ## Same app through FastAPI/uvicorn
	CRRO_DASHBOARD_DIR=crro-dashboard/dist uvicorn api:app --port 8000
seed:               ## Seed synthetic demo data into a running API (idempotent)
	$(PY) scripts/seed_demo.py --api http://127.0.0.1:8000
e2e:                ## Fresh DB + server + seed + Playwright browser tests + screenshots
	scripts/run_e2e.sh --screenshots docs/screenshots
bench:              ## Multi-seed policy benchmark (~30 min on 1 vCPU)
	$(PY) scripts/benchmarks/policy_benchmark.py
bench-all: bench    ## + optimizer scaling, webhook trials, API latency
	$(PY) scripts/benchmarks/optimizer_scaling.py
	$(PY) scripts/benchmarks/webhook_trials.py
	$(PY) scripts/benchmarks/api_latency.py
clean-demo-db:      ## DESTRUCTIVE: deletes the local SQLite demo database only
	@echo "Deleting ./crro_demo.db (local SQLite demo data). Ctrl+C within 3s to abort."; sleep 3; rm -f crro_demo.db crro_demo.db-wal crro_demo.db-shm
