.PHONY: up down logs dev test test-docker lint pages barcodes gt-template

up:        ## build and start everything (http://localhost:8000/health)
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

dev:       ## run the API locally with reload (needs: pip install -r backend/requirements-dev.txt)
	cd backend && DATA_DIR=../data uvicorn vera.api.main:app --reload --port 8000

test:      ## local; tests that need Tesseract are skipped (use test-docker)
	cd backend && python -m pytest -q

test-docker:  ## full suite inside the api container
	docker compose exec api sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"

pages:     ## sample PDFs -> upright page PNGs in data/work/pages (+ manifest.json)
	docker compose exec api python -m vera.cli pages "/data/samples/*.pdf" --out /data/work/pages

barcodes:  ## barcodes on extracted pages -> data/work/pages/barcodes.json (run `make pages` first)
	docker compose exec api python -m vera.cli barcodes --pages /data/work/pages

gt-template:  ## empty ground-truth workbook (refuses to overwrite a filled one)
	docker compose exec api python -m vera.cli gt-template "/data/samples/*.pdf" \
		--out /eval/ground_truth.xlsx --splits /eval/splits.yaml --images /data/work/pages

lint:
	cd backend && black --check . && flake8 .
