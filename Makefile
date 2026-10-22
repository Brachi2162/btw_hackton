# Local settings (NETFREE_REFRESH_CERTS, ports...) reach the recipes too.
-include .env
export

.PHONY: up down logs dev dev-local dtest test test-docker test-web lint pages barcodes vlm escalate ocr crops crop-check decide eval gt-template seed-demo clear-demo

up:        ## build and start everything: UI http://localhost:8080, API :8000/health
	docker compose up --build -d

dev: up    ## same as up: api + web in Docker (code under backend/ reloads live)
	@echo "VERA: http://localhost:$${WEB_HOST_PORT:-8080}"

down:
	docker compose down

logs:
	docker compose logs -f api web

dev-local: ## run the API locally with reload (needs: pip install -r backend/requirements-dev.txt)
	cd backend && DATA_DIR=../data uvicorn vera.api.main:app --reload --port 8000

dtest: test-docker test-web  ## all tests in Docker: backend (pytest, lint) + frontend (tsc, vitest)

test:      ## local; tests that need Tesseract are skipped (use test-docker)
	cd backend && python -m pytest -q

test-docker:  ## backend suite + lint inside the api container
	docker compose exec -T api sh -c "pip install -q -r requirements-dev.txt && black --check -q . && flake8 . && python -m pytest -q -p no:cacheprovider"

test-web:  ## frontend type check + tests, in the web build image
	docker build --target test -f frontend/Dockerfile \
		--build-arg NETFREE_REFRESH_CERTS=$${NETFREE_REFRESH_CERTS:-0} \
		--build-arg NPM_STRICT_SSL=$${NPM_STRICT_SSL:-true} .

seed-demo: ## 4 weeks of made-up history (marked demo=true, "נתוני דוגמה" in the UI)
	docker compose exec -T api python -m vera.cli seed-demo

clear-demo: ## remove everything seed-demo created
	docker compose exec -T api python -m vera.cli clear-demo

pages:     ## sample PDFs -> upright page PNGs in data/work/pages (+ manifest.json)
	docker compose exec api python -m vera.cli pages "/data/samples/*.pdf" --out /data/work/pages

barcodes:  ## barcodes on extracted pages -> data/work/pages/barcodes.json (run `make pages` first)
	docker compose exec api python -m vera.cli barcodes --pages /data/work/pages

vlm:       ## vision-model read of the dev set -> data/work/pages/vlm.json (cached; costs money once)
	docker compose exec api python -m vera.cli vlm --pages /data/work/pages --doc part2

escalate:  ## re-read pages with orange/grey fields with the stronger model (after `make decide`)
	docker compose exec api python -m vera.cli vlm --pages /data/work/pages --doc part2 --escalate

ocr:       ## Google Vision OCR of the dev set -> data/work/pages/ocr.json (D-009; needs GOOGLE_VISION_API_KEY)
	docker compose exec api python -m vera.cli ocr --pages /data/work/pages --doc part2

crops:     ## crop reads of the patient area for unconfirmed names/IDs (D-005; after `make decide`)
	docker compose exec api python -m vera.cli vlm --pages /data/work/pages --doc part2 --crops

crop-check: ## the same crops through crop_check_model, another provider (D-012; after `make crops`)
	docker compose exec api python -m vera.cli vlm --pages /data/work/pages --doc part2 --crop-check

# Samples are from Jun-Aug 2026: judge dates against 1/9/2026, not today (D-004).
REF_DATE ?= 2026-09-01

decide:    ## barcodes + VLM -> data/work/pages/submissions.json with a status per field
	docker compose exec api python -m vera.cli decide --pages /data/work/pages --ref-date $(REF_DATE)

eval: decide  ## accuracy report vs eval/ground_truth.xlsx -> eval/reports/<ts>/
	docker compose exec api python -m vera.cli eval --submissions /data/work/pages/submissions.json \
		--gt /eval/ground_truth.xlsx --reports /eval/reports --split dev --ref-date $(REF_DATE)

gt-template:  ## empty ground-truth workbook (refuses to overwrite a filled one)
	docker compose exec api python -m vera.cli gt-template "/data/samples/*.pdf" \
		--out /eval/ground_truth.xlsx --splits /eval/splits.yaml --images /data/work/pages

lint:
	cd backend && black --check . && flake8 .
